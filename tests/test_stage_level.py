"""Этапы, уровни и необязательность понятий (T-0087, A-0030)."""

from __future__ import annotations

from modules.knowledge import merge, stages
from modules.knowledge.cow import effective_graph
from modules.knowledge.course import build_path
from modules.knowledge.models import Concept
from modules.knowledge.router import _persist_draft
from tests.conftest import make_user

THEORY = {
    "summary": "Теория узла. " * 12,
    "sections": [
        {"heading": "Идея", "body": "Подробно.", "examples": ["а"], "counter_examples": ["б"]}
    ],
    "references": [],
}


def node(key, title, **extra):
    return {"key": key, "title": title, "content": THEORY, "bloomLevels": ["remember"], **extra}


def test_labels_are_cleaned():
    assert stages.clean_level("Intermediate") == "middle"
    assert stages.clean_level(" ADVANCED ") == "advanced"
    assert stages.clean_level("legendary") is None and stages.clean_level(5) is None
    assert stages.clean_order("3") == 3 and stages.clean_order(-1) is None
    f = stages.stage_fields(
        {"stage": "  Определители ", "stageOrder": 4, "level": "mid", "optional": True}
    )
    assert f == {"stage": "Определители", "stage_order": 4, "level": "middle", "optional": True}
    assert stages.stage_fields({"stageOrder": 4}) == {
        "stage": None,
        "stage_order": None,
        "level": None,
        "optional": False,
    }
    assert stages.stage_fields({"optional": "yes"})["optional"] is False  # только настоящее True


def test_persisted_draft_keeps_the_labels_and_the_graph_returns_them(session):
    _persist_draft(
        session,
        "algebra",
        {
            "nodes": [
                node("det", "Определитель", stage="Определители", stageOrder=4, level="middle"),
                node(
                    "cramer",
                    "Метод Крамера",
                    stage="СЛУ",
                    stageOrder=5,
                    level="basic",
                    optional=True,
                ),
                node("plain", "Без меток"),
            ],
            "edges": [],
        },
        refresh=False,
    )
    user = make_user(session)

    by_title = {n["title"]: n for n in effective_graph(session, user.id, "algebra")["nodes"]}

    assert (by_title["Определитель"]["stage"], by_title["Определитель"]["stageOrder"]) == (
        "Определители",
        4,
    )
    assert (
        by_title["Определитель"]["level"] == "middle"
        and by_title["Определитель"]["optional"] is False
    )
    assert by_title["Метод Крамера"]["optional"] is True
    assert by_title["Без меток"]["stage"] is None and by_title["Без меток"]["level"] is None


def make(session, title, **kw):
    c = Concept(
        domain="algebra", title=title, tier="core", content=THEORY, bloom_levels=["remember"], **kw
    )
    session.add(c)
    session.flush()
    return c


def test_merge_moves_labels_to_a_bare_keeper_and_keeps_required(session):
    keeper = make(session, "Det", optional=True)
    loser = make(session, "Определитель", stage="Определители", stage_order=4, level="middle")

    merge.merge_into(session, keeper, loser)

    assert (keeper.stage, keeper.stage_order, keeper.level) == ("Определители", 4, "middle")
    assert keeper.optional is False  # одна из копий обязательная — значит, обязательное


def test_merge_does_not_overwrite_existing_labels(session):
    keeper = make(session, "A", stage="Основы", stage_order=1, level="basic")
    loser = make(session, "B", stage="Продвинутое", stage_order=8, level="advanced")
    merge.merge_into(session, keeper, loser)
    assert (keeper.stage, keeper.level) == ("Основы", "basic")


def test_course_steps_carry_labels_only_when_set(session):
    user = make_user(session)
    make(session, "С метками", stage="Основы", stage_order=1, level="basic", optional=False)
    path = build_path(session, user.id, "algebra", "understand")
    assert (
        path[0]["stage"] == "Основы" and path[0]["level"] == "basic" and "optional" not in path[0]
    )

    other = make_user(session)
    make(session, "Необязательное", optional=True)
    labelled = {s["title"]: s for s in build_path(session, other.id, "algebra", "understand")}
    assert (
        labelled["Необязательное"]["optional"] is True and "stage" not in labelled["Необязательное"]
    )
