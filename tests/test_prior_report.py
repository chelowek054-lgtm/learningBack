"""Отчёт по предварительным знаниям: хватает / мало / нет / не проверено / нет в графе (T-0075)."""
# ruff: noqa: F811  (фикстура world импортирована из test_chain_placement)

from __future__ import annotations

from modules.knowledge import domains, prior_report
from modules.knowledge.course import build_path
from modules.knowledge.mastery import MasteryState, save_state
from tests.conftest import make_user
from tests.test_chain_placement import (  # noqa: F401
    FAILED_STATE,
    KNOWN_STATE,
    concept,
    world,
)


def know(session, user, w, name, state=KNOWN_STATE):
    save_state(session, user.id, w[name].domain, w[name].id, state)


def verdicts(session, user, target="understand"):
    rep = prior_report.report(session, user.id, "top", target)
    return {a["key"]: a["verdict"] for a in rep["areas"]}


# ---- вердикты ----


def test_without_a_test_everything_is_unchecked_and_the_course_still_builds(session, world):
    user = make_user(session)

    rep = prior_report.report(session, user.id, "top", "understand")

    assert [a["key"] for a in rep["areas"]] == ["mid", "base"]  # сверху вниз
    assert verdicts(session, user) == {"mid": "unchecked", "base": "unchecked"}
    assert rep["checked"] is False and rep["missing"] == []
    # Тест пропущен — курс строится так, будто человек ничего не знает.
    assert [s["title"] for s in build_path(session, user.id, "top", "understand")] == [
        "Наклон",
        "Производная",
        "Спуск",
    ]


def test_known_upper_concept_makes_both_areas_enough(session, world):
    user = make_user(session)
    know(session, user, world, "deriv")

    assert verdicts(session, user) == {"mid": "enough", "base": "enough"}
    rep = prior_report.report(session, user.id, "top", "understand")
    base = next(a for a in rep["areas"] if a["key"] == "base")
    assert base["known"] == 1 and base["answered"] == 0  # снято вышестоящим, не отвечал


def test_known_lower_area_only_is_little(session, world):
    user = make_user(session)
    know(session, user, world, "slope")

    assert verdicts(session, user) == {"mid": "unchecked", "base": "enough"}


def test_failed_answers_in_an_area_mean_none(session, world):
    user = make_user(session)
    know(session, user, world, "deriv", FAILED_STATE)
    know(session, user, world, "slope", FAILED_STATE)

    assert verdicts(session, user) == {"mid": "none", "base": "none"}
    assert prior_report.report(session, user.id, "top", "understand")["checked"] is True


def test_area_with_part_of_the_needed_concepts_known_is_partial(session, world):
    user = make_user(session)
    extra = concept(session, "base", "Касательная")
    from modules.knowledge import cross_links

    cross_links.add_link(session, extra.id, world["deriv"].id, "understand")
    know(session, user, world, "slope")

    rep = prior_report.report(session, user.id, "top", "understand")
    base = next(a for a in rep["areas"] if a["key"] == "base")

    assert base["verdict"] == "partial" and (base["needed"], base["known"]) == (2, 1)


def test_prior_alone_is_not_an_answer(session, world):
    user = make_user(session)
    know(session, user, world, "deriv", MasteryState(alpha=9.0, beta=1.0, observations=0))

    assert verdicts(session, user)["mid"] == "unchecked"


def test_summary_counts_every_verdict(session, world):
    user = make_user(session)
    know(session, user, world, "slope")

    s = prior_report.report(session, user.id, "top", "understand")["summary"]

    assert s == {"enough": 1, "partial": 0, "none": 0, "unchecked": 1, "no_graph": 0}


# ---- сверка с графом ----


def test_area_missing_from_the_graph_is_reported_as_such(session, world):
    domains.register(session, "deep", foundation=True)
    domains.add_prereq(session, "mid", "deep")  # опорная область без понятий
    user = make_user(session)

    rep = prior_report.report(session, user.id, "top", "understand")

    assert {a["key"]: a["verdict"] for a in rep["areas"]}["deep"] == "no_graph"
    assert rep["missing"] == ["deep"]
    assert rep["areas"][-1]["key"] == "deep"  # самая примитивная — последней


def test_drafts_are_counted_per_area(session, world):
    world["slope"].status = "draft"
    session.flush()

    rep = prior_report.report(session, make_user(session).id, "top", "understand")

    base = next(a for a in rep["areas"] if a["key"] == "base")
    assert (base["concepts"], base["drafts"]) == (1, 1)


def test_unregistered_goal_has_no_report(session):
    rep = prior_report.report(session, make_user(session).id, "ghost", "understand")
    assert rep["registered"] is False and rep["areas"] == []


# ---- API ----


def test_report_endpoint(session, client, world):
    api = client(make_user(session))

    rep = api.get("/graph/placement/top/report?target=understand").json()

    assert rep["goal"] == "top" and [a["verdict"] for a in rep["areas"]] == [
        "unchecked",
        "unchecked",
    ]


def test_chain_answer_records_mastery_and_returns_next_probe_and_report(session, client, world):
    user = make_user(session)
    api = client(user)
    probe = api.get("/graph/placement/top/chain-probe?target=understand").json()
    item = probe["item"]
    correct = (
        next(i for i, o in enumerate(item["options"]) if o["correct"])
        if item["options"]
        else item["expected"]
    )

    r = api.post(
        "/graph/placement/top/chain-answer?target=understand",
        json={"concept_id": probe["conceptId"], "bloom": probe["bloom"], "answer": correct},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["score"] > 0 and body["report"]["checked"] is True
    assert body["report"]["areas"][0]["answered"] == 1
    assert "next" in body


def test_chain_answer_for_unknown_concept_is_404(session, client, world):
    api = client(make_user(session))
    r = api.post(
        "/graph/placement/top/chain-answer",
        json={
            "concept_id": "00000000-0000-0000-0000-000000000000",
            "bloom": "understand",
            "answer": 0,
        },
    )
    assert r.status_code == 404
