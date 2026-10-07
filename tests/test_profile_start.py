"""Сборка графа в фоне после подтверждения цели: профиль и скелет одной задачей, ход виден из статуса."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from core import ai_gateway
from core.jobs import process_job
from modules.knowledge import goal_intake, profile_store, skill_profile
from modules.knowledge.models import Concept
from tests.conftest import make_user


def confirmed_goal(session, user, domain="ml"):
    goal_intake.confirm(
        session,
        user.id,
        domain,
        {"area": "Навык", "goal": "цель", "level": "apply", "knows": "ничего"},
    )


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(skill_profile, "has_llm", lambda: False)
    monkeypatch.setattr(ai_gateway, "has_llm", lambda: False)
    monkeypatch.setattr(profile_store, "has_llm", lambda: False)


def test_job_with_graph_builds_the_skeleton_and_confirms(session):
    user = make_user(session)
    confirmed_goal(session, user)
    row, job = profile_store.request(session, user.id, "ml", with_graph=True)

    process_job(session, job, None)

    assert job.status == "done" and row.status == "confirmed"
    assert job.result["graph"]["created"] > 0
    assert session.query(Concept).filter_by(domain="ml").count() > 0


def test_plain_job_leaves_the_graph_alone(session):
    user = make_user(session)
    confirmed_goal(session, user)
    row, job = profile_store.request(session, user.id, "ml")

    process_job(session, job, None)

    assert row.status == "draft" and session.query(Concept).count() == 0


def test_graph_failure_keeps_the_profile_and_says_why(session, monkeypatch):
    user = make_user(session)
    confirmed_goal(session, user)
    row, job = profile_store.request(session, user.id, "ml", with_graph=True)

    def boom(*a, **k):
        raise RuntimeError("база недоступна")

    monkeypatch.setattr(profile_store.profile_build, "build_skeleton", boom)
    process_job(session, job, None)

    assert row.status == "failed" and "граф не построился" in row.error
    assert row.profile and row.profile["areas"]  # профиль цел


def test_missing_goal_fails_the_profile_instead_of_hanging(session):
    user = make_user(session)
    confirmed_goal(session, user)
    row, job = profile_store.request(session, user.id, "ml", with_graph=True)
    session.query(goal_intake.GoalIntake).delete()
    session.flush()

    process_job(session, job, None)

    assert job.status == "failed" and row.status == "failed"


def test_stale_building_can_be_restarted_and_is_flagged(session):
    user = make_user(session)
    confirmed_goal(session, user)
    row, _ = profile_store.request(session, user.id, "ml", with_graph=True)
    assert profile_store.view(row)["stale"] is False

    row.updated_at = datetime.now(timezone.utc) - timedelta(hours=1)
    assert profile_store.view(row)["stale"] is True
    again, _ = profile_store.request(session, user.id, "ml", with_graph=True)  # не «уже строится»
    assert again.status == "building" and profile_store.view(again)["stale"] is False


class As:
    def __init__(self, client, user):
        self._c, self._u = client, user

    def get(self, url, **kw):
        return self._c(self._u).get(url, **kw)

    def post(self, url, **kw):
        return self._c(self._u).post(url, **kw)


def test_start_endpoint_answers_at_once_with_the_status(session, client):
    user = make_user(session)
    api = As(client, user)
    assert api.post("/graph/profile/ml/start").status_code == 422  # цель не подтверждена

    confirmed_goal(session, user)
    started = api.post("/graph/profile/ml/start")
    assert started.status_code == 202
    assert started.json()["status"] == "building" and started.json()["stale"] is False
    assert api.post("/graph/profile/ml/start").status_code == 409  # уже идёт
    assert session.query(Concept).count() == 0  # запрос не ждал модель
