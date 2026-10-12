"""Одно понятие в двух областях (V-0132, R-0073, T-0155)."""

from __future__ import annotations

from core.embeddings import HashEmbedder
from modules.knowledge import merge
from modules.knowledge.course import build_path
from modules.knowledge.cow import effective_graph
from modules.knowledge.mastery import MasteryState, load_map, save_state
from modules.knowledge.models import Concept, ConceptArea, ConceptSource
from tests.conftest import make_user
from tests.kg_helpers import Gateway, claim, concept, document

SUMMARY = "Скорость изменения величины по другой величине."


def two_areas(session):
    d1, f1 = document(session, title="Calc", domain="calculus")
    d2, f2 = document(session, title="Phys", domain="physics", data=b"p")
    a = concept(
        session,
        "Derivative",
        domain="calculus",
        summary=SUMMARY,
        stage="Basics",
        stage_order=1,
        level="basic",
    )
    b = concept(
        session,
        "Derivative",
        domain="physics",
        summary=SUMMARY,
        stage="Kinematics",
        stage_order=3,
        level="middle",
    )
    from modules.knowledge import provenance

    provenance.link_concept(session, a, [f1[0].id])
    provenance.link_concept(session, b, [f2[0].id])
    claim(session, a, "A derivative is a rate of change.", f1[0], status="proposed")
    claim(session, b, "Velocity is the derivative of position.", f2[0], status="proposed")
    return a, b


def merged(session):
    a, b = two_areas(session)
    merge.merge_domain(session, "calculus", Gateway(), HashEmbedder())
    session.flush()
    return session.query(Concept).filter_by(title="Derivative").all()


def test_two_sources_give_one_node_with_two_memberships(session):
    nodes = merged(session)
    assert len(nodes) == 1
    areas = {x.domain: x for x in session.query(ConceptArea).filter_by(concept_id=nodes[0].id)}
    assert set(areas) == {"calculus", "physics"}
    assert {x.stage for x in areas.values()} == {"Basics", "Kinematics"}
    assert session.query(ConceptSource).filter_by(concept_id=nodes[0].id).count() == 2


def test_each_area_course_sees_the_node_with_its_own_stage(session):
    node = merged(session)[0]
    user = make_user(session)
    stages = {}
    for domain in ("calculus", "physics"):
        step = next(
            s
            for s in build_path(session, user.id, domain, "understand")
            if s["conceptId"] == str(node.id)
        )
        stages[domain] = (step.get("stage"), step.get("level"))
    assert stages["calculus"][0] == "Basics" and stages["physics"][0] == "Kinematics"


def test_graph_of_each_area_carries_its_own_labels(session):
    node = merged(session)[0]
    user = make_user(session)
    for domain, stage in (("calculus", "Basics"), ("physics", "Kinematics")):
        nodes = effective_graph(session, user.id, domain)["nodes"]
        assert [n["stage"] for n in nodes if n["id"] == str(node.id)] == [stage]


def test_mastery_is_shared_between_areas(session):
    node = merged(session)[0]
    user = make_user(session)
    known = MasteryState(alpha=9.0, beta=1.0, observations=9)
    save_state(session, user.id, "calculus", node.id, known)
    for domain in ("calculus", "physics"):
        assert load_map(session, user.id, domain)[node.id].estimate >= 0.8, domain
