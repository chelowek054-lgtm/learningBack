"""Предпросмотр объёма пути перед построением (T-0067, R-0038, V-0090)."""

from __future__ import annotations

import pytest

from modules.knowledge import cross_links, domains, path_volume
from modules.knowledge.models import Concept
from tests.conftest import make_user

THEORY = {"summary": "Теория. " * 12, "sections": [], "references": []}


def concept(session, domain, title):
    c = Concept(
        domain=domain,
        title=title,
        tier="core",
        content=THEORY,
        bloom_levels=["remember", "understand", "apply"],
        source="curated",
        status="approved",
    )
    session.add(c)
    session.flush()
    return c


@pytest.fixture
def stack(session):
    """top ← mid ← base; для «понять» нужна только опора, для «применить» ещё и середина."""
    for key, foundation in (("base", True), ("mid", False), ("top", False)):
        domains.register(session, key, foundation=foundation)
    domains.add_prereq(session, "mid", "base")
    domains.add_prereq(session, "top", "mid")
    w = {
        "b1": concept(session, "base", "Опора 1"),
        "b2": concept(session, "base", "Опора 2"),
        "m1": concept(session, "mid", "Середина"),
        "t1": concept(session, "top", "Цель"),
    }
    cross_links.add_link(session, w["m1"].id, w["t1"].id, "apply")
    cross_links.add_link(session, w["b1"].id, w["t1"].id, "understand")
    cross_links.add_link(session, w["b2"].id, w["m1"].id, "apply")
    return w


def test_full_path_counts_all_needed_areas_and_concepts(session, stack):
    full = path_volume.volume(session, "top", "apply")["variants"]["full"]

    assert full["precise"] and full["domainCount"] == 2 and full["conceptCount"] == 3
    assert {d["key"] for d in full["domains"]} == {"base", "mid"}


def test_intuitive_path_is_shorter_because_higher_stage_links_drop_out(session, stack):
    v = path_volume.volume(session, "top", "apply")

    assert v["variants"]["intuitive"]["conceptCount"] == 1  # только «Опора 1»
    assert v["variants"]["intuitive"]["conceptCount"] < v["variants"]["full"]["conceptCount"]
    assert v["differs"] is True


def test_when_the_goal_is_already_intuitive_there_is_nothing_to_choose(session, stack):
    v = path_volume.volume(session, "top", "understand")

    assert v["differs"] is False


def test_goal_not_built_yet_is_estimated_by_the_areas_below(session):
    for key, foundation in (("base", True), ("goal", False)):
        domains.register(session, key, foundation=foundation)
    domains.add_prereq(session, "goal", "base")
    concept(session, "base", "Опора")

    full = path_volume.volume(session, "goal", "apply")["variants"]["full"]

    assert full["precise"] is False and full["domainCount"] == 1 and full["conceptCount"] == 1


def test_area_without_a_graph_yet_is_reported_as_unbuilt(session):
    for key, foundation in (("base", True), ("goal", False)):
        domains.register(session, key, foundation=foundation)
    domains.add_prereq(session, "goal", "base")

    full = path_volume.volume(session, "goal", "apply")["variants"]["full"]

    assert full["unbuilt"] == ["base"]


def test_unregistered_goal_has_no_preview(session):
    v = path_volume.volume(session, "ничего", "apply")

    assert v == {"goal": "ничего", "registered": False, "variants": {}}


def test_endpoint_is_open_to_any_user_and_builds_nothing(session, client, stack):
    before = session.query(Concept).count()

    r = client(make_user(session)).get("/graph/goal/top/volume?target=apply")

    assert r.status_code == 200 and r.json()["variants"]["full"]["conceptCount"] == 3
    assert session.query(Concept).count() == before
