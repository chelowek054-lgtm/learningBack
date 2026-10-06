"""Уведомления «курс готов» и «курс дополнен» с пометкой о черновике (T-0083, V-0100)."""

from __future__ import annotations

import pytest

from core import objects
from core.jobs import process_job
from core.objects import MemoryObjectStore
from modules.knowledge import ingest, notifications, provenance
from modules.knowledge.course import generate_course
from modules.knowledge.models import Concept, Course, Notification
from tests.conftest import make_user

DOMAIN = "algebra"


class As:
    def __init__(self, client, user):
        self._c, self._u = client, user

    def get(self, url, **kw):
        return self._c(self._u).get(url, **kw)

    def post(self, url, **kw):
        return self._c(self._u).post(url, **kw)


def concept(session, title, status="draft"):
    c = Concept(
        domain=DOMAIN,
        title=title,
        tier="core",
        content={"summary": "Достаточно длинное изложение понятия.", "sections": []},
        bloom_levels=["remember", "understand"],
        difficulty=1,
        source="doc",
        status=status,
    )
    session.add(c)
    session.flush()
    return c


def learner_with_course(session):
    user = make_user(session)
    concept(session, "Group")
    course = generate_course(session, user.id, DOMAIN, "understand", [])
    session.flush()
    return user, course


# ---- «курс готов» ----


def test_ready_notice_names_the_size_and_the_draft_part(session):
    user = make_user(session)
    n = notifications.course_ready(session, user.id, DOMAIN, steps=5, drafts=2)
    assert n.title == "Курс готов" and "5 шагов" in n.body and "можно приступать" in n.body
    assert notifications.UNVERIFIED in n.body and "Черновых шагов: 2" in n.body


def test_ready_notice_without_drafts_does_not_scare(session):
    user = make_user(session)
    n = notifications.course_ready(session, user.id, DOMAIN, steps=1, drafts=0)
    assert "1 шаг," in n.body and "Не проверено" not in n.body


def test_second_unread_ready_notice_for_the_same_area_is_not_created(session):
    user = make_user(session)
    assert notifications.course_ready(session, user.id, DOMAIN, 3, 0) is not None
    assert notifications.course_ready(session, user.id, DOMAIN, 4, 0) is None
    assert session.query(Notification).count() == 1


def test_building_a_course_over_the_api_leaves_the_notice(session, client):
    user = make_user(session)
    concept(session, "Group", status="draft")
    api = As(client, user)
    r = api.post(f"/graph/course/{DOMAIN}", json={"target_bloom": "understand"})
    assert r.status_code == 200
    got = api.get("/graph/notifications").json()
    assert [n["kind"] for n in got] == ["course_ready"]
    assert got[0]["data"] == {"steps": 1, "drafts": 1} and "Не проверено" in got[0]["body"]


# ---- «курс дополнен» ----


def test_extension_reaches_everyone_studying_the_area_and_nobody_else(session):
    a, _ = learner_with_course(session)
    other = make_user(session)  # курса по области нет
    count = notifications.course_extended(session, DOMAIN, added=3, drafts=3)
    assert count == 1
    mine = notifications.listing(session, a.id)
    assert [n["kind"] for n in mine] == ["course_extended"]
    assert "3 понятия" in mine[0]["body"] and "Из них черновых: 3" in mine[0]["body"]
    assert notifications.listing(session, other.id) == []


def test_unread_extensions_accumulate_instead_of_piling_up(session):
    user, _ = learner_with_course(session)
    notifications.course_extended(session, DOMAIN, 2, 2)
    notifications.course_extended(session, DOMAIN, 3, 1)
    rows = session.query(Notification).filter_by(user_id=user.id, kind="course_extended").all()
    assert len(rows) == 1 and rows[0].data == {"added": 5, "drafts": 3}
    assert "5 понятий" in rows[0].body


def test_after_reading_the_next_extension_is_a_new_notice(session):
    user, _ = learner_with_course(session)
    notifications.course_extended(session, DOMAIN, 2, 2)
    notifications.mark_read(session, user.id)
    notifications.course_extended(session, DOMAIN, 1, 1)
    assert session.query(Notification).filter_by(user_id=user.id).count() == 2
    assert len(notifications.listing(session, user.id)) == 1


def test_nothing_added_means_no_notice(session):
    learner_with_course(session)
    assert notifications.course_extended(session, DOMAIN, 0, 0) == 0
    assert session.query(Notification).count() == 0


def test_plural_forms():
    p = notifications._plural  # noqa: SLF001
    assert [p(n, "понятие", "понятия", "понятий") for n in (1, 2, 5, 11, 21, 12, 24)] == [
        "понятие",
        "понятия",
        "понятий",
        "понятий",
        "понятие",
        "понятий",
        "понятия",
    ]


# ---- чтение и отметка ----


def test_user_reads_only_his_own_and_marks_them_read(session, client):
    me, _ = learner_with_course(session)
    stranger = make_user(session)
    notifications.course_ready(session, stranger.id, DOMAIN, 1, 0)
    notifications.course_extended(session, DOMAIN, 2, 2)
    api = As(client, me)

    unread = api.get("/graph/notifications").json()
    assert [n["kind"] for n in unread] == ["course_extended"]

    foreign = session.query(Notification).filter_by(user_id=stranger.id).one()
    r = api.post("/graph/notifications/read", json={"ids": [str(foreign.id), "not-a-uuid"]})
    assert r.json() == {"marked": 0} and foreign.read_at is None  # чужое не отметить

    assert api.post("/graph/notifications/read", json={"ids": [unread[0]["id"]]}).json() == {
        "marked": 1
    }
    assert api.get("/graph/notifications").json() == []
    assert len(api.get("/graph/notifications", params={"unread": "false"}).json()) == 1


def test_mark_all_read_with_empty_body(session, client):
    me, _ = learner_with_course(session)
    notifications.course_extended(session, DOMAIN, 1, 1)
    assert As(client, me).post("/graph/notifications/read", json={}).json() == {"marked": 1}


# ---- связка с разбором документа ----


@pytest.fixture
def store(monkeypatch):
    s = MemoryObjectStore()
    monkeypatch.setattr(objects, "_store", s)
    yield s
    objects.reset_store()


class Gateway:
    def structured(self, *a, **k):
        return {
            "concepts": [
                {
                    "key": "ring",
                    "title": "Ring",
                    "summary": "Множество с двумя операциями.",
                    "sources": [1],
                    "quote": "A ring is a set equipped with two operations",
                }
            ],
            "edges": [],
        }


def test_finished_ingestion_tells_the_learners_of_that_area(session, store):
    user, _ = learner_with_course(session)
    doc, _ = provenance.add_document(
        session,
        title="Rings",
        data=b"# Rings\n\nA ring is a set equipped with two operations, addition and multiplication.\n",
        domain=DOMAIN,
        meta={"filename": "rings.md"},
        store=store,
    )
    job = ingest.enqueue_ingest(session, doc, user.id)

    process_job(session, job, Gateway())

    got = notifications.listing(session, user.id)
    assert [n["kind"] for n in got] == ["course_extended"]
    assert got[0]["data"] == {"added": 1, "drafts": 1}
    assert session.query(Course).filter_by(user_id=user.id).count() == 1  # курс не пересобирался


def test_repeating_a_finished_ingestion_does_not_notify_twice(session, store):
    user, _ = learner_with_course(session)
    doc, _ = provenance.add_document(
        session,
        title="Rings",
        data=b"# Rings\n\nA ring is a set equipped with two operations, addition and multiplication.\n",
        domain=DOMAIN,
        meta={"filename": "rings.md"},
        store=store,
    )
    process_job(session, ingest.enqueue_ingest(session, doc, user.id), Gateway())
    notifications.mark_read(session, user.id)
    process_job(session, ingest.enqueue_ingest(session, doc, user.id), Gateway())
    assert notifications.listing(session, user.id) == []


# ---- статус на экране узла ----


def test_step_payload_marks_draft_and_verified_nodes_without_any_source(session, client):
    from core.models import Activity
    from modules.knowledge.course import generate_course as build

    user = make_user(session)
    draft = concept(session, "Draft one")
    verified = concept(session, "Verified one", status="approved")
    course = build(session, user.id, DOMAIN, "understand", [])
    session.flush()
    api = As(client, user)
    for node, expected in ((draft, "draft"), (verified, "verified")):
        r = api.post(f"/graph/course/{DOMAIN}/step/{node.id}/start")
        assert r.status_code == 200, r.text
    statuses = {}
    for a in session.query(Activity).filter_by(user_id=user.id).all():
        p = a.payload or {}
        if p.get("title") in ("Draft one", "Verified one"):
            statuses[p["title"]] = p.get("status")
            assert "sources" not in p and "fragment" not in str(p).lower()
    assert statuses == {"Draft one": "draft", "Verified one": "verified"}
    assert course is not None
