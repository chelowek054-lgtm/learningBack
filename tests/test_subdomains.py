"""Граф цели из субдоменов (T-0060, R-0032, V-0084)."""

from __future__ import annotations

import pytest

from modules.knowledge import subdomains
from modules.knowledge.models import Concept, ConceptEdge
from tests.conftest import confirm_goal, make_user


def sub(key, title=None, prereqs=()):
    return {"key": key, "title": title or key.title(), "summary": "", "prereqs": list(prereqs)}


# ---- чистка разбиения ----


def test_split_drops_duplicates_unknown_and_self_prereqs():
    out = subdomains.clean_split(
        {
            "subdomains": [
                {"key": "a", "title": "A", "prereqs": ["a", "ghost"]},
                {"key": "a", "title": "A повтор"},  # тот же ключ: получает новый
                {"key": "b", "title": "a"},  # тот же заголовок без учёта регистра: отброшен
                {"key": "c", "title": "C", "prereqs": ["a", "a"]},
                "мусор",
                {"title": ""},
            ]
        }
    )

    assert [s["title"] for s in out] == ["A", "A повтор", "C"]
    assert len({s["key"] for s in out}) == 3
    assert out[0]["prereqs"] == []  # петля и неизвестный ключ убраны
    assert out[2]["prereqs"] == ["a"]  # дубль в связях схлопнут


def test_split_is_capped():
    raw = {"subdomains": [{"key": f"k{i}", "title": f"T{i}"} for i in range(20)]}

    assert len(subdomains.clean_split(raw)) == subdomains.MAX_SUBDOMAINS
    assert len(subdomains.clean_split(raw, 3)) == 3


def test_prereq_cycle_is_broken_so_order_exists():
    out = subdomains.clean_split(
        {
            "subdomains": [
                {"key": "a", "title": "A", "prereqs": ["c"]},
                {"key": "b", "title": "B", "prereqs": ["a"]},
                {"key": "c", "title": "C", "prereqs": ["b"]},
            ]
        }
    )

    edges = {(s["key"], p) for s in out for p in s["prereqs"]}
    # Остались два ребра из трёх, и цикла нет.
    assert len(edges) == 2
    by = {s["key"]: set(s["prereqs"]) for s in out}
    seen: set[str] = set()

    def reach(k, path):
        assert k not in path, "цикл"
        for p in by[k]:
            reach(p, path | {k})

    for k in by:
        reach(k, set())
        seen.add(k)


# ---- сборка ----


def graph(*keys, edges=()):
    return {
        "nodes": [{"key": k, "title": k.upper(), "tier": "derived", "content": {}} for k in keys],
        "edges": [{"from": a, "to": b, "type": "prereq"} for a, b in edges],
    }


def test_assemble_prefixes_keys_so_subdomains_can_reuse_names():
    out = subdomains.assemble([sub("x"), sub("y")], {"x": graph("n1"), "y": graph("n1")})

    assert [n["key"] for n in out["nodes"]] == ["x.n1", "y.n1"]


def test_assemble_links_tail_of_prerequisite_to_root_of_dependent():
    split = [sub("base"), sub("next", prereqs=["base"])]
    built = {
        "base": graph("a", "b", edges=[("a", "b")]),  # хвост — b
        "next": graph("c", "d", edges=[("c", "d")]),  # корень — c
    }

    out = subdomains.assemble(split, built)

    cross = [e for e in out["edges"] if e["from"].startswith("base") and e["to"].startswith("next")]
    assert cross == [{"from": "base.b", "to": "next.c", "type": "prereq"}]
    # Порядок через границу сохранён, внутренние связи на месте.
    assert {"from": "base.a", "to": "base.b", "type": "prereq"} in out["edges"]


def test_assemble_without_prereqs_adds_no_bridges():
    out = subdomains.assemble([sub("p"), sub("q")], {"p": graph("a"), "q": graph("b")})

    assert out["edges"] == []


def test_assemble_survives_a_subdomain_without_graph():
    out = subdomains.assemble([sub("p"), sub("q", prereqs=["p"])], {"p": graph("a")})

    assert [n["key"] for n in out["nodes"]] == ["p.a"] and out["edges"] == []


def test_edges_to_unknown_nodes_are_dropped():
    g = graph("a")
    g["edges"] = [{"from": "a", "to": "ghost", "type": "prereq"}]

    assert subdomains.assemble([sub("p")], {"p": g})["edges"] == []


def test_budget_counts_one_request_per_subdomain():
    assert subdomains.budget([sub("a"), sub("b"), sub("c")]) == {
        "requests": 3,
        "subdomains": 3,
        "limit": subdomains.MAX_SUBDOMAINS,
    }


# ---- API ----


def test_split_endpoint_proposes_and_writes_nothing(session, client):
    user = make_user(session)
    confirm_goal(session, user, "d")
    r = client(user).post("/graph/goal/split", json={"domain": "d", "topic": "Алгебра"})

    assert r.status_code == 200
    body = r.json()
    assert len(body["subdomains"]) == 3 and body["budget"]["requests"] == 3
    assert session.query(Concept).count() == 0


def test_build_runs_one_request_per_subdomain_and_keeps_drafts(session, client, monkeypatch):
    calls: list[str] = []
    real = subdomains.build_subdomain

    def counting(domain, goal, s):
        calls.append(s["key"])
        return real(domain, goal, s)

    monkeypatch.setattr(subdomains, "build_subdomain", counting)
    user = make_user(session)
    confirm_goal(session, user, "d")
    api = client(user)
    split = api.post("/graph/goal/split", json={"domain": "d", "topic": "Алгебра"}).json()

    r = api.post(
        "/graph/goal/build",
        json={"domain": "d", "topic": "Алгебра", "subdomains": split["subdomains"]},
    )

    assert r.status_code == 200, r.text
    assert calls == ["foundations", "core", "practice"]  # по запросу на субдомен
    assert r.json()["budget"]["requests"] == 3
    concepts = session.query(Concept).filter_by(domain="d").all()
    assert len(concepts) == 6 and {c.status for c in concepts} == {"draft"}
    # Порядок предпосылок через границы субдоменов: между группами есть рёбра.
    keys = {c.id: c.key for c in concepts}
    cross = [
        (keys[e.from_id].split(".")[0], keys[e.to_id].split(".")[0])
        for e in session.query(ConceptEdge)
        if keys[e.from_id].split(".")[0] != keys[e.to_id].split(".")[0]
    ]
    assert ("foundations", "core") in cross and ("core", "practice") in cross


def test_human_edit_of_the_split_is_what_gets_built(session, client):
    user = make_user(session)
    confirm_goal(session, user, "d")
    api = client(user)
    edited = [
        {"key": "only", "title": "Единственный субдомен", "summary": "", "prereqs": []},
    ]

    r = api.post("/graph/goal/build", json={"domain": "d", "subdomains": edited})

    assert [s["key"] for s in r.json()["subdomains"]] == ["only"]
    assert session.query(Concept).filter_by(domain="d").count() == 2


def test_build_with_cyclic_split_still_works(session, client):
    user = make_user(session)
    confirm_goal(session, user, "d")
    api = client(user)
    cyc = [
        {"key": "a", "title": "A", "prereqs": ["b"]},
        {"key": "b", "title": "B", "prereqs": ["a"]},
    ]

    assert api.post("/graph/goal/build", json={"domain": "d", "subdomains": cyc}).status_code == 200


def test_existing_domain_can_only_be_extended_by_admin(session, client):
    session.add(Concept(domain="d", title="Есть", source="curated", status="approved"))
    session.flush()
    body = {"domain": "d", "subdomains": [{"key": "a", "title": "A"}]}

    assert client(make_user(session)).post("/graph/goal/build", json=body).status_code == 403
    assert (
        client(make_user(session, superuser=True)).post("/graph/goal/build", json=body).status_code
        == 200
    )


def test_empty_split_is_rejected(session, client):
    r = client(make_user(session)).post("/graph/goal/build", json={"domain": "d", "subdomains": []})

    assert r.status_code == 422


@pytest.mark.parametrize("n", [0, 9])
def test_split_limit_is_bounded(session, client, n):
    r = client(make_user(session)).post(
        "/graph/goal/split", json={"domain": "d", "max_subdomains": n}
    )

    assert r.status_code == 422


def test_plain_canon_build_still_works_after_refactor(session, client):
    user = make_user(session)
    confirm_goal(session, user, "z")
    api = client(user)

    r = api.post("/graph/canon/build", json={"domain": "z", "topic": "t"})

    assert r.status_code == 200
    assert session.query(Concept).filter_by(domain="z").count() > 0
