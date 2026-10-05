"""Учёт токенов LLM (FR-AI-05, AC-04.6)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx
from fastapi.testclient import TestClient

from core import usage
from api.app import app
from core.db import get_session
from core.models import LlmUsage
from core.security import create_access_token
from tests.conftest import make_user
from tests.test_openai_gateway import SCHEMA, _gateway, _text_response


def _tool(payload, usage_block=None):
    body = {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "tool_calls": [{"function": {"name": "t", "arguments": json.dumps(payload)}}]
                },
            }
        ]
    }
    if usage_block is not None:
        body["usage"] = usage_block
    return httpx.Response(200, json=body)


def test_gateway_records_tokens_of_each_answer(usage_rows):
    gw, _ = _gateway(_tool({"ok": True}, {"prompt_tokens": 120, "completion_tokens": 30}))
    gw.structured("submit_graph", "d", SCHEMA, "p")
    [row] = usage_rows
    assert (row.purpose, row.prompt_tokens, row.completion_tokens) == ("submit_graph", 120, 30)


def test_failed_attempt_that_was_billed_is_counted(usage_rows):
    """Ответ текстом вместо инструмента тоже стоит денег."""
    billed = _text_response()
    billed = httpx.Response(
        200, json={**billed.json(), "usage": {"prompt_tokens": 50, "completion_tokens": 5}}
    )
    gw, _ = _gateway(billed, _tool({"ok": True}, {"prompt_tokens": 60, "completion_tokens": 6}))
    gw.structured("t", "d", SCHEMA, "p")
    assert [r.prompt_tokens for r in usage_rows] == [50, 60]


def test_missing_usage_block_counts_as_zero(usage_rows):
    gw, _ = _gateway(_tool({"ok": True}))
    gw.structured("t", "d", SCHEMA, "p")
    assert (usage_rows[0].prompt_tokens, usage_rows[0].completion_tokens) == (0, 0)


def test_recording_failure_never_breaks_the_call():
    def broken():
        raise RuntimeError("db down")

    usage.record(model="m", purpose="p", usage={"prompt_tokens": 1}, session_factory=broken)


def test_user_comes_from_request_token(session, usage_rows):
    """Middleware кладёт пользователя из Bearer-токена в контекст вызова."""
    user = make_user(session)
    seen = {}

    @app.get("/_usage_probe")
    def probe():
        usage.record(model="m", purpose="probe", usage={"prompt_tokens": 7})
        seen["user"] = usage._current_user.get()
        return {}

    app.dependency_overrides[get_session] = lambda: session
    try:
        c = TestClient(app)
        c.get(
            "/_usage_probe",
            headers={"Authorization": f"Bearer {create_access_token(str(user.id))}"},
        )
        c.get("/_usage_probe")
    finally:
        app.dependency_overrides.clear()
        app.router.routes[:] = [
            r for r in app.router.routes if getattr(r, "path", "") != "/_usage_probe"
        ]
    assert [r.user_id for r in usage_rows] == [user.id, None]


def _row(session, user, purpose, p, c, when=None):
    session.add(
        LlmUsage(
            user_id=user.id if user else None,
            purpose=purpose,
            model="m",
            prompt_tokens=p,
            completion_tokens=c,
            created_at=when or datetime.now(timezone.utc),
        )
    )


def test_summary_groups_by_user_and_purpose(session):
    a, b = make_user(session), make_user(session)
    _row(session, a, "submit_grade", 100, 10)
    _row(session, a, "submit_grade", 50, 5)
    _row(session, a, "submit_graph", 200, 20)
    _row(session, b, "submit_grade", 10, 1)
    session.flush()
    rows = {(r["userId"], r["purpose"]): r for r in usage.summary(session)}
    mine = rows[(str(a.id), "submit_grade")]
    assert (mine["calls"], mine["promptTokens"], mine["completionTokens"]) == (2, 150, 15)
    assert rows[(str(b.id), "submit_grade")]["promptTokens"] == 10


def test_summary_respects_period(session):
    u = make_user(session)
    old = datetime.now(timezone.utc) - timedelta(days=10)
    _row(session, u, "x", 100, 0, old)
    _row(session, u, "x", 1, 0)
    session.flush()
    since = datetime.now(timezone.utc) - timedelta(days=1)
    [row] = [r for r in usage.summary(session, since=since) if r["userId"] == str(u.id)]
    assert row["promptTokens"] == 1


def test_summary_endpoint_is_admin_only(client, session):
    regular, admin = make_user(session), make_user(session, superuser=True)
    assert client(regular).get("/usage/summary").status_code == 403
    assert client(admin).get("/usage/summary").status_code == 200
