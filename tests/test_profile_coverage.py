"""Отчёт полноты графа против профиля навыка (T-0099)."""

from __future__ import annotations

from modules.knowledge import cross_links, merge, profile_build, profile_coverage, profile_store
from modules.knowledge.models import (
    Concept,
    ConceptEdge,
    ConceptSource,
    SkillProfile,
    SourceDocument,
    SourceFragment,
)
from tests.conftest import make_user
from tests.test_profile_build import profile


def row_for(session, user, prof=None, status="confirmed"):
    row = SkillProfile(user_id=user.id, domain="ml", status=status)
    row.profile = prof or profile()
    session.add(row)
    session.flush()
    return row


def cite(session, concept):
    doc = SourceDocument(title="Книга", content_hash=f"h{concept.id}", domain=concept.domain)
    session.add(doc)
    session.flush()
    frag = SourceFragment(document_id=doc.id, ordinal=1, text="текст")
    session.add(frag)
    session.flush()
    session.add(ConceptSource(concept_id=concept.id, fragment_id=frag.id, role="definition"))
    session.flush()


def area(report, key):
    return next(a for a in report["areas"] if a["key"] == key)


def test_before_the_build_everything_is_missing(session):
    row = row_for(session, make_user(session))
    rep = profile_coverage.report(session, row)
    assert rep["summary"] == {"total": 5, "verified": 0, "missing": 4, "coverage": 0.0}
    assert area(rep, "goal")["missing"] == [
        "Икс",
        "Игрек",
    ]  # «Зет» необязательное и в «не хватает» не входит
    assert area(rep, "goal")["optionalMissing"] == 1


def test_after_the_build_coverage_is_full_and_nothing_is_verified(session):
    user = make_user(session)
    row = row_for(session, user)
    profile_build.build_skeleton(session, "ml", row.profile)

    rep = profile_coverage.report(session, row)

    assert rep["summary"]["coverage"] == 1.0 and rep["summary"]["missing"] == 0
    assert rep["summary"]["verified"] == 0 and area(rep, "base")["found"] == 2


def test_missing_concepts_are_named_and_lower_the_coverage(session):
    prof = profile()
    for i, c in enumerate(c for a in prof["areas"] for c in a["concepts"]):
        c["summary"] = f"Совсем другое описание номер {i}: " + "слова " * 25 + f"уникум{i * 7919}"
    row = row_for(session, make_user(session), prof)
    profile_build.build_skeleton(session, "ml", row.profile)
    gone = session.query(Concept).filter_by(domain="ml", title="Игрек").one()
    session.query(ConceptEdge).filter(
        (ConceptEdge.from_id == gone.id) | (ConceptEdge.to_id == gone.id)
    ).delete()
    session.query(cross_links.ConceptLink).filter(
        (cross_links.ConceptLink.from_id == gone.id) | (cross_links.ConceptLink.to_id == gone.id)
    ).delete()
    session.delete(gone)
    session.flush()

    rep = profile_coverage.report(session, row)

    assert area(rep, "goal")["missing"] == ["Игрек"]
    assert area(rep, "goal")["coverage"] == 0.5  # обязательных двое (Икс, Игрек), найден один


def test_verification_is_counted(session):
    row = row_for(session, make_user(session))
    profile_build.build_skeleton(session, "ml", row.profile)
    session.query(Concept).filter_by(domain="ml", title="Икс").one().status = "approved"
    session.flush()
    assert profile_coverage.report(session, row)["summary"]["verified"] == 1


def test_source_breakdown_is_for_admins_only(session):
    row = row_for(session, make_user(session))
    profile_build.build_skeleton(session, "ml", row.profile)
    cite(session, session.query(Concept).filter_by(domain="ml", title="Икс").one())

    learner = profile_coverage.report(session, row)
    admin = profile_coverage.report(session, row, admin=True)

    assert "sourced" not in learner["summary"] and "modelOnly" not in area(learner, "goal")
    assert admin["summary"]["sourced"] == 1 and admin["summary"]["modelOnly"] == 4
    assert (area(admin, "goal")["sourced"], area(admin, "goal")["modelOnly"]) == (1, 2)


def test_concept_merged_with_a_source_one_is_still_found(session):
    row = row_for(session, make_user(session))
    profile_build.build_skeleton(session, "ml", row.profile)
    skeleton = session.query(Concept).filter_by(domain="ml", title="Икс").one()
    # понятие из источника: другой ключ и название, но тот же смысл; скелетное влито в него
    ingested = Concept(
        domain="ml",
        key="ingested",
        title=skeleton.title,
        tier="core",
        content=skeleton.content,
        bloom_levels=[],
    )
    session.add(ingested)
    session.flush()
    merge.merge_into(session, ingested, skeleton)
    session.delete(skeleton)
    session.flush()

    rep = profile_coverage.report(session, row, admin=True)

    assert area(rep, "goal")["missing"] == [] and area(rep, "goal")["found"] == 3


def test_api_needs_a_profile_and_hides_sources_from_learners(session, client):
    user = make_user(session)
    api = client(user)
    assert api.get("/graph/profile/ml/coverage").status_code == 404
    row = row_for(session, user)
    profile_build.build_skeleton(session, "ml", row.profile)
    body = api.get("/graph/profile/ml/coverage").json()
    assert body["summary"]["coverage"] == 1.0 and "sourced" not in body["summary"]
    admin = make_user(session, superuser=True)
    row_for_admin = SkillProfile(
        user_id=admin.id, domain="ml", status="confirmed", profile=profile()
    )
    session.add(row_for_admin)
    session.flush()
    assert "sourced" in client(admin).get("/graph/profile/ml/coverage").json()["summary"]


def test_stored_profile_flows_end_to_end(session):
    user = make_user(session)
    row = row_for(session, user)
    profile_store.build_graph(session, row)
    assert profile_coverage.report(session, row)["summary"]["coverage"] == 1.0
