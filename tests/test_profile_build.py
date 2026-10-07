"""Скелет графа из профиля навыка и онбординг (T-0090)."""

from __future__ import annotations

import pytest

from core import ai_gateway
from modules.knowledge import (
    cross_links,
    domains,
    goal_intake,
    profile_build,
    profile_match,
    profile_store,
    skill_profile,
)
from modules.knowledge.models import Concept, ConceptEdge, Domain
from tests.conftest import make_user

SUMMARY = (
    "Подробное описание понятия, не короче ста двадцати знаков, чтобы по нему можно было строить задания и проверку. "
    * 1
)


def concept(key, title, stage=None, level="basic", prereqs=(), optional=False):
    return {
        "key": key,
        "title": title,
        "summary": SUMMARY,
        "stage": stage,
        "level": level,
        "optional": optional,
        "prereqs": list(prereqs),
    }


def profile():
    stage = [
        {"key": "s1", "title": "Основы", "order": 1},
        {"key": "s2", "title": "Методы", "order": 2},
    ]
    return {
        "skill": "Навык",
        "level": "apply",
        "areas": [
            {
                "key": "base",
                "title": "База",
                "summary": "",
                "role": "foundation",
                "weight": 2,
                "prereqs": [],
                "stages": stage,
                "concepts": [
                    concept("a", "Азы", "s1"),
                    concept("b", "Приёмы", "s2", "middle", ["a"]),
                ],
            },
            {
                "key": "goal",
                "title": "Цель",
                "summary": "",
                "role": "goal",
                "weight": 5,
                "prereqs": ["base"],
                "stages": stage,
                "concepts": [
                    concept("x", "Икс", "s1"),
                    concept("y", "Игрек", "s2", "advanced"),  # без предпосылок на втором этапе
                    concept("z", "Зет", "s2", "middle", ["x"], optional=True),
                ],
            },
        ],
    }


def by_title(session, domain):
    return {c.title: c for c in session.query(Concept).filter_by(domain=domain).all()}


def test_skeleton_creates_domains_concepts_edges_and_links(session):
    report = profile_build.build_skeleton(session, "ml", profile())

    assert report["created"] == 5 and report["reused"] == 0
    # цель — под именем, которое выбрал человек; предпосылка — своя область
    assert {d.title for d in session.query(Domain)} >= {"ml", "База"}
    goal, base = by_title(session, "ml"), by_title(session, "База")
    assert set(goal) == {"Икс", "Игрек", "Зет"} and set(base) == {"Азы", "Приёмы"}
    # этапы, уровни и обязательность перенесены метками
    assert (goal["Игрек"].stage, goal["Игрек"].stage_order, goal["Игрек"].level) == (
        "Методы",
        2,
        "advanced",
    )
    assert goal["Зет"].optional is True and goal["Икс"].optional is False
    assert all(c.status == "draft" and c.source == "llm" for c in [*goal.values(), *base.values()])
    assert "remember" in goal["Икс"].bloom_levels and goal["Икс"].tier == "core"
    assert goal["Игрек"].tier == "derived"


def test_prereqs_follow_the_profile_and_stages_form_a_chain(session):
    profile_build.build_skeleton(session, "ml", profile())
    goal = by_title(session, "ml")
    edges = {(e.from_id, e.to_id) for e in session.query(ConceptEdge).all()}
    names = {c.id: c.title for c in session.query(Concept).all()}
    readable = {(names[a], names[b]) for a, b in edges}

    assert ("Икс", "Зет") in readable  # предпосылка из профиля
    assert ("Азы", "Приёмы") in readable
    # у «Игрек» предпосылок нет, но он на втором этапе — цепляется к последнему понятию первого
    assert ("Икс", "Игрек") in readable
    assert goal["Икс"].id not in {b for _, b in edges}  # корень остаётся корнем


def test_areas_are_ordered_and_goal_concepts_get_cross_links(session):
    report = profile_build.build_skeleton(session, "ml", profile())

    chain = [c["key"] for c in domains.chain(session, domains.normalize("ml"))]
    assert domains.normalize("База") in chain
    assert report["links"] >= 1
    link = session.query(cross_links.ConceptLink).first()
    assert (
        session.get(Concept, link.from_id).domain == "База"
        and session.get(Concept, link.to_id).domain == "ml"
    )
    assert (
        session.get(Domain, domains.normalize("База")).foundation is True
    )  # опора без предпосылок


def test_second_build_does_not_duplicate(session):
    profile_build.build_skeleton(session, "ml", profile())
    count = session.query(Concept).count()
    edges = session.query(ConceptEdge).count()

    again = profile_build.build_skeleton(session, "ml", profile())

    assert again["created"] == 0 and session.query(Concept).count() == count
    assert session.query(ConceptEdge).count() == edges


def test_existing_decisions_reuse_instead_of_duplicating(session):
    domains.register(session, "База", foundation=True)
    old = Concept(domain="База", key="old", title="Азы", tier="core", content={}, bloom_levels=[])
    session.add(old)
    session.flush()
    match = {
        "areas": [
            {
                "key": "base",
                "decision": profile_match.EXISTING,
                "domain": domains.normalize("База"),
                "concepts": [
                    {
                        "key": "a",
                        "decision": "existing",
                        "conceptId": str(old.id),
                        "similarity": 0.99,
                    },
                    {"key": "b", "decision": "new", "conceptId": None, "similarity": 0.1},
                ],
            }
        ]
    }

    report = profile_build.build_skeleton(session, "ml", profile(), match)

    assert report["reused"] == 1
    base = by_title(session, "База")
    assert set(base) == {"Азы", "Приёмы"} and base["Азы"].id == old.id  # «Азы» не продублировано
    edge = session.query(ConceptEdge).filter_by(from_id=old.id, to_id=base["Приёмы"].id).first()
    assert edge is not None  # связь с переиспользованным понятием есть


# ---- хранилище и онбординг ----


def confirmed(session, user, domain="ml"):
    goal_intake.confirm(
        session, user.id, domain, {"area": "Навык", "goal": "цель", "level": "apply"}
    )


def test_build_from_goal_makes_profile_match_and_graph(session, monkeypatch):
    monkeypatch.setattr(skill_profile, "has_llm", lambda: False)
    user = make_user(session)
    confirmed(session, user)

    report = profile_store.build_from_goal(session, user.id, "ml")

    row = profile_store.get(session, user.id, "ml")
    assert row.status == "confirmed" and row.profile["match"]["areas"]
    assert report["created"] == 6 and session.query(Concept).filter_by(domain="ml").count() == 3


def test_build_from_goal_needs_a_confirmed_goal(session):
    with pytest.raises(profile_store.ProfileError):
        profile_store.build_from_goal(session, make_user(session).id, "ml")


def test_onboarding_canon_build_uses_the_profile(session, client, monkeypatch):
    monkeypatch.setattr(skill_profile, "has_llm", lambda: False)
    monkeypatch.setattr(ai_gateway, "has_llm", lambda: False)
    user = make_user(session)
    confirmed(session, user)
    api = client(user)

    graph = api.post("/graph/canon/build", json={"domain": "ml", "topic": "Навык"}).json()

    assert len(graph["nodes"]) == 3 and all(n.get("stage") for n in graph["nodes"])


def test_onboarding_falls_back_to_the_old_builder_when_the_profile_fails(
    session, client, monkeypatch
):
    def boom(*a, **k):
        raise RuntimeError("профиль не вышел")

    monkeypatch.setattr(profile_store, "build_from_goal", boom)
    user = make_user(session)
    confirmed(session, user)
    r = client(user).post("/graph/canon/build", json={"domain": "ml", "topic": "Навык"})
    assert r.status_code == 200 and r.json()["nodes"]  # запасной путь дал карту


def test_api_build_from_a_stored_profile(session, client, monkeypatch):
    monkeypatch.setattr(skill_profile, "has_llm", lambda: False)
    user = make_user(session)
    confirmed(session, user)
    api = client(user)
    api.post("/graph/profile/ml")
    out = api.post("/graph/profile/ml/build").json()
    assert out["status"] == "confirmed" and out["build"]["created"] == 6
