"""Граф без предметов (T-0052, R-0028, V-0080): один код — три предмета разной природы."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from modules.knowledge.models import Concept, ConceptEdge
from tests.conftest import make_user

SUBJECTS = [
    pytest.param("ml", id="технический"),
    pytest.param("english-grammar", id="языковой"),
    pytest.param("cooking", id="произвольный"),
]

KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "modules" / "knowledge"

# Слова предметов: в коде графа их быть не должно ни как условия, ни как контент заглушек.
SUBJECT_WORDS = [
    "ielts",
    "toefl",
    "english",
    "английск",
    "машинн",
    "нейро",
    "backprop",
    "softmax",
    "трансформер",
    "python",
    "программир",
    "кулинар",
]


# ---- в коде графа нет предметов ----


def _source_lines():
    for path in sorted(KNOWLEDGE_DIR.glob("*.py")):
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            yield path.name, n, line


def test_graph_code_contains_no_subject_words():
    hits = [
        f"{name}:{n}: {line.strip()}"
        for name, n, line in _source_lines()
        if any(w in line.lower() for w in SUBJECT_WORDS)
    ]

    assert hits == []


def test_graph_code_never_branches_on_a_subject_or_module_name():
    # `domain == "ml"`, `subject in ("ielts", ...)`, `if title == "..."` и подобные.
    branch = re.compile(r"\b(domain|subject|title)\b[^\n]*(==|!=|\bin\b)\s*[\(\[]?\s*[\"']")
    hits = [
        f"{name}:{n}: {line.strip()}"
        for name, n, line in _source_lines()
        if branch.search(line) and "filter" not in line and "filter_by" not in line
    ]

    assert hits == []


def test_graph_code_does_not_name_other_modules():
    pattern = re.compile(r"[\"'](languages|ml)[\"']")

    assert [f"{n}:{i}" for n, i, line in _source_lines() if pattern.search(line)] == []


# ---- один код, три предмета ----


def _build(api, domain):
    return api.post("/graph/canon/build", json={"domain": domain, "topic": "что угодно"})


def _shape(session, domain):
    concepts = session.query(Concept).filter_by(domain=domain).all()
    ids = {c.id for c in concepts}
    edges = session.query(ConceptEdge).filter(ConceptEdge.from_id.in_(ids)).all()
    return len(concepts), len(edges), tuple(sorted({c.tier for c in concepts}))


@pytest.mark.parametrize("domain", SUBJECTS)
def test_graph_course_and_placement_work_for_any_subject(session, client, domain):
    api = client(make_user(session))

    graph = _build(api, domain).json()
    probe = api.get(f"/graph/placement/{domain}/probe?target=understand").json()
    course = api.post(f"/graph/course/{domain}", json={"bloom": "apply"})

    assert len(graph["nodes"]) == 6 and len(graph["edges"]) == 6
    assert probe.get("conceptId") or probe.get("done")  # граница найдена или честно исчерпана
    assert course.status_code in (200, 201), course.text
    assert course.json()["total"] >= 1


def test_the_three_subjects_get_structurally_identical_results(session, client):
    api = client(make_user(session, superuser=True))
    shapes = {}
    for param in SUBJECTS:
        domain = param.values[0]
        _build(api, domain)
        shapes[domain] = _shape(session, domain)

    assert len(set(shapes.values())) == 1  # различия — в данных, а не в ветвлении кода


def test_subjects_do_not_leak_into_each_other(session, client):
    api = client(make_user(session))
    _build(api, "ml")
    _build(api, "cooking")

    ml = {n["id"] for n in api.get("/graph/ml").json()["nodes"]}
    cooking = {n["id"] for n in api.get("/graph/cooking").json()["nodes"]}

    assert ml and cooking and not ml & cooking


# ---- языковая область отдельно: плоская, без иерархии предпосылок ----


def test_flat_language_graph_without_prerequisites_still_works(session, client):
    """Языковая область менее иерархична: допущение «граф — дерево предпосылок» не переносим."""
    user = make_user(session)
    for word in ("глаголы", "артикли", "предлоги", "времена"):
        session.add(
            Concept(
                domain="english-flat",
                title=word,
                tier="core",
                content={
                    "summary": f"Тема «{word}»: что это и когда применяется. " * 4,
                    "sections": [
                        {
                            "heading": word,
                            "body": f"Разбор темы «{word}».",
                            "examples": ["пример"],
                            "counter_examples": ["заблуждение"],
                        }
                    ],
                    "references": [],
                },
                bloom_levels=["remember", "understand", "apply"],
                source="curated",
                status="approved",
            )
        )
    session.flush()
    api = client(user)

    probe = api.get("/graph/placement/english-flat/probe?target=understand").json()
    course = api.post("/graph/course/english-flat", json={"bloom": "apply"}).json()

    assert probe.get("conceptId"), probe  # без предпосылок все темы — граница
    assert course["total"] == 4  # путь охватывает все темы, ни одна не потеряна
