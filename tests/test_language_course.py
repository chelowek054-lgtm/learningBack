"""Языковой предмет на общем графе: практика шага — дрилл чтения (T-0020, V-0045)."""

from __future__ import annotations

from core import modules
from modules.languages import reading_payload

NODE = {
    "conceptId": "c1",
    "title": "Present perfect",
    "content": {
        "summary": "Present perfect connects past actions with the present moment. "
        "It is formed with have and a past participle.",
        "sections": [{"heading": "Use", "body": "Use it for experience."}],
    },
}


def test_language_domain_practices_with_reading_drill():
    assert modules.practice_activity_type("english-b2") == "reading_drill"
    assert modules.practice_activity_type("machine-learning") != "reading_drill"


def test_reading_payload_has_local_gaps_from_theory():
    p = reading_payload(NODE)
    assert p is not None
    assert "Use it for experience." in p["passage"]
    for q in p["questions"]:
        assert q["type"] == "gap" and "____" in q["prompt"]
        assert q["answer"][0] in NODE["content"]["summary"]


def test_no_summary_means_no_drill():
    assert reading_payload({"title": "x", "content": {}}) is None
    assert modules.payload_for("reading_drill", {"title": "x", "content": {}}) is None


def test_reading_drill_runs_offline():
    assert modules.method_offline("reading_drill") is True
