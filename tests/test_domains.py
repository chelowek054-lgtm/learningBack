"""Граф областей: реестр, связи «нужно знать до», вычисляемый уровень (T-0064, R-0035, V-0087)."""

from __future__ import annotations

import pytest

from modules.knowledge import domains
from modules.knowledge.domains import DomainError
from tests.conftest import make_user


def build(session, edges, foundations=()):
    """Граф из пар (область, предпосылка); опоры — без предпосылок."""
    names = {n for e in edges for n in e} | set(foundations)
    for n in sorted(names):
        domains.register(session, n, foundation=n in foundations)
    for d, p in edges:
        domains.add_prereq(session, domains.normalize(d), domains.normalize(p))


# ---- уровень вычисляется ----


def test_level_is_depth_in_the_graph_not_a_label(session):
    build(session, [("b", "a"), ("c", "b"), ("d", "b"), ("e", "c"), ("e", "d")], foundations=("a",))

    assert domains.levels(session) == {"a": 0, "b": 1, "c": 2, "d": 2, "e": 3}


def test_level_takes_the_longest_path_when_there_are_several(session):
    build(session, [("b", "a"), ("c", "b"), ("c", "a")], foundations=("a",))

    assert domains.levels(session)["c"] == 2  # а не 1 по короткому пути через a


def test_adding_a_prerequisite_below_raises_the_levels_above_it(session):
    build(session, [("b", "a")], foundations=("a",))
    assert domains.levels(session)["b"] == 1

    domains.register(session, "z", foundation=True)
    domains.register(session, "a2")
    domains.add_prereq(session, "a2", "z")
    domains.add_prereq(session, "b", "a2")

    assert domains.levels(session)["b"] == 2


def test_chain_lists_everything_below_from_most_primitive(session):
    build(session, [("b", "a"), ("c", "b"), ("x", "a")], foundations=("a",))

    chain = domains.chain(session, "c")

    assert [(c["key"], c["level"]) for c in chain] == [("a", 0), ("b", 1)]  # x не нужна для c


def test_chain_of_a_foundation_is_empty(session):
    build(session, [("b", "a")], foundations=("a",))

    assert domains.chain(session, "a") == []


# ---- правила графа ----


def test_cycle_is_refused_and_leaves_the_graph_intact(session):
    build(session, [("b", "a"), ("c", "b")])

    with pytest.raises(DomainError) as e:
        domains.add_prereq(session, "a", "c")

    assert e.value.code == "cycle"
    assert domains.levels(session) == {"a": 0, "b": 1, "c": 2}


def test_area_cannot_require_itself(session):
    domains.register(session, "a")

    with pytest.raises(DomainError) as e:
        domains.add_prereq(session, "a", "a")

    assert e.value.code == "self_prereq"


def test_foundation_has_no_prerequisites_so_the_graph_does_not_go_lower(session):
    domains.register(session, "base", foundation=True)
    domains.register(session, "lower")

    with pytest.raises(DomainError) as e:
        domains.add_prereq(session, "base", "lower")

    assert e.value.code == "foundation_has_no_prereqs"


def test_unknown_area_is_refused(session):
    domains.register(session, "a")

    with pytest.raises(DomainError) as e:
        domains.add_prereq(session, "a", "ghost")

    assert e.value.code == "unknown_domain"


def test_repeating_a_link_does_not_duplicate_it(session):
    build(session, [("b", "a")])

    domains.add_prereq(session, "b", "a")

    assert [r["prereqs"] for r in domains.listing(session) if r["key"] == "b"] == [["a"]]


# ---- реестр: одна область — один ключ ----


def test_same_name_in_another_spelling_is_the_same_area(session):
    first, created = domains.register(session, "Linear Algebra")
    second, created_again = domains.register(session, "  linear   algebra ")

    assert created and not created_again
    assert first.key == second.key == "linear-algebra"


def test_alias_leads_to_the_existing_area_instead_of_a_new_one(session):
    first, _ = domains.register(session, "Linear Algebra", aliases=["линал", "линейная алгебра"])

    by_alias, created = domains.register(session, "Линал")

    assert not created and by_alias.key == first.key
    assert domains.resolve(session, "линейная алгебра").key == first.key


def test_registering_with_a_known_alias_adds_the_new_names_to_the_same_area(session):
    first, _ = domains.register(session, "Linear Algebra", aliases=["линал"])

    merged, created = domains.register(session, "Матричное исчисление", aliases=["линал"])

    assert not created and merged.key == first.key
    assert domains.resolve(session, "матричное исчисление").key == first.key
    assert len(domains.listing(session)) == 1


def test_empty_title_is_refused(session):
    with pytest.raises(DomainError) as e:
        domains.register(session, "   ")
    assert e.value.code == "empty_title"


def test_resolve_of_unknown_name_is_none(session):
    assert domains.resolve(session, "ничего") is None
    assert domains.resolve(session, "") is None


# ---- API ----


def test_curator_builds_the_graph_and_anyone_reads_levels(session, client):
    admin = client(make_user(session, superuser=True))
    admin.post("/graph/domains", json={"title": "Base", "foundation": True})
    admin.post("/graph/domains", json={"title": "Mid"})
    r = admin.post("/graph/domains/mid/prereqs", json={"prereq": "BASE"})
    assert r.status_code == 201

    rows = client(make_user(session)).get("/graph/domains").json()

    assert [(x["key"], x["level"]) for x in rows] == [("base", 0), ("mid", 1)]
    chain = client(make_user(session)).get("/graph/domains/mid/chain").json()
    assert chain["level"] == 1 and [c["key"] for c in chain["chain"]] == ["base"]


def test_registering_twice_returns_the_same_key(session, client):
    admin = client(make_user(session, superuser=True))

    one = admin.post(
        "/graph/domains", json={"title": "Linear Algebra", "aliases": ["линал"]}
    ).json()
    two = admin.post("/graph/domains", json={"title": "Линал"}).json()

    assert one["created"] is True and two["created"] is False and one["key"] == two["key"]


def test_regular_user_cannot_change_the_graph(session, client):
    api = client(make_user(session))

    assert api.post("/graph/domains", json={"title": "X"}).status_code == 403
    assert api.post("/graph/domains/x/prereqs", json={"prereq": "y"}).status_code == 403


def test_api_reports_cycle_and_unknown_area(session, client):
    admin = client(make_user(session, superuser=True))
    admin.post("/graph/domains", json={"title": "A"})
    admin.post("/graph/domains", json={"title": "B"})
    admin.post("/graph/domains/b/prereqs", json={"prereq": "a"})

    assert admin.post("/graph/domains/a/prereqs", json={"prereq": "b"}).status_code == 422
    assert admin.post("/graph/domains/a/prereqs", json={"prereq": "ghost"}).status_code == 404
    assert admin.get("/graph/domains/ghost/chain").status_code == 404
