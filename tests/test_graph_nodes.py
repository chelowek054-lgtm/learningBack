"""Граф: устойчивый ключ узла при перегенерации (T-0007), облегчённый список (T-0008)."""

from __future__ import annotations

import uuid

import pytest

from modules.knowledge.models import Concept, UserConcept
from tests.conftest import make_user

THEORY = {
    "summary": "Краткое пояснение узла.",
    "sections": [{"heading": "Определение", "body": "Подробная теория узла. " * 20}],
    "references": [],
}


def _draft(*nodes):
    """Ответ модели: узлы (key, title) и одно ребро между первыми двумя."""
    out = {
        "nodes": [
            {"key": k, "title": t, "tier": "derived", "content": THEORY, "bloomLevels": []}
            for k, t in nodes
        ],
        "edges": [],
    }
    if len(nodes) >= 2:
        out["edges"].append({"from": nodes[0][0], "to": nodes[1][0], "type": "prereq"})
    return out


@pytest.fixture
def build(monkeypatch, session, client):
    """Вызов /canon/build с подставленным ответом модели; от имени администратора."""
    api = client(make_user(session, superuser=True))
    state = {}

    def run(draft, refresh=False):
        state["draft"] = draft
        monkeypatch.setattr("modules.knowledge.router.build_graph", lambda *a, **k: state["draft"])
        return api.post(
            "/graph/canon/build", json={"domain": "d", "topic": "t", "refresh": refresh}
        )

    return run


# ---- T-0007: ключ узла ----


def test_rename_in_regeneration_does_not_duplicate_node(build, session):
    build(_draft(("alpha", "Альфа"), ("beta", "Бета")))
    build(_draft(("alpha", "Альфа (переименовано)"), ("beta", "Бета")), refresh=True)

    titles = sorted(c.title for c in session.query(Concept).filter_by(domain="d"))
    assert titles == ["Альфа", "Бета"]  # заголовок куратора не затирается, дубля нет


def test_new_key_creates_new_node(build, session):
    build(_draft(("alpha", "Альфа")))
    build(_draft(("alpha", "Альфа"), ("gamma", "Гамма")))

    keys = sorted(c.key for c in session.query(Concept).filter_by(domain="d"))
    assert keys == ["alpha", "gamma"]


def test_node_without_key_is_matched_by_title_and_gets_key(build, session):
    legacy = Concept(
        domain="d", title="Альфа", tier="derived", content=THEORY, source="llm", status="draft"
    )
    session.add(legacy)
    session.flush()

    build(_draft(("alpha", "Альфа")))

    assert session.query(Concept).filter_by(domain="d").count() == 1
    session.refresh(legacy)
    assert legacy.key == "alpha"


def test_edges_follow_matched_node_after_rename(build, session):
    build(_draft(("alpha", "Альфа"), ("beta", "Бета")))
    build(_draft(("alpha", "Альфа 2"), ("beta", "Бета")), refresh=True)

    from modules.knowledge.models import ConceptEdge

    assert session.query(ConceptEdge).count() == 1  # ребро не задвоилось


def test_same_key_in_other_domain_is_independent(build, session, monkeypatch, client):
    build(_draft(("alpha", "Альфа")))
    api = client(make_user(session, superuser=True))
    monkeypatch.setattr(
        "modules.knowledge.router.build_graph", lambda *a, **k: _draft(("alpha", "Альфа"))
    )
    api.post("/graph/canon/build", json={"domain": "other", "topic": "t"})

    assert session.query(Concept).filter_by(key="alpha").count() == 2


# ---- T-0008: облегчённый список ----


def test_graph_list_has_no_full_theory(build, session, client):
    build(_draft(("alpha", "Альфа")))

    nodes = client(make_user(session)).get("/graph/d").json()["nodes"]

    assert nodes[0]["light"] is True
    assert nodes[0]["content"] == {"summary": THEORY["summary"]}


def test_node_endpoint_returns_full_theory(build, session, client):
    build(_draft(("alpha", "Альфа")))
    node_id = session.query(Concept).filter_by(domain="d").one().id

    node = client(make_user(session)).get(f"/graph/nodes/{node_id}").json()

    assert node["light"] is False
    assert node["content"]["sections"][0]["body"] == THEORY["sections"][0]["body"]
    assert node["key"] == "alpha"


def test_node_endpoint_applies_users_override(build, session, client):
    build(_draft(("alpha", "Альфа")))
    node_id = session.query(Concept).filter_by(domain="d").one().id
    user = make_user(session)
    api = client(user)
    api.post(f"/graph/nodes/{node_id}/override", json={"content": {"summary": "моё"}})

    node = api.get(f"/graph/nodes/{node_id}").json()

    assert node["content"]["summary"] == "моё"


def test_node_endpoint_returns_own_node_only_to_owner(session, client):
    owner, stranger = make_user(session), make_user(session)
    uc = UserConcept(
        user_id=owner.id, domain="d", title="Свой", content_override=THEORY, origin="grown_llm"
    )
    session.add(uc)
    session.flush()

    assert client(owner).get(f"/graph/nodes/{uc.id}").status_code == 200
    assert client(stranger).get(f"/graph/nodes/{uc.id}").status_code == 404


def test_node_endpoint_unknown_and_malformed_ids(session, client):
    api = client(make_user(session))
    assert api.get(f"/graph/nodes/{uuid.uuid4()}").status_code == 404
    assert api.get("/graph/nodes/not-a-uuid").status_code == 404


# ---- T-0006: версия персональных узлов и задания по ним ----

LONG = {
    "summary": "Личный узел про градиентный спуск. " * 5,
    "sections": [{"heading": "Идея", "body": "Идём против градиента. " * 20}],
    "references": [],
}


def _own_node(api, content=LONG, title="Мой узел"):
    r = api.post("/graph/nodes", json={"domain": "d", "title": title, "content": content})
    return r.json()["userConceptId"]


def _version(session, uc_id):
    session.expire_all()
    return session.get(UserConcept, uc_id).version


def test_personal_node_starts_at_version_one_and_edit_raises_it(session, client):
    api = client(make_user(session))
    uc_id = _own_node(api)
    assert _version(session, uc_id) == 1

    api.put(f"/graph/user-nodes/{uc_id}", json={"content": {**LONG, "summary": "новое"}})
    assert _version(session, uc_id) == 2

    api.put(f"/graph/user-nodes/{uc_id}", json={"title": "Другое имя"})
    assert _version(session, uc_id) == 3


def test_resaving_same_content_does_not_raise_version(session, client):
    api = client(make_user(session))
    uc_id = _own_node(api)
    stored = session.get(UserConcept, uc_id).content_override

    api.put(f"/graph/user-nodes/{uc_id}", json={"content": stored})
    api.put(f"/graph/user-nodes/{uc_id}", json={"mastery": {"p": 0.5}})

    assert _version(session, uc_id) == 1


def test_graph_reports_real_version_of_personal_node(session, client):
    api = client(make_user(session))
    uc_id = _own_node(api)
    api.put(f"/graph/user-nodes/{uc_id}", json={"title": "Иначе"})

    node = next(n for n in api.get("/graph/d").json()["nodes"] if n["id"] == uc_id)

    assert node["version"] == 2


def test_assessment_for_personal_node_is_generated_then_cached(session, client):
    api = client(make_user(session))
    uc_id = _own_node(api)
    url = f"/graph/nodes/{uc_id}/assessment?bloom=remember&kind=test"

    first = api.get(url)
    assert first.status_code == 200, first.text
    assert first.json()["cached"] is False
    assert first.json()["conceptVersion"] == 1

    assert api.get(url).json()["cached"] is True


def test_editing_personal_node_invalidates_its_assessments(session, client):
    from modules.knowledge.models import Assessment

    api = client(make_user(session))
    uc_id = _own_node(api)
    url = f"/graph/nodes/{uc_id}/assessment?bloom=remember&kind=test"
    api.get(url)

    api.put(f"/graph/user-nodes/{uc_id}", json={"content": {**LONG, "summary": "правка " * 30}})
    after = api.get(url).json()

    assert after["cached"] is False
    assert after["conceptVersion"] == 2
    # Строки прошлой версии вычищены, таблица не растёт на каждую правку.
    rows = session.query(Assessment).filter_by(concept_id=uuid.UUID(uc_id)).all()
    assert {r.concept_version for r in rows} == {2}


def test_assessment_of_other_users_node_is_not_found(session, client):
    owner, stranger = make_user(session), make_user(session)
    uc_id = _own_node(client(owner))

    r = client(stranger).get(f"/graph/nodes/{uc_id}/assessment?bloom=remember&kind=test")

    assert r.status_code == 404


def test_assessment_of_personal_node_without_theory_is_conflict(session, client):
    api = client(make_user(session))
    uc_id = _own_node(api, content={"summary": "коротко"})

    r = api.get(f"/graph/nodes/{uc_id}/assessment?bloom=remember&kind=test")

    assert r.status_code == 409
