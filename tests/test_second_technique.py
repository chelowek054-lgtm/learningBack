"""Вторая техника запоминания и смена способа на лету (T-0063, T-0062, V-0086)."""

from __future__ import annotations

import pytest

from core import modules
from core.evidence import Evidence
from core.methods import REMEMBER, check_methods
from core.models import Activity
from modules.knowledge import api as kg
from modules.knowledge.course import generate_course
from modules.knowledge.models import Concept, Course
from modules.knowledge.study import start_step
from modules.mnemonic import ACTIVITY_TYPE, mask_text
from tests.conftest import make_user

SUMMARY = (
    "Градиентный спуск уменьшает ошибку шагами против градиента. "
    "Размер шага задаёт скорость обучения. Слишком большой шаг расходится."
)
THEORY = {
    "summary": SUMMARY,
    "sections": [
        {"heading": "Идея", "body": "Подробно.", "examples": ["а"], "counter_examples": ["б"]}
    ],
    "references": [],
}


@pytest.fixture(autouse=True)
def _clean_state():
    modules.reset_state_cache()
    yield
    modules.reset_state_cache()


def node(session, title="Градиентный спуск", domain="d"):
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


def chain(session, user, domain="d"):
    course = session.query(Course).filter_by(user_id=user.id, domain=domain).one()
    return [a["type"] for a in course.path[0]["activities"]]


# ---- сама техника ----


def test_mask_keeps_first_letters_and_punctuation():
    assert mask_text("Шаг задаёт скорость, да.") == "Ш__ з_____ с_______, да."


def test_second_technique_is_a_method_of_the_same_step_as_repetition(session):
    modules.sync_module_state(session)

    by_id = {m.id: m for m in modules.study_methods()}

    assert by_id["first_letters"].purpose == REMEMBER == by_id["srs"].purpose
    assert by_id["first_letters"].module == "mnemonic" and by_id["first_letters"].offline
    check_methods(list(by_id.values()))  # те же правила контракта, что у остальных


def test_default_course_keeps_repetition_so_behavior_does_not_change(session):
    modules.sync_module_state(session)
    user = make_user(session)
    node(session)

    generate_course(session, user.id, "d", "apply")

    assert chain(session, user)[-1] == "srs"


def test_payload_is_built_by_the_module_not_by_the_graph(session):
    modules.sync_module_state(session)
    n = node(session)

    payload = modules.payload_for(ACTIVITY_TYPE, {"conceptId": str(n.id), "content": THEORY})

    assert payload["answer"].startswith("Градиентный спуск уменьшает")
    assert payload["cue"].startswith("Г__________ с____ у________")
    assert payload["conceptId"] == str(n.id) and "content" not in payload
    assert modules.payload_for("no_such_type", {"content": THEORY}) is None


def test_node_without_summary_gives_no_activity(session):
    modules.sync_module_state(session)

    assert modules.payload_for(ACTIVITY_TYPE, {"content": {"summary": " "}}) is None


# ---- выбор и смена способа ----


def test_person_can_choose_the_second_technique(session, client):
    modules.sync_module_state(session)
    user = make_user(session)
    node(session)
    generate_course(session, user.id, "d", "apply")

    r = client(user).put(
        "/v1/me/study-methods", json={"purpose": "remember", "method": "first_letters"}
    )

    assert r.status_code == 200
    assert r.json()["preferred"] == {"remember": "first_letters"}
    assert {o["id"] for o in r.json()["options"]} >= {"srs", "first_letters"}
    assert chain(session, user)[-1] == ACTIVITY_TYPE


def test_switching_keeps_mastery_progress_and_cards(session, client):
    """V-0086: освоенность и прогресс принадлежат человеку, а не способу."""
    modules.sync_module_state(session)
    user = make_user(session)
    n = node(session)
    kg.record_evidence(session, user.id, "d", Evidence(n.id, "understand", 0.9, "t"))
    course = generate_course(session, user.id, "d", "apply")
    course.progress = {"completed": [str(n.id)]}
    session.flush()
    before = kg.get_mastery(session, user.id, "d")

    c = client(user)
    c.put("/v1/me/study-methods", json={"purpose": "remember", "method": "first_letters"})
    c.put("/v1/me/study-methods", json={"purpose": "remember", "method": "srs"})

    assert kg.get_mastery(session, user.id, "d") == before
    assert session.query(Course).filter_by(user_id=user.id).one().progress == {
        "completed": [str(n.id)]
    }
    assert chain(session, user)[-1] == "srs"


def test_resetting_the_choice_returns_the_default(session, client):
    modules.sync_module_state(session)
    user = make_user(session)
    node(session)
    generate_course(session, user.id, "d", "apply")
    c = client(user)
    c.put("/v1/me/study-methods", json={"purpose": "remember", "method": "first_letters"})

    r = c.put("/v1/me/study-methods", json={"purpose": "remember", "method": None})

    assert r.json()["preferred"] == {}
    assert chain(session, user)[-1] == "srs"


@pytest.mark.parametrize(
    "body",
    [
        {"purpose": "teleport", "method": "srs"},
        {"purpose": "remember", "method": "no_such"},
        {"purpose": "read", "method": "first_letters"},  # способ другого шага
    ],
)
def test_wrong_choice_is_rejected(session, client, body):
    modules.sync_module_state(session)

    r = client(make_user(session)).put("/v1/me/study-methods", json=body)

    assert r.status_code == 422


def test_disabled_module_falls_back_to_the_default_method(session, client):
    modules.sync_module_state(session)
    user = make_user(session)
    node(session)
    generate_course(session, user.id, "d", "apply")
    client(user).put(
        "/v1/me/study-methods", json={"purpose": "remember", "method": "first_letters"}
    )

    modules.set_enabled(session, "mnemonic", False)
    generate_course(session, user.id, "d", "apply")

    assert chain(session, user)[-1] == "srs"


def test_interleaved_retention_follows_the_chosen_technique(session, client):
    modules.sync_module_state(session)
    user = make_user(session)
    for i in range(4):
        node(session, title=f"Узел {i}")
    client(user).put(
        "/v1/me/study-methods", json={"purpose": "remember", "method": "first_letters"}
    )

    course = generate_course(session, user.id, "d", "apply")

    woven = [a for a in course.path[3]["activities"] if a.get("note")]
    assert (
        woven
        and woven[0]["type"] == ACTIVITY_TYPE
        and woven[0]["conceptId"] == course.path[0]["conceptId"]
    )


# ---- прохождение шага ----


def test_step_starts_with_an_offline_mnemonic_activity(session, client):
    modules.sync_module_state(session)
    user = make_user(session)
    n = node(session)
    client(user).put(
        "/v1/me/study-methods", json={"purpose": "remember", "method": "first_letters"}
    )
    course = generate_course(session, user.id, "d", "apply")

    activities = start_step(session, user.id, course, str(n.id))

    mnemonic = next(a for a in activities if a.type == ACTIVITY_TYPE)
    assert mnemonic.connectivity == "offline"
    assert mnemonic.payload["cue"] and mnemonic.payload["answer"].startswith("Градиентный")
    assert session.query(Activity).filter_by(user_id=user.id, type=ACTIVITY_TYPE).count() == 1


def test_self_rating_of_the_technique_moves_the_same_mastery_as_repetition(session, client):
    modules.sync_module_state(session)
    one, two = make_user(session), make_user(session)
    n = node(session)

    body = {"domain": "d", "conceptId": str(n.id), "bloom": "remember", "score": 0.8}
    client(one).post("/v1/evidence", json={**body, "source": "first_letters"})
    client(two).post("/v1/evidence", json={**body, "source": "srs"})

    assert kg.get_mastery(session, one.id, "d") == kg.get_mastery(session, two.id, "d")
