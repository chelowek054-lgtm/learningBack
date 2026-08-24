"""Гейтвей к любому провайдеру с OpenAI-совместимым API.

Одна реализация на всех: провайдеры этого протокола говорят одинаково —
`POST {base_url}/chat/completions` с Bearer-ключом и tool calling. Провайдер
задаётся адресом и слагом модели в конфиге, а не отдельным классом: чтобы
подключить следующего, кода писать не нужно.

Контракт тот же (`AIGateway`), поэтому модулям всё равно, кто отвечает:
предметные схемы и промпты остаются в модулях, здесь только транспорт.

Транспорт приходится делать живучим — мешают три разные вещи, и все три лечатся
повтором, поэтому обрабатываются в одном цикле:
  * канал до провайдера рвётся примерно на каждом третьем запросе;
  * при нехватке кредита приходит 402, где НАЗВАН доступный бюджет токенов;
  * модель иногда отвечает текстом вместо вызова инструмента.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from core.ai_gateway.base import render_prompt
from core.config import settings
from core.models import Rubric

_ATTEMPTS = 3
# Провайдер в тексте 402 сообщает, на сколько токенов хватает остатка.
# У разных сервисов формулировка отличается — не совпало, значит запрос
# честно падает с 402, а не молча продолжает.
_AFFORDABLE = re.compile(r"can only afford (\d+)")
# Запас до границы бюджета и минимум, ниже которого запрос бессмыслен.
_BUDGET_MARGIN = 256
_MIN_TOKENS = 512


class OpenAICompatibleGateway:
    """Вызовы модели со structured output через tool calling."""

    def __init__(self) -> None:
        headers = {
            "Authorization": f"Bearer {settings.llm_api_key}",
            "Content-Type": "application/json",
        }
        # Необязательная атрибуция: часть провайдеров её читает, прочие игнорируют.
        if settings.llm_site_url:
            headers["HTTP-Referer"] = settings.llm_site_url
        if settings.llm_site_title:
            headers["X-Title"] = settings.llm_site_title
        self._client = httpx.Client(
            base_url=settings.llm_base_url,
            headers=headers,
            timeout=httpx.Timeout(settings.llm_timeout_seconds),
        )

    def _call_tool(
        self, model: str, tool_name: str, description: str, schema: dict[str, Any], prompt: str
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model,
            # max_tokens ОБЯЗАТЕЛЕН: без него провайдер резервирует потолок контекста
            # модели и отклоняет запрос как неоплачиваемый.
            "max_tokens": settings.llm_max_tokens,
            "messages": [{"role": "user", "content": prompt}],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "description": description,
                        "parameters": schema,
                    },
                }
            ],
            "tool_choice": {"type": "function", "function": {"name": tool_name}},
        }

        last_error = "причина неизвестна"
        for _ in range(_ATTEMPTS):
            try:
                response = self._client.post("/chat/completions", json=payload)
            except httpx.TransportError as e:
                last_error = f"сеть до провайдера недоступна ({type(e).__name__})"
                continue

            if response.status_code == 402:
                budget = self._affordable_budget(response.text)
                if budget is None:
                    raise RuntimeError(f"402 (кредита не хватает): {response.text[:220]}")
                # Баланс тает по мере расходов — подстраиваем бюджет под остаток,
                # иначе запрос отклоняется целиком, хотя денег на него хватает.
                payload["max_tokens"] = budget
                last_error = f"кредита хватило лишь на {budget} токенов"
                continue

            if response.status_code >= 400:
                raise RuntimeError(f"HTTP {response.status_code}: {response.text[:220]}")

            body = response.json()
            if "error" in body:
                raise RuntimeError(f"провайдер вернул ошибку: {str(body['error'])[:220]}")

            choice = (body.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            calls = message.get("tool_calls") or []
            if calls:
                return self._parse_arguments(model, calls[0])

            # Обычно означает обрыв по длине: ответ не поместился в max_tokens.
            last_error = (
                f"модель не вызвала инструмент {tool_name} "
                f"(finish_reason={choice.get('finish_reason')}, "
                f"max_tokens={payload['max_tokens']})"
            )

        raise RuntimeError(f"{model}: не удалось за {_ATTEMPTS} попытки — {last_error}")

    @staticmethod
    def _affordable_budget(text: str) -> int | None:
        match = _AFFORDABLE.search(text)
        if not match:
            return None
        budget = int(match.group(1)) - _BUDGET_MARGIN
        return budget if budget >= _MIN_TOKENS else None

    @staticmethod
    def _parse_arguments(model: str, call: dict[str, Any]) -> dict[str, Any]:
        arguments = call.get("function", {}).get("arguments") or "{}"
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"{model} вернул невалидный JSON в аргументах инструмента") from e
        return parsed if isinstance(parsed, dict) else {}

    def grade(
        self, rubric: Rubric, activity_payload: dict[str, Any], answer: Any
    ) -> dict[str, Any]:
        grade_schema = rubric.schema.get("grade_schema") or {"type": "object", "properties": {}}
        grade = self._call_tool(
            rubric.model or settings.llm_model_scoring,
            "submit_grade",
            "Вернуть оценку строго по рубрике.",
            grade_schema,
            render_prompt(rubric, activity_payload, answer),
        )
        grade.setdefault("rubricId", rubric.id)
        grade.setdefault("rubricVersion", rubric.version)
        grade.setdefault("gradedOfflineFallback", False)
        return grade

    def generate(self, generator_id: str, params: dict[str, Any]) -> dict[str, Any]:
        # Генерация карточек остаётся детерминированной (модульные генераторы).
        return {"generator": generator_id, "items": []}

    def structured(
        self, tool_name: str, description: str, schema: dict[str, Any], prompt: str
    ) -> dict[str, Any]:
        return self._call_tool(
            settings.llm_model_generation, tool_name, description, schema, prompt
        )
