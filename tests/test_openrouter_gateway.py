"""Транспорт OpenRouter: живучесть на нестабильной сети и при таящем кредите.

Сетевых вызовов здесь нет — подменяется httpx-клиент.
"""

from __future__ import annotations

import json

import httpx
import pytest

from core.ai_gateway.openrouter import _ATTEMPTS, OpenRouterAIGateway

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}}


def _tool_response(payload: dict, status: int = 200) -> httpx.Response:
    body = {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "tool_calls": [
                        {"function": {"name": "t", "arguments": json.dumps(payload)}}
                    ]
                },
            }
        ]
    }
    return httpx.Response(status, json=body)


def _text_response(finish_reason: str = "length") -> httpx.Response:
    return httpx.Response(
        200,
        json={"choices": [{"finish_reason": finish_reason, "message": {"content": "просто текст"}}]},
    )


def _credit_response(affordable: int) -> httpx.Response:
    return httpx.Response(
        402,
        json={"error": {"message": f"requires more credits. You can only afford {affordable}"}},
    )


class FakeClient:
    """Отдаёт заготовленные ответы; исключения в списке — сетевые сбои."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.payloads: list[dict] = []

    def post(self, _url, json=None):  # noqa: A002
        # Копия: гейтвей мутирует payload между попытками, а нам нужны снимки.
        self.payloads.append(dict(json or {}))
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _gateway(*responses) -> tuple[OpenRouterAIGateway, FakeClient]:
    gw = OpenRouterAIGateway.__new__(OpenRouterAIGateway)  # без сетевого __init__
    fake = FakeClient(*responses)
    gw._client = fake
    return gw, fake


def test_tool_arguments_are_returned():
    gw, _ = _gateway(_tool_response({"ok": True}))

    assert gw.structured("t", "d", SCHEMA, "p") == {"ok": True}


def test_max_tokens_is_always_sent():
    """Без явного лимита OpenRouter резервирует потолок контекста и отклоняет запрос."""
    gw, fake = _gateway(_tool_response({"ok": True}))

    gw.structured("t", "d", SCHEMA, "p")

    assert fake.payloads[0]["max_tokens"] > 0


def test_network_failure_is_retried():
    """Канал до openrouter.ai рвётся через раз — одна потеря не должна ронять запрос."""
    gw, fake = _gateway(httpx.ConnectTimeout("tls"), _tool_response({"ok": True}))

    assert gw.structured("t", "d", SCHEMA, "p") == {"ok": True}
    assert len(fake.payloads) == 2


def test_budget_is_lowered_to_what_the_credit_allows():
    gw, fake = _gateway(_credit_response(5000), _tool_response({"ok": True}))

    gw.structured("t", "d", SCHEMA, "p")

    first, second = fake.payloads[0]["max_tokens"], fake.payloads[1]["max_tokens"]
    assert second < first, "бюджет должен опуститься до доступного"
    assert second <= 5000


def test_hopeless_credit_fails_immediately():
    """Если остатка не хватит даже на минимум, повторять бессмысленно."""
    gw, fake = _gateway(_credit_response(10))

    with pytest.raises(RuntimeError, match="402"):
        gw.structured("t", "d", SCHEMA, "p")
    assert len(fake.payloads) == 1


def test_missing_tool_call_is_retried_then_reported():
    gw, _ = _gateway(*[_text_response() for _ in range(_ATTEMPTS)])

    with pytest.raises(RuntimeError, match="не вызвала инструмент"):
        gw.structured("t", "d", SCHEMA, "p")


def test_error_names_the_finish_reason():
    """Диагноз должен быть в сообщении: чаще всего это обрыв по длине."""
    gw, _ = _gateway(*[_text_response("length") for _ in range(_ATTEMPTS)])

    with pytest.raises(RuntimeError, match="finish_reason=length"):
        gw.structured("t", "d", SCHEMA, "p")


def test_server_errors_are_not_retried():
    gw, fake = _gateway(httpx.Response(500, text="boom"))

    with pytest.raises(RuntimeError, match="500"):
        gw.structured("t", "d", SCHEMA, "p")
    assert len(fake.payloads) == 1


def test_broken_json_in_arguments_is_reported():
    body = {
        "choices": [
            {"message": {"tool_calls": [{"function": {"name": "t", "arguments": "{не json"}}]}}
        ]
    }
    gw, _ = _gateway(httpx.Response(200, json=body))

    with pytest.raises(RuntimeError, match="невалидный JSON"):
        gw.structured("t", "d", SCHEMA, "p")
