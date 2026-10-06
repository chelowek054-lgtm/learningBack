"""Загрузка учебника администратором: форма, дубли, скан, прогресс (T-0079)."""

from __future__ import annotations

import io

import pytest
from pypdf import PdfWriter

from core import objects
from core.config import settings
from core.jobs import process_job
from core.models import Job
from core.objects import MemoryObjectStore
from modules.knowledge import provenance
from modules.knowledge.models import Concept, SourceDocument, SourceFragment
from tests.conftest import make_user

BOOK = b"# Groups\n\nA group is a set together with an operation that is associative.\n"


class As:
    def __init__(self, client, user):
        self._c, self._u = client, user

    def get(self, url, **kw):
        return self._c(self._u).get(url, **kw)

    def post(self, url, **kw):
        return self._c(self._u).post(url, **kw)


@pytest.fixture(autouse=True)
def _memory_store(monkeypatch):
    store = MemoryObjectStore()
    monkeypatch.setattr(objects, "_store", store)
    yield store
    objects.reset_store()


@pytest.fixture
def admin(session, client):
    return As(client, make_user(session, superuser=True))


def upload(who, data=BOOK, name="book.md", **form):
    return who.post(
        "/graph/sources",
        files={"file": (name, data, "application/octet-stream")},
        data={"domain": "algebra", **form},
    )


def blank_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(100, 100)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


# ---- права ----


def test_only_admin_can_upload_or_watch_progress(session, client):
    learner = As(client, make_user(session))
    assert upload(learner).status_code == 403
    assert (
        learner.get("/graph/sources/00000000-0000-0000-0000-000000000000/progress").status_code
        == 403
    )


# ---- загрузка ----


def test_upload_stores_the_file_parses_fragments_and_queues_ingestion(
    session, admin, _memory_store
):
    r = upload(admin, title="Algebra", level="B2", license="CC BY-SA")
    body = r.json()

    assert r.status_code == 201 and body["created"] is True and body["status"] == "queued"
    doc = session.get(SourceDocument, body["documentId"])
    assert doc.title == "Algebra" and doc.level == "B2" and doc.license == "CC BY-SA"
    assert doc.domain == "algebra" and _memory_store.exists(doc.object_key)
    assert (
        session.query(SourceFragment).filter_by(document_id=doc.id).count()
        == body["fragments"]
        == 1
    )
    job = session.query(Job).filter_by(type="ingest_document").one()
    assert job.input_ref == {"documentId": str(doc.id)} and job.status == "pending"


def test_title_defaults_to_the_one_found_in_the_file(session, admin):
    body = upload(admin).json()
    assert body["title"] == "Groups"


def test_same_file_again_returns_the_same_document_without_a_second_job(session, admin):
    first = upload(admin).json()
    second = upload(admin).json()

    assert second["created"] is False and second["documentId"] == first["documentId"]
    assert session.query(SourceDocument).count() == 1
    assert session.query(Job).filter_by(type="ingest_document").count() == 1


def test_scan_without_text_is_refused_at_once_and_leaves_no_trace(session, admin, _memory_store):
    r = upload(admin, data=blank_pdf(), name="scan.pdf")

    assert r.status_code == 422
    assert "нет текста" in r.json()["detail"] or "скан" in r.json()["detail"]
    assert session.query(SourceDocument).count() == 0 and session.query(Job).count() == 0
    assert _memory_store._items == {}  # в хранилище ничего не попало


@pytest.mark.parametrize(
    ("data", "name", "code"),
    [(b"", "a.md", 422), (b"x", "a.docx", 415)],
)
def test_empty_and_unsupported_files(admin, data, name, code):
    assert upload(admin, data=data, name=name).status_code == code


def test_too_large_file_is_refused(admin, monkeypatch):
    monkeypatch.setattr(settings, "max_source_bytes", 10)
    assert upload(admin).status_code == 413


def test_domain_is_required(admin):
    r = admin.post("/graph/sources", files={"file": ("a.md", BOOK, "text/plain")}, data={})
    assert r.status_code == 422


# ---- прогресс ----


class Gateway:
    def __init__(self, answer, fail=False):
        self.answer, self.fail = answer, fail

    def structured(self, *a, **k):
        if self.fail:
            raise RuntimeError("провайдер недоступен")
        return self.answer


ANSWER = {
    "concepts": [
        {
            "key": "group",
            "title": "Group",
            "summary": "Множество с операцией.",
            "sources": [1],
            "quote": "A group is a set together with an operation",
        }
    ],
    "edges": [],
}


def test_progress_goes_from_queued_to_done_and_counts_drafts(session, admin):
    doc_id = upload(admin).json()["documentId"]
    url = f"/graph/sources/{doc_id}/progress"
    assert admin.get(url).json()["status"] == "queued"

    job = session.query(Job).filter_by(type="ingest_document").one()
    process_job(session, job, Gateway(ANSWER))

    done = admin.get(url).json()
    assert done["status"] == "done" and done["windowsDone"] == done["windows"] == 1
    assert done["concepts"] == 1 and done["drafts"] == 1 and done["verified"] == 0
    # человек подтвердил — в сводке это видно
    concept = session.query(Concept).filter_by(domain="algebra").one()
    provenance.review_concept(session, concept, make_user(session, superuser=True).id, "approve")
    again = admin.get(url).json()
    assert again["drafts"] == 0 and again["verified"] == 1


def test_failed_job_is_reported_with_its_reason(session, admin, monkeypatch):
    monkeypatch.setattr(settings, "job_max_attempts", 1)
    doc_id = upload(admin).json()["documentId"]
    job = session.query(Job).filter_by(type="ingest_document").one()
    process_job(session, job, Gateway(ANSWER, fail=True))

    got = admin.get(f"/graph/sources/{doc_id}/progress").json()
    assert got["status"] == "failed" and "недоступен" in got["error"]


def test_reupload_after_failure_queues_the_ingestion_again(session, admin, monkeypatch):
    monkeypatch.setattr(settings, "job_max_attempts", 1)
    upload(admin)
    process_job(session, session.query(Job).one(), Gateway(ANSWER, fail=True))

    again = upload(admin).json()

    assert again["created"] is False and again["status"] == "queued"
    assert session.query(Job).filter_by(type="ingest_document").count() == 2


def test_list_carries_progress_for_every_document(session, admin):
    upload(admin)
    rows = admin.get("/graph/sources").json()
    assert len(rows) == 1 and rows[0]["progress"]["status"] == "queued"
    assert rows[0]["progress"]["fragments"] == 1
