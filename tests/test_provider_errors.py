"""Сбой провайдера модели — 502, а не 500 (качество ответов API)."""

from __future__ import annotations

from core.ai_gateway.base import ProviderError
from modules.knowledge import goal_intake
from tests.conftest import make_user


def test_provider_failure_is_a_502_with_a_retryable_message(session, client, monkeypatch):
    def down(text):
        raise ProviderError("сеть до провайдера недоступна (ConnectError)")

    monkeypatch.setattr(goal_intake, "propose_questions", down)

    r = client(make_user(session)).post("/graph/goal/clarify", json={"text": "что угодно"})

    assert r.status_code == 502
    assert "повторите" in r.json()["detail"] and "ConnectError" not in r.json()["detail"]


def test_provider_error_is_still_a_runtime_error_for_existing_handlers():
    assert issubclass(ProviderError, RuntimeError)
