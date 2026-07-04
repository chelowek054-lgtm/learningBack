"""ClaudeAIGateway — реальный вызов Claude через tool-use (structured output)."""

from typing import Any

from anthropic import Anthropic

from core.ai_gateway.base import render_prompt
from core.config import settings
from core.models import Rubric


class ClaudeAIGateway:
    def __init__(self) -> None:
        self._client = Anthropic(api_key=settings.claude_api_key)

    def grade(
        self, rubric: Rubric, activity_payload: dict[str, Any], answer: Any
    ) -> dict[str, Any]:
        # JSON-schema результата — для инструмента (structured output).
        grade_schema = rubric.schema.get("grade_schema") or {
            "type": "object",
            "properties": {},
        }
        prompt = render_prompt(rubric, activity_payload, answer)

        resp = self._client.messages.create(
            model=rubric.model or settings.model_scoring,
            max_tokens=2048,
            tools=[
                {
                    "name": "submit_grade",
                    "description": "Вернуть оценку строго по рубрике.",
                    "input_schema": grade_schema,
                }
            ],
            tool_choice={"type": "tool", "name": "submit_grade"},
            messages=[{"role": "user", "content": prompt}],
        )
        for block in resp.content:
            if block.type == "tool_use":
                grade = dict(block.input)
                grade.setdefault("rubricId", rubric.id)
                grade.setdefault("rubricVersion", rubric.version)
                grade.setdefault("gradedOfflineFallback", False)
                return grade
        raise RuntimeError("Claude не вернул tool_use с оценкой")

    def generate(self, generator_id: str, params: dict[str, Any]) -> dict[str, Any]:
        # Для MVP генерация карточек — детерминированная (модульные генераторы),
        # LLM-генерация passages/вопросов — Фаза 2.
        return {"generator": generator_id, "items": []}
