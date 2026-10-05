"""Мониторинг: ошибки клиента, доля упавших задач, расход токенов (T-0050)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core import monitoring
from core.config import settings
from core.models import ClientError, Job, LlmUsage
from api.routers import monitoring as monitoring_router
from tests.conftest import make_user


@pytest.fixture(autouse=True)
def _fresh_limiter(monkeypatch):
    monkeypatch.setattr(monitoring_router, "_limiter", monitoring_router.SlidingWindowLimiter())


def _jobs(session, user, done=0, failed=0, pending=0, age_hours=0):
    created = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    for status, n in (("done", done), ("failed", failed), ("pending", pending)):
        for _ in range(n):
            session.add(
                Job(
                    user_id=user.id,
                    type="grade_writing",
                    status=status,
                    input_ref={},
                    created_at=created,
                )
            )
    session.flush()


def _usage(session, user, tokens, age_hours=0):
    session.add(
        LlmUsage(
            user_id=user.id,
            purpose="p",
            model="m",
            prompt_tokens=tokens,
            completion_tokens=0,
            created_at=datetime.now(timezone.utc) - timedelta(hours=age_hours),
        )
    )
    session.flush()


# ---- доля упавших задач ----


def test_failed_job_share_alerts_above_threshold(session):
    user = make_user(session)
    _jobs(session, user, done=6, failed=4)  # 40% при пороге 20%

    jobs = monitoring.jobs_health(session)

    assert jobs["finished"] == 10 and jobs["ratio"] == 0.4 and jobs["alert"] is True


def test_healthy_job_share_does_not_alert(session):
    user = make_user(session)
    _jobs(session, user, done=19, failed=1)

    assert monitoring.jobs_health(session)["alert"] is False


def test_tiny_sample_is_not_an_alarm(session):
    user = make_user(session)
    _jobs(session, user, done=1, failed=1)  # 50%, но задач меньше минимума

    jobs = monitoring.jobs_health(session)

    assert jobs["ratio"] == 0.5 and jobs["alert"] is False


def test_pending_jobs_are_not_counted_and_old_jobs_fall_out_of_window(session):
    user = make_user(session)
    _jobs(session, user, done=5, pending=50)
    _jobs(session, user, failed=50, age_hours=settings.alert_window_hours + 5)

    jobs = monitoring.jobs_health(session)

    assert jobs["finished"] == 5 and jobs["failed"] == 0


# ---- токены ----


def test_token_alert_is_off_without_limit(session, monkeypatch):
    user = make_user(session)
    _usage(session, user, 10_000_000)
    monkeypatch.setattr(settings, "alert_tokens_per_window", 0)

    tokens = monitoring.tokens_health(session)

    assert tokens["used"] == 10_000_000 and tokens["limit"] is None and tokens["alert"] is False


def test_token_alert_fires_at_limit_and_ignores_old_usage(session, monkeypatch):
    user = make_user(session)
    monkeypatch.setattr(settings, "alert_tokens_per_window", 1000)
    _usage(session, user, 600)
    _usage(session, user, 500)
    _usage(session, user, 99_999, age_hours=settings.alert_window_hours + 1)

    tokens = monitoring.tokens_health(session)

    assert tokens["used"] == 1100 and tokens["alert"] is True


# ---- ошибки клиента ----


def test_client_error_is_stored_with_version_and_context(session, client):
    user = make_user(session)

    r = client(user).post(
        "/v1/client-errors",
        json={
            "message": "boom",
            "stack": "at x",
            "appVersion": "1.0.3",
            "fatal": True,
            "context": {"screen": "course"},
        },
    )

    assert r.status_code == 202
    e = session.query(ClientError).one()
    assert (e.user_id, e.app_version, e.fatal, e.context) == (
        user.id,
        "1.0.3",
        True,
        {"screen": "course"},
    )


def test_oversized_fields_are_clipped(session, client):
    user = make_user(session)

    client(user).post(
        "/v1/client-errors",
        json={
            "message": "m" * 5000,
            "stack": "s" * 50_000,
            "context": {f"k{i}": i for i in range(100)},
        },
    )

    e = session.query(ClientError).one()
    assert len(e.message) <= monitoring.MAX_MESSAGE + 1
    assert len(e.stack) <= monitoring.MAX_STACK + 1
    assert len(e.context) == monitoring.MAX_CONTEXT_KEYS


def test_error_flood_from_one_user_is_limited(session, client, monkeypatch):
    monkeypatch.setattr(monitoring_router, "CLIENT_ERROR_LIMIT", 3)
    api = client(make_user(session))

    codes = [api.post("/v1/client-errors", json={"message": "x"}).status_code for _ in range(5)]

    assert codes == [202, 202, 202, 429, 429]
    assert session.query(ClientError).count() == 3


def test_empty_message_is_rejected(session, client):
    assert (
        client(make_user(session)).post("/v1/client-errors", json={"message": ""}).status_code
        == 422
    )


def test_client_errors_alert_when_limit_set(session, monkeypatch):
    user = make_user(session)
    monkeypatch.setattr(settings, "alert_client_errors", 2)
    for _ in range(2):
        session.add(ClientError(user_id=user.id, message="x"))
    session.flush()

    assert monitoring.client_errors_health(session)["alert"] is True


# ---- сводка ----


def test_snapshot_is_ok_when_nothing_is_wrong(session):
    snap = monitoring.snapshot(session)

    assert snap["status"] == "ok" and snap["alerts"] == []


def test_snapshot_lists_every_firing_alert(session, monkeypatch):
    user = make_user(session)
    monkeypatch.setattr(settings, "alert_tokens_per_window", 10)
    _jobs(session, user, done=2, failed=8)
    _usage(session, user, 50)

    snap = monitoring.snapshot(session)

    assert snap["status"] == "alert" and snap["alerts"] == ["failed_jobs", "tokens"]


def test_monitoring_endpoint_is_admin_only(session, client):
    assert client(make_user(session)).get("/v1/monitoring").status_code == 403
    r = client(make_user(session, superuser=True)).get("/v1/monitoring")
    assert r.status_code == 200 and r.json()["status"] in ("ok", "alert")
