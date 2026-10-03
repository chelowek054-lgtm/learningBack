"""Предпосылки между понятиями разных областей и подтягивание предков (T-0065, R-0036, V-0088)."""

from __future__ import annotations

import pytest

from modules.knowledge import cross_links, domains
from modules.knowledge.course import build_path
from modules.knowledge.cross_links import LinkError
from modules.knowledge.mastery import MasteryState, save_state
from modules.knowledge.models import Concept, ConceptEdge
from tests.conftest import make_user

THEORY = {
    "summary": "Теория узла. " * 10,
    "sections": [
        {"heading": "Идея", "body": "Подробно.", "examples": ["а"], "counter_examples": []}
    ],
    "references": [],
}


def concept(session, domain, title, *, tier="core", blooms=("remember", "understand", "apply")):
    c = Concept(
        domain=domain,
        title=title,
        tier=tier,
        content=THEORY,
        bloom_levels=list(blooms),
        source="curated",
        status="approved",
    )
    session.add(c)
    session.flush()
    return c


@pytest.fixture
def world(session):
    """Три области: base ← mid ← top; по одному-двум понятиям в каждой."""
    for key, foundation in (("base", True), ("mid", False), ("top", False)):
        domains.register(session, key, foundation=foundation)
    domains.add_prereq(session, "mid", "base")
    domains.add_prereq(session, "top", "mid")
    w = {
        "slope": concept(session, "base", "Наклон"),
        "unused": concept(session, "base", "Не нужное никому"),
        "deriv": concept(session, "mid", "Производная"),
        "partial": concept(session, "mid", "Частная производная"),
        "descent": concept(session, "top", "Спуск"),
    }
    session.add(ConceptEdge(from_id=w["deriv"].id, to_id=w["partial"].id, type="prereq"))
    session.flush()
    return w


def link(session, w, a, b, bloom):
    return cross_links.add_link(session, w[a].id, w[b].id, bloom)


def titles(session, user, domain="top", bloom="apply"):
    return [s["title"] for s in build_path(session, user.id, domain, bloom)]


# ---- правила связи ----


def test_link_is_between_concepts_of_different_domains_and_has_a_stage(session, world):
    created = link(session, world, "partial", "descent", "apply")

    assert created.bloom == "apply" and created.from_id == world["partial"].id


def test_same_domain_link_is_refused(session, world):
    with pytest.raises(LinkError) as e:
        link(session, world, "deriv", "partial", "apply")
    assert e.value.code == "same_domain"


def test_unknown_stage_is_refused(session, world):
    with pytest.raises(LinkError) as e:
        link(session, world, "slope", "descent", "teleport")
    assert e.value.code == "unknown_bloom"


def test_link_against_the_order_of_domains_is_refused(session, world):
    with pytest.raises(LinkError) as e:
        link(session, world, "descent", "slope", "apply")  # top ниже base — нельзя
    assert e.value.code == "domain_order"


def test_domain_must_be_in_the_registry(session, world):
    other = concept(session, "unregistered", "Что-то")

    with pytest.raises(LinkError) as e:
        cross_links.add_link(session, other.id, world["descent"].id, "apply")
    assert e.value.code == "domain_unregistered"


def test_relinking_updates_the_stage_instead_of_duplicating(session, world):
    link(session, world, "slope", "descent", "apply")
    link(session, world, "slope", "descent", "understand")

    assert len(cross_links.links_into(session, world["descent"].id)) == 1
    assert cross_links.links_into(session, world["descent"].id)[0].bloom == "understand"


def test_concept_cycle_is_refused(session, world):
    link(session, world, "deriv", "descent", "apply")
    # Обратная связь невозможна и по порядку областей; цикл между понятиями ловится отдельно.
    with pytest.raises(LinkError):
        link(session, world, "descent", "deriv", "apply")


# ---- что подтягивает курс ----


def test_only_the_needed_ancestors_are_pulled_not_the_whole_base_area(session, world):
    link(session, world, "partial", "descent", "apply")
    link(session, world, "slope", "deriv", "apply")
    user = make_user(session)

    path = titles(session, user)

    assert "Наклон" in path and "Производная" in path and "Частная производная" in path
    assert "Не нужное никому" not in path  # из базовой области — только предок


def test_foundations_come_first_from_the_most_primitive_area(session, world):
    link(session, world, "partial", "descent", "apply")
    link(session, world, "slope", "deriv", "apply")
    user = make_user(session)

    path = titles(session, user)

    assert path.index("Наклон") < path.index("Производная") < path.index("Спуск")
    assert path[0] == "Наклон"


def test_lower_goal_pulls_a_shorter_chain(session, world):
    """«Понять» требует интуиции наклона, «применить» — ещё и производных."""
    link(session, world, "slope", "descent", "understand")
    link(session, world, "partial", "descent", "apply")
    link(session, world, "slope", "deriv", "understand")
    user = make_user(session)

    understand = titles(session, user, bloom="understand")
    apply_ = titles(session, user, bloom="apply")

    assert "Наклон" in understand and "Производная" not in understand
    assert {"Наклон", "Производная", "Частная производная"} <= set(apply_)


def test_known_ancestors_are_not_pulled_again(session, world):
    link(session, world, "slope", "descent", "apply")
    user = make_user(session)
    save_state(
        session,
        user.id,
        "base",
        world["slope"].id,
        MasteryState(alpha=9.0, beta=1.0, observations=9),
    )

    assert "Наклон" not in titles(session, user)


def test_step_names_its_own_domain_and_reason(session, world):
    link(session, world, "slope", "descent", "apply")
    user = make_user(session)

    first = build_path(session, user.id, "top", "apply")[0]

    assert first["domain"] == "base" and first["reason"] == "foundation"


def test_goal_without_cross_links_is_built_as_before(session, world):
    user = make_user(session)

    path = build_path(session, user.id, "top", "apply")

    assert [s["title"] for s in path] == ["Спуск"] and path[0]["domain"] == "top"


# ---- API ----


def test_curator_links_and_anyone_reads(session, client, world):
    admin = client(make_user(session, superuser=True))
    body = {"from_id": str(world["slope"].id), "to_id": str(world["descent"].id), "bloom": "apply"}

    assert admin.post("/graph/concept-links", json=body).status_code == 201

    got = client(make_user(session)).get(f"/graph/concept-links/{world['descent'].id}").json()
    assert got == [{"fromId": str(world["slope"].id), "bloom": "apply"}]


def test_regular_user_cannot_link_and_bad_link_is_422(session, client, world):
    body = {"from_id": str(world["deriv"].id), "to_id": str(world["partial"].id), "bloom": "apply"}

    assert client(make_user(session)).post("/graph/concept-links", json=body).status_code == 403
    assert (
        client(make_user(session, superuser=True))
        .post("/graph/concept-links", json=body)
        .status_code
        == 422
    )
