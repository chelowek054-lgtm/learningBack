"""Проверка уровня по цепочке областей сверху вниз (T-0066, R-0037, V-0089)."""

from __future__ import annotations

import pytest

from modules.knowledge import chain_placement, cross_links, domains
from modules.knowledge.course import build_path
from modules.knowledge.mastery import MasteryState, save_state
from modules.knowledge.models import Concept
from modules.knowledge.placement import NoProbeAvailable
from tests.conftest import make_user

THEORY = {
    "summary": "Теория узла. " * 12,
    "sections": [
        {
            "heading": "Идея",
            "body": "Подробно о том, как это устроено.",
            "examples": ["а"],
            "counter_examples": ["б"],
        }
    ],
    "references": [],
}
KNOWN_STATE = MasteryState(alpha=9.0, beta=1.0, observations=9)
FAILED_STATE = MasteryState(alpha=1.0, beta=8.0, observations=9)


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
def world(session):
    """top ← mid ← base; цель в top требует «Производную» (mid), а ей нужен «Наклон» (base)."""
    for key, foundation in (("base", True), ("mid", False), ("top", False)):
        domains.register(session, key, foundation=foundation)
    domains.add_prereq(session, "mid", "base")
    domains.add_prereq(session, "top", "mid")
    w = {
        "slope": concept(session, "base", "Наклон"),
        "deriv": concept(session, "mid", "Производная"),
        "descent": concept(session, "top", "Спуск"),
    }
    cross_links.add_link(session, w["slope"].id, w["deriv"].id, "understand")
    cross_links.add_link(session, w["deriv"].id, w["descent"].id, "understand")
    return w


def know(session, user, w, name, state=KNOWN_STATE):
    save_state(session, user.id, w[name].domain, w[name].id, state)


def states_of(session, user):
    plan = chain_placement.chain_plan(session, user.id, "top", "understand")
    return {n["title"]: n["state"] for p in plan for n in p["nodes"]}


# ---- порядок: сверху вниз ----


def test_plan_goes_from_the_hardest_area_to_the_simplest(session, world):
    user = make_user(session)

    plan = chain_placement.chain_plan(session, user.id, "top", "understand")

    assert [(p["domain"], p["level"]) for p in plan] == [("mid", 1), ("base", 0)]
    assert states_of(session, user) == {"Производная": "unknown", "Наклон": "unknown"}


def test_first_probe_is_in_the_hardest_area_not_the_simplest(session, world):
    probe = chain_placement.next_chain_probe(session, make_user(session).id, "top", "understand")

    assert probe["domain"] == "mid" and probe["conceptId"] == str(world["deriv"].id)
    assert probe["item"]["prompt"]


# ---- освоенное верхнее снимает нижнее ----


def test_known_upper_concept_removes_the_check_of_everything_below(session, world):
    user = make_user(session)
    know(session, user, world, "deriv")

    assert states_of(session, user) == {"Производная": "known", "Наклон": "implied"}
    with pytest.raises(NoProbeAvailable) as e:
        chain_placement.next_chain_probe(session, user.id, "top", "understand")
    assert e.value.code == "settled"


def test_unknown_upper_concept_sends_the_check_down(session, world):
    user = make_user(session)
    know(session, user, world, "deriv", FAILED_STATE)

    probe = chain_placement.next_chain_probe(session, user.id, "top", "understand")

    assert probe["domain"] == "base" and probe["conceptId"] == str(world["slope"].id)


def test_prior_alone_is_not_knowledge(session, world):
    """Приор от предпосылок без единого ответа не снимает проверку нижестоящих."""
    user = make_user(session)
    know(session, user, world, "deriv", MasteryState(alpha=9.0, beta=1.0, observations=0))

    assert states_of(session, user)["Производная"] == "unknown"


def test_known_lower_concept_does_not_remove_the_upper_one(session, world):
    user = make_user(session)
    know(session, user, world, "slope")

    assert states_of(session, user) == {"Производная": "unknown", "Наклон": "known"}


# ---- путь человека ----


def test_path_of_a_beginner_starts_from_the_lowest_area(session, world):
    path = build_path(session, make_user(session).id, "top", "understand")

    assert [s["title"] for s in path] == ["Наклон", "Производная", "Спуск"]


def test_path_of_an_expert_skips_what_the_upper_concept_covers(session, world):
    user = make_user(session)
    know(session, user, world, "deriv")

    path = build_path(session, user.id, "top", "understand")

    assert [s["title"] for s in path] == ["Спуск"]


def test_path_starts_from_the_lowest_unknown_area(session, world):
    user = make_user(session)
    know(session, user, world, "slope")

    path = build_path(session, user.id, "top", "understand")

    assert [s["title"] for s in path] == ["Производная", "Спуск"]


def test_goal_without_foundations_has_nothing_to_check(session):
    domains.register(session, "solo")
    concept(session, "solo", "Единственное")

    with pytest.raises(NoProbeAvailable) as e:
        chain_placement.next_chain_probe(session, make_user(session).id, "solo", "apply")
    assert e.value.code == "no_foundations"


# ---- API ----


def test_endpoints_show_the_chain_and_the_next_probe(session, client, world):
    api = client(make_user(session))

    plan = api.get("/graph/placement/top/chain?target=understand").json()
    probe = api.get("/graph/placement/top/chain-probe?target=understand").json()

    assert [p["domain"] for p in plan] == ["mid", "base"]
    assert probe["domain"] == "mid"


def test_answering_a_chain_probe_moves_the_check_down_or_finishes(session, client, world):
    api = client(make_user(session))
    probe = api.get("/graph/placement/top/chain-probe?target=understand").json()
    correct = (
        next(i for i, o in enumerate(probe["item"]["options"]) if o["correct"])
        if probe["item"]["options"]
        else probe["item"]["expected"]
    )

    for _ in range(6):
        api.post(
            "/graph/placement/answer",
            json={
                "domain": probe["domain"],
                "concept_id": probe["conceptId"],
                "bloom": probe["bloom"],
                "answer": correct,
            },
        )
        probe = api.get("/graph/placement/top/chain-probe?target=understand").json()
        if probe.get("done"):
            break

    assert probe.get("done") is True and probe["code"] == "settled"
