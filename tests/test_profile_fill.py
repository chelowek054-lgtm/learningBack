"""Контур отдельно от наполнения: подтверждение, «уже владею», параллель, ход по областям, подхват (T-0101…T-0104)."""

from __future__ import annotations

import pytest

from core import ai_gateway
from core.jobs import process_job
from modules.knowledge import goal_intake, profile_fill, profile_store, skill_profile
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


def outline(session, user, domain="ml"):
    confirmed_goal(session, user, domain)
    row, job = profile_store.request(session, user.id, domain, phase="outline")
    process_job(session, job, None)
    return row


def test_outline_job_gives_areas_without_concepts_and_waits(session):
    user = make_user(session)
    row = outline(session, user)

    assert row.status == "outline"
    assert [a["role"] for a in row.profile["areas"]] == ["foundation", "goal"]
    assert all(a["concepts"] == [] for a in row.profile["areas"])
    assert session.query(Concept).count() == 0  # наполнение не началось без «Собрать карту»
    assert profile_store.view(row)["progress"] is None


def test_edit_keeps_it_an_outline_and_goal_cannot_be_known(session):
    user = make_user(session)
    row = outline(session, user)
    edited = dict(row.profile)
    edited["areas"] = [dict(a, known=True) for a in row.profile["areas"]]

    profile_store.save_edit(session, row, edited)

    assert row.status == "outline"
    base, goal = row.profile["areas"]
    assert base["known"] is True and goal["known"] is False  # цель «уже владею» быть не может


def test_known_area_gets_only_core_concepts():
    area = {"weight": 5, "known": False}
    assert skill_profile.concept_budget(area, "create", 5) == 48
    assert skill_profile.concept_budget({**area, "known": True}, "create", 5) == (
        skill_profile.KNOWN_AREA_CONCEPTS
    )


def test_fill_builds_everything_and_confirms(session):
    user = make_user(session)
    row = outline(session, user)
    row, job = profile_store.request_fill(session, user.id, "ml")
    assert row.status == "building"

    process_job(session, job, None)

    assert job.status == "done" and row.status == "confirmed"
    assert all(a["concepts"] for a in row.profile["areas"])
    states = profile_store.view(row)["progress"]["areas"]
    assert set(states.values()) == {"done"}
    assert session.query(Concept).filter_by(domain="ml").count() > 0


def test_fill_needs_an_outline_and_refuses_a_second_start(session):
    user = make_user(session)
    with pytest.raises(profile_store.ProfileError) as e:
        profile_store.request_fill(session, user.id, "ml")
    assert e.value.code == "not_ready"

    outline(session, user)
    profile_store.request_fill(session, user.id, "ml")
    with pytest.raises(profile_store.ProfileError) as e:
        profile_store.request_fill(session, user.id, "ml")
    assert e.value.code == "already_building"


def test_failed_area_keeps_the_rest_and_retry_only_redoes_it(session, monkeypatch):
    user = make_user(session)
    outline(session, user)
    row, job = profile_store.request_fill(session, user.id, "ml")
    real = skill_profile.propose_concepts
    calls: list[str] = []
    broken = {"goal": True}

    def flaky(skill, goal, level, area, budget):
        calls.append(area["key"])
        if area["role"] == "goal" and broken["goal"]:
            raise RuntimeError("модель недоступна")
        return real(skill, goal, level, area, budget)

    monkeypatch.setattr(skill_profile, "propose_concepts", flaky)
    process_job(session, job, None)

    # первая попытка: область-основа готова и уже лежит в графе, цель не удалась — повтор в очереди
    assert job.status == "pending" and row.status == "building"
    states = profile_store.view(row)["progress"]["areas"]
    assert sorted(states.values()) == ["done", "failed"]
    assert (
        session.query(Concept).filter(Concept.domain != "ml").count() > 0
    )  # опубликована досрочно

    calls.clear()
    broken["goal"] = False
    process_job(session, job, None)

    assert calls == ["main"]  # переделана только недостающая область
    assert job.status == "done" and row.status == "confirmed"


def test_exhausted_attempts_mark_the_profile_failed_but_keep_it(session, monkeypatch):
    user = make_user(session)
    outline(session, user)
    row, job = profile_store.request_fill(session, user.id, "ml")

    def boom(*a, **k):
        raise RuntimeError("модель недоступна")

    monkeypatch.setattr(skill_profile, "propose_concepts", boom)
    job.attempts = 2
    process_job(session, job, None)

    assert row.status == "failed" and "Не удалось составить" in row.error
    assert row.profile["areas"]  # контур цел: «Запустить заново» продолжит, а не начнёт с нуля
    again, _ = profile_store.request_fill(session, user.id, "ml")
    assert again.status == "building"


def test_interrupted_build_is_resumed_on_start(session, monkeypatch):
    user = make_user(session)
    outline(session, user)
    row, job = profile_store.request_fill(session, user.id, "ml")
    job.status = "running"  # процесс умер посреди работы
    sent: list = []
    monkeypatch.setattr(profile_store, "dispatch", lambda job_id: sent.append(job_id))

    assert profile_store.resume_interrupted(session) == 1
    assert job.status == "pending" and sent == [job.id]

    row.status = "confirmed"  # у законченной сборки подхватывать нечего
    assert profile_store.resume_interrupted(session) == 0


def test_progress_helper_marks_existing_concepts_as_done():
    profile = {"areas": [{"key": "a", "concepts": [{"key": "x"}]}, {"key": "b", "concepts": []}]}
    assert profile_fill.init_progress(profile)["areas"] == {"a": "done", "b": "waiting"}


class As:
    def __init__(self, client, user):
        self._c, self._u = client, user

    def post(self, url, **kw):
        return self._c(self._u).post(url, **kw)

    def get(self, url, **kw):
        return self._c(self._u).get(url, **kw)


def test_api_two_steps(session, client):
    user = make_user(session)
    api = As(client, user)
    assert api.post("/graph/profile/ml/fill").status_code == 422  # контура ещё нет

    outline(session, user)
    started = api.post("/graph/profile/ml/fill")
    assert started.status_code == 202 and started.json()["status"] == "building"
    assert api.post("/graph/profile/ml/fill").status_code == 409
