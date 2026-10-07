"""Профиль навыка: чистка ответа модели, размер по уровню, фоновая задача, правка (T-0088)."""

from __future__ import annotations

import pytest

from core import ai_gateway
from core.jobs import process_job
from modules.knowledge import goal_intake, profile_store, skill_profile
from modules.knowledge.models import SkillProfile
from tests.conftest import make_user


class FakeGateway:
    """Контур на три области и понятия с мусором: ответ модели нельзя принимать на веру."""

    def __init__(self):
        self.calls = []

    def structured(self, tool, desc, schema, prompt):
        self.calls.append(tool)
        if tool == "submit_outline":
            return {
                "areas": [
                    {
                        "key": "base",
                        "title": "База",
                        "role": "foundation",
                        "weight": 2,
                        "stages": [
                            {"key": "s1", "title": "Первый"},
                            {"key": "s2", "title": "Второй"},
                        ],
                    },
                    {
                        "key": "goal",
                        "title": "Цель",
                        "role": "goal",
                        "weight": 5,
                        "prereqs": ["base", "ghost"],
                        "stages": [{"key": "g1", "title": "Этап цели"}],
                    },
                ]
            }
        # понятия: дубль названия, неизвестный этап, предпосылка на себя и на несуществующее
        return {
            "concepts": [
                {
                    "key": "a",
                    "title": "Один",
                    "summary": "x" * 130,
                    "stage": "s1",
                    "level": "basic",
                },
                {
                    "key": "b",
                    "title": "Два",
                    "summary": "y" * 130,
                    "stage": "nope",
                    "level": "legend",
                    "optional": True,
                    "prereqs": ["a", "b", "zzz"],
                },
                {"key": "c", "title": "один", "summary": "дубль", "stage": "s1"},
            ]
        }


def run_with(monkeypatch, gateway, level="apply", with_llm=True):
    monkeypatch.setattr(skill_profile, "has_llm", lambda: with_llm)
    monkeypatch.setattr(skill_profile, "get_ai_gateway", lambda: gateway)
    return skill_profile.build_profile("Навык", "контекст", level)


def test_outline_is_cleaned(monkeypatch):
    profile = run_with(monkeypatch, FakeGateway())
    base, goal = profile["areas"]

    assert [a["role"] for a in profile["areas"]] == ["foundation", "goal"]
    assert goal["prereqs"] == ["base"]  # неизвестная область отброшена
    assert [s["order"] for s in base["stages"]] == [1, 2]


def test_concepts_are_cleaned(monkeypatch):
    profile = run_with(monkeypatch, FakeGateway())
    concepts = profile["areas"][0]["concepts"]

    assert [c["title"] for c in concepts] == [
        "Один",
        "Два",
    ]  # дубль по названию без учёта регистра убран
    first, second = concepts
    assert first["stage"] == "s1" and first["level"] == "basic"
    assert second["stage"] is None and second["level"] is None  # неизвестное не выдумывается
    assert second["optional"] is True and second["prereqs"] == [
        "a"
    ]  # на себя и на несуществующее нет


def test_prereq_cycle_is_broken():
    items = skill_profile.clean_concepts(
        {
            "concepts": [
                {"key": "a", "title": "A", "prereqs": ["b"]},
                {"key": "b", "title": "B", "prereqs": ["a"]},
            ]
        },
        [],
        10,
    )
    assert sum(len(c["prereqs"]) for c in items) == 1


def test_goal_area_is_always_exactly_one():
    raw = {
        "areas": [{"title": "A", "role": "goal"}, {"title": "B", "role": "goal"}, {"title": "C"}]
    }
    assert [a["role"] for a in skill_profile.clean_outline(raw)].count("goal") == 1
    none = skill_profile.clean_outline(
        {"areas": [{"key": "a", "title": "A"}, {"key": "b", "title": "B", "prereqs": ["a"]}]}
    )
    assert [a["title"] for a in none if a["role"] == "goal"] == ["B"]  # от неё никто не зависит


def test_limits_apply():
    many = {"areas": [{"title": f"Область {i}", "role": "foundation"} for i in range(20)]}
    assert len(skill_profile.clean_outline(many)) == skill_profile.MAX_AREAS
    stages_raw = {
        "areas": [{"title": "A", "stages": [{"key": f"s{i}", "title": f"Э{i}"} for i in range(30)]}]
    }
    assert len(skill_profile.clean_outline(stages_raw)[0]["stages"]) == skill_profile.MAX_STAGES
    capped = skill_profile.clean_concepts(
        {"concepts": [{"title": f"П{i}"} for i in range(50)]}, [], 7
    )
    assert len(capped) == 7


def test_size_grows_with_the_level():
    sizes = [
        skill_profile.target_size(lvl) for lvl in ("remember", "understand", "apply", "create")
    ]
    assert sizes == sorted(sizes) and sizes[0] < sizes[-1]
    assert skill_profile.target_size("nonsense") == skill_profile.SIZE_BY_LEVEL["apply"]
    # самая большая область получает полный размер, малая — меньше, но не меньше минимума
    assert skill_profile.area_budget("create", 5, 5) == 48
    assert skill_profile.MIN_AREA_CONCEPTS <= skill_profile.area_budget("remember", 1, 5) < 12


def test_budget_is_requested_per_area(monkeypatch):
    seen = []

    class Spy(FakeGateway):
        def structured(self, tool, desc, schema, prompt):
            seen.append(prompt)
            return super().structured(tool, desc, schema, prompt)

    run_with(monkeypatch, Spy(), level="create")
    concept_prompts = [p for p in seen if "не больше" in p and "понятия" in p.lower()]
    assert any("не больше 48" in p for p in concept_prompts)  # у области цели вес 5 → полный размер


def test_without_a_model_the_fixture_is_generic_and_valid(monkeypatch):
    profile = run_with(monkeypatch, None, with_llm=False)
    assert [a["role"] for a in profile["areas"]] == ["foundation", "goal"]
    goal = profile["areas"][1]
    assert goal["title"] == "Навык" and goal["prereqs"] == ["foundations"]
    assert len(goal["concepts"]) == 3 and all(len(c["summary"]) >= 120 for c in goal["concepts"])


def test_clean_profile_applies_the_same_rules_to_human_edits(monkeypatch):
    profile = run_with(monkeypatch, FakeGateway())
    before = skill_profile.concept_count(profile)
    profile["areas"][0]["concepts"].append(
        {"key": "a", "title": "Лишнее", "stage": "xx", "level": "?"}
    )
    profile["areas"][1]["prereqs"].append("base")  # повтор
    cleaned = skill_profile.clean_profile(profile)
    assert cleaned["areas"][1]["prereqs"] == ["base"]
    assert all(c["stage"] in (None, "s1", "s2") for c in cleaned["areas"][0]["concepts"])
    assert skill_profile.concept_count(cleaned) == before + 1


# ---- хранение и задача ----


def confirmed_goal(session, user, domain="ml"):
    goal_intake.confirm(
        session,
        user.id,
        domain,
        {"area": "Навык", "goal": "цель", "level": "apply", "knows": "ничего"},
    )


def test_request_needs_a_confirmed_goal(session):
    with pytest.raises(profile_store.ProfileError) as e:
        profile_store.request(session, make_user(session).id, "ml")
    assert e.value.code == "goal_not_confirmed"


def test_job_builds_the_profile_and_marks_it_draft(session, monkeypatch):
    monkeypatch.setattr(skill_profile, "has_llm", lambda: False)
    user = make_user(session)
    confirmed_goal(session, user)
    row, job = profile_store.request(session, user.id, "ml")
    assert row.status == "building"

    process_job(session, job, None)

    assert job.status == "done" and row.status == "draft"
    assert row.profile["skill"] == "Навык" and row.profile["areas"]


def test_second_request_while_building_is_refused(session):
    user = make_user(session)
    confirmed_goal(session, user)
    profile_store.request(session, user.id, "ml")
    with pytest.raises(profile_store.ProfileError) as e:
        profile_store.request(session, user.id, "ml")
    assert e.value.code == "already_building"


def test_model_failure_marks_the_profile_failed_after_the_last_attempt(session, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("модель недоступна")

    monkeypatch.setattr(skill_profile, "build_profile", boom)
    user = make_user(session)
    confirmed_goal(session, user)
    row, job = profile_store.request(session, user.id, "ml")

    process_job(session, job, None)
    assert job.status == "pending" and row.status == "building"  # временный сбой — повтор
    job.attempts = 2
    process_job(session, job, None)  # третья попытка — последняя
    assert row.status == "failed" and "недоступна" in row.error


def test_edit_resets_confirmation_and_rejects_an_empty_profile(session, monkeypatch):
    monkeypatch.setattr(skill_profile, "has_llm", lambda: False)
    user = make_user(session)
    confirmed_goal(session, user)
    row, job = profile_store.request(session, user.id, "ml")
    process_job(session, job, None)
    profile_store.confirm(session, row)
    assert row.status == "confirmed" and row.confirmed_at

    edited = dict(row.profile)
    edited["areas"] = [dict(a) for a in row.profile["areas"]]
    edited["areas"][1]["concepts"] = edited["areas"][1]["concepts"][:1]
    profile_store.save_edit(session, row, edited)
    assert row.status == "draft" and row.confirmed_at is None
    assert len(row.profile["areas"][1]["concepts"]) == 1

    with pytest.raises(profile_store.ProfileError) as e:
        profile_store.save_edit(session, row, {"areas": []})
    assert e.value.code == "empty"


# ---- API ----


class As:
    def __init__(self, client, user):
        self._c, self._u = client, user

    def get(self, url, **kw):
        return self._c(self._u).get(url, **kw)

    def post(self, url, **kw):
        return self._c(self._u).post(url, **kw)

    def put(self, url, **kw):
        return self._c(self._u).put(url, **kw)


def test_api_flow(session, client, monkeypatch):
    monkeypatch.setattr(skill_profile, "has_llm", lambda: False)
    monkeypatch.setattr(ai_gateway, "has_llm", lambda: False)
    user = make_user(session)
    api = As(client, user)

    assert api.get("/graph/profile/ml").json() == {"exists": False, "status": None, "profile": None}
    assert api.post("/graph/profile/ml").status_code == 422  # цель не подтверждена

    confirmed_goal(session, user)
    built = api.post("/graph/profile/ml")
    assert built.status_code == 202 and built.json()["status"] == "draft"
    assert built.json()["concepts"] == 6

    body = api.get("/graph/profile/ml").json()["profile"]
    body["areas"][0]["concepts"] = []
    assert api.put("/graph/profile/ml", json=body).json()["concepts"] == 3
    assert api.post("/graph/profile/ml/confirm").json()["status"] == "confirmed"
    assert session.query(SkillProfile).count() == 1


def test_other_user_sees_nothing(session, client, monkeypatch):
    monkeypatch.setattr(skill_profile, "has_llm", lambda: False)
    owner, other = make_user(session), make_user(session)
    confirmed_goal(session, owner)
    As(client, owner).post("/graph/profile/ml")
    assert As(client, other).get("/graph/profile/ml").json()["exists"] is False
