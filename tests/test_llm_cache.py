"""Кэш детерминированных ответов LLM (FR-AI-06, AC-04.7)."""

from __future__ import annotations

import contextlib

from core import llm_cache
from core.ai_base import GRADE_JSON_SCHEMA
from core.models import LlmCache, Rubric
from tests.test_openai_gateway import SCHEMA, _gateway
from tests.test_usage import _tool


def _factory(session):
    """Фабрика сессий поверх тестовой транзакции (без закрытия сессии)."""
    return lambda: contextlib.nullcontext(session)


def test_key_depends_on_model_tool_schema_and_prompt():
    base = llm_cache.make_key("m", "t", SCHEMA, "p")
    assert base == llm_cache.make_key("m", "t", SCHEMA, "p")
    assert (
        len(
            {
                base,
                llm_cache.make_key("m2", "t", SCHEMA, "p"),
                llm_cache.make_key("m", "t2", SCHEMA, "p"),
                llm_cache.make_key("m", "t", {"type": "object"}, "p"),
                llm_cache.make_key("m", "t", SCHEMA, "p2"),
            }
        )
        == 5
    )


def test_put_then_get_roundtrip(session):
    f = _factory(session)
    assert llm_cache.get("k" * 64, f) is None
    llm_cache.put("k" * 64, {"score": 1}, f)
    assert llm_cache.get("k" * 64, f) == {"score": 1}
    # Повторная запись того же ключа не падает и не меняет значение.
    llm_cache.put("k" * 64, {"score": 0}, f)
    assert llm_cache.get("k" * 64, f) == {"score": 1}
    assert session.query(LlmCache).count() == 1


def test_cache_failure_is_a_miss_not_an_error():
    def broken():
        raise RuntimeError("db down")

    assert llm_cache.get("x", broken) is None
    llm_cache.put("x", {}, broken)  # не бросает


def test_gateway_uses_cache_when_allowed(monkeypatch):
    store: dict = {}
    monkeypatch.setattr(llm_cache, "get", lambda key, *_: store.get(key))
    monkeypatch.setattr(llm_cache, "put", lambda key, payload, *_: store.__setitem__(key, payload))
    gw, fake = _gateway(_tool({"ok": True}), _tool({"ok": False}))

    first = gw.structured("t", "d", SCHEMA, "p", cache=True)
    second = gw.structured("t", "d", SCHEMA, "p", cache=True)

    assert first == second == {"ok": True}
    assert len(fake.payloads) == 1, "второй вызов должен прийти из кэша, без запроса"


def test_gateway_does_not_cache_by_default(monkeypatch):
    store: dict = {}
    monkeypatch.setattr(llm_cache, "get", lambda key, *_: store.get(key))
    monkeypatch.setattr(llm_cache, "put", lambda key, payload, *_: store.__setitem__(key, payload))
    gw, fake = _gateway(_tool({"n": 1}), _tool({"n": 2}))

    assert gw.structured("t", "d", SCHEMA, "p") == {"n": 1}
    assert gw.structured("t", "d", SCHEMA, "p") == {"n": 2}  # «перестроить» даёт новый результат
    assert store == {} and len(fake.payloads) == 2


def test_grade_is_cached_per_answer(monkeypatch):
    store: dict = {}
    monkeypatch.setattr(llm_cache, "get", lambda key, *_: store.get(key))
    monkeypatch.setattr(llm_cache, "put", lambda key, payload, *_: store.__setitem__(key, payload))
    rubric = Rubric(
        id="r",
        version=1,
        module="m",
        model="",
        prompt="оцени",
        schema={"grade_schema": GRADE_JSON_SCHEMA},
    )
    grade = {"criteria": [], "overall": 5, "errors": []}
    gw, fake = _gateway(_tool(grade), _tool({**grade, "overall": 9}))

    a1 = gw.grade(rubric, {"prompt": "p"}, "эссе один")
    a2 = gw.grade(rubric, {"prompt": "p"}, "эссе один")
    b = gw.grade(rubric, {"prompt": "p"}, "другой текст")

    assert a1["overall"] == a2["overall"] == 5
    assert b["overall"] == 9
    assert len(fake.payloads) == 2
