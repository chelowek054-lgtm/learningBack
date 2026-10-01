"""Промоция персональных узлов в канон (T-0001, AC-13.5)."""

from __future__ import annotations

import uuid

from modules.knowledge.models import Concept, ConceptEdge, UserConcept, UserEdge
from tests.conftest import make_user

THEORY = {
    "summary": "Теория личного узла.",
    "sections": [{"heading": "Идея", "body": "Подробно. " * 20}],
    "references": [],
}


def _canon(session, title="База", domain="d"):
    c = Concept(
        domain=domain,
        title=title,
        tier="core",
        content={"summary": "канон"},
        source="curated",
        status="approved",
    )
    session.add(c)
    session.flush()
    return c


def _own(session, user, title="Свой", domain="d", content=THEORY):
    uc = UserConcept(
        user_id=user.id,
        domain=domain,
        title=title,
        content_override=content,
        origin="grown_llm",
        status="learning",
        mastery={"alpha": 3.0, "beta": 1.0},
    )
    session.add(uc)
    session.flush()
    return uc


def _override(session, user, base):
    uc = UserConcept(
        user_id=user.id,
        domain="d",
        base_concept_id=base.id,
        content_override=THEORY,
        origin="edited",
    )
    session.add(uc)
    session.flush()
    return uc


def _admin(session, client):
    return client(make_user(session, superuser=True))


def test_promoting_own_node_creates_canonical_and_clears_override(session, client):
    author = make_user(session)
    uc = _own(session, author)

    r = _admin(session, client).post("/graph/canon/promote", json={"user_concept_id": str(uc.id)})

    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "own" and r.json()["version"] == 1
    concept = session.get(Concept, r.json()["id"])
    assert concept.title == "Свой" and concept.status == "approved"
    assert concept.source == "promoted"
    session.refresh(uc)
    # Оверрайда нет, прогресс автора сохранён.
    assert uc.base_concept_id == concept.id
    assert uc.content_override is None and uc.title is None
    assert uc.mastery == {"alpha": 3.0, "beta": 1.0}


def test_other_users_see_the_new_canonical_node(session, client):
    author, other = make_user(session), make_user(session)
    uc = _own(session, author)
    _admin(session, client).post("/graph/canon/promote", json={"user_concept_id": str(uc.id)})

    assert [n["title"] for n in client(other).get("/graph/d").json()["nodes"]] == ["Свой"]
    # У автора узел не задвоился: канон + его запись поверх, а не ещё и личный.
    assert [n["title"] for n in client(author).get("/graph/d").json()["nodes"]] == ["Свой"]


def test_promoting_override_updates_canon_and_bumps_version(session, client):
    base = _canon(session)
    uc = _override(session, make_user(session), base)

    r = _admin(session, client).post("/graph/canon/promote", json={"user_concept_id": str(uc.id)})

    assert r.json() == {"id": str(base.id), "version": 2, "kind": "override"}
    session.refresh(base)
    assert base.content["summary"] == THEORY["summary"]
    session.refresh(uc)
    assert uc.content_override is None and uc.origin == "inherited"


def test_author_edges_between_canonical_nodes_become_canonical(session, client):
    base = _canon(session)
    author = make_user(session)
    uc = _own(session, author)
    session.add(
        UserEdge(user_id=author.id, domain="d", from_id=base.id, to_id=uc.id, type="prereq")
    )
    session.flush()

    _admin(session, client).post("/graph/canon/promote", json={"user_concept_id": str(uc.id)})

    new = session.query(Concept).filter_by(title="Свой").one()
    assert session.query(ConceptEdge).filter_by(from_id=base.id, to_id=new.id).count() == 1
    assert session.query(UserEdge).filter_by(user_id=author.id).count() == 0


def test_promotion_is_admin_only(session, client):
    uc = _own(session, make_user(session))
    user_api = client(make_user(session))

    body = {"user_concept_id": str(uc.id)}
    assert user_api.post("/graph/canon/promote", json=body).status_code == 403
    assert user_api.get("/graph/canon/promotion-candidates?domain=d").status_code == 403


def test_title_clash_and_missing_theory_are_conflicts(session, client):
    _canon(session, title="Занято")
    author = make_user(session)
    clash = _own(session, author, title="Занято")
    empty = UserConcept(user_id=author.id, domain="d", title="Пусто", origin="grown_llm")
    session.add(empty)
    session.flush()
    admin = _admin(session, client)

    r1 = admin.post("/graph/canon/promote", json={"user_concept_id": str(clash.id)})
    r2 = admin.post("/graph/canon/promote", json={"user_concept_id": str(empty.id)})
    assert r1.status_code == 409 and r2.status_code == 409


def test_unknown_node_is_404(session, client):
    r = _admin(session, client).post(
        "/graph/canon/promote", json={"user_concept_id": str(uuid.uuid4())}
    )
    assert r.status_code == 404


def test_candidates_rank_by_number_of_users(session, client):
    base = _canon(session, title="Правимый")
    a, b, c = (make_user(session) for _ in range(3))
    _own(session, a, title="Популярный")
    _own(session, b, title="популярный")  # то же название, другой регистр
    _own(session, c, title="Редкий")
    _override(session, a, base)
    _override(session, b, base)
    admin = _admin(session, client)

    rows = admin.get("/graph/canon/promotion-candidates?domain=d").json()

    # При равенстве числа людей порядок — по названию.
    assert [(r["kind"], r["title"].lower(), r["users"]) for r in rows[:2]] == [
        ("own", "популярный", 2),
        ("override", "правимый", 2),
    ]
    assert rows[-1]["title"] == "Редкий" and rows[-1]["users"] == 1
    assert len(admin.get("/graph/canon/promotion-candidates?domain=d&min_users=2").json()) == 2
