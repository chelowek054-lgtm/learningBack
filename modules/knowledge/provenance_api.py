"""Админский API происхождения знаний (T-0076, R-0045): источники, проверка, удаление источника.

Всё здесь только для администратора: учащийся не получает ни документов, ни ссылок, ни текстов
фрагментов. Статус «черновик» или «проверено» он видит в курсе, но не отсюда.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field

from core.config import settings
from core.deps import CurrentSuperuser, CurrentUser, SessionDep
from core.materials import NothingToExtract, UnsupportedFile
from modules.knowledge import gap_search, ingest, provenance, review, source_search
from modules.knowledge.models import Concept, ConceptEdge, SourceDocument

router = APIRouter(tags=["provenance"])


class ReviewIn(BaseModel):
    action: str = Field(pattern="^(approve|reject)$")
    note: str | None = Field(default=None, max_length=500)


def _uuid(raw: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{what} не найден") from e


def _fail(e: provenance.ProvenanceError) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e))


@router.get("/canon/nodes/{concept_id}/sources")
def node_sources(concept_id: str, user: CurrentUser, session: SessionDep) -> dict:
    cid = _uuid(concept_id, "concept")
    concept = session.get(Concept, cid)
    if concept is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "concept не найден")
    review.require_reviewer(session, user, concept.domain)  # администратор или специалист области
    return {
        "status": concept.status,
        "sources": provenance.concept_sources(session, cid),
        "history": provenance.history(session, "concept", cid),
    }


@router.get("/canon/edges/{edge_id}/sources")
def edge_sources(edge_id: str, user: CurrentUser, session: SessionDep) -> dict:
    eid = _uuid(edge_id, "edge")
    edge = session.get(ConceptEdge, eid)
    if edge is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "edge не найден")
    review.require_reviewer(session, user, session.get(Concept, edge.from_id).domain)
    return {
        "status": edge.status,
        "sources": provenance.edge_sources(session, eid),
        "history": provenance.history(session, "edge", eid),
    }


@router.post("/canon/nodes/{concept_id}/review")
def review_node(concept_id: str, body: ReviewIn, user: CurrentUser, session: SessionDep) -> dict:
    concept = session.get(Concept, _uuid(concept_id, "concept"))
    if concept is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "concept не найден")
    review.require_reviewer(session, user, concept.domain)
    try:
        provenance.review_concept(session, concept, user.id, body.action, body.note)
    except provenance.ProvenanceError as e:
        raise _fail(e) from e
    session.commit()
    return {"id": str(concept.id), "status": concept.status}


@router.post("/canon/edges/{edge_id}/review")
def review_edge(edge_id: str, body: ReviewIn, user: CurrentUser, session: SessionDep) -> dict:
    edge = session.get(ConceptEdge, _uuid(edge_id, "edge"))
    if edge is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "edge не найден")
    review.require_reviewer(session, user, session.get(Concept, edge.from_id).domain)
    try:
        provenance.review_edge(session, edge, user.id, body.action, body.note)
    except provenance.ProvenanceError as e:
        raise _fail(e) from e
    session.commit()
    return {"id": str(edge.id), "status": edge.status}


@router.get("/sources")
def list_sources(_: CurrentSuperuser, session: SessionDep) -> list[dict]:
    docs = session.query(SourceDocument).order_by(SourceDocument.created_at.desc()).all()
    return [
        {
            "id": str(d.id),
            "title": d.title,
            "domain": d.domain,
            "level": d.level,
            "license": d.license,
            "originUrl": d.origin_url,
            "createdAt": d.created_at.isoformat() if d.created_at else None,
            "progress": ingest.progress(session, d),
        }
        for d in docs
    ]


@router.get("/sources/{document_id}/link")
def source_link(document_id: str, _: CurrentSuperuser, session: SessionDep) -> dict:
    """Временная подписанная ссылка на исходный файл: живёт минуты, выдаётся только админу."""
    doc = session.get(SourceDocument, _uuid(document_id, "document"))
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document не найден")
    try:
        url = provenance.document_link(doc)
    except KeyError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Файл в хранилище не найден") from e
    if url is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Файл не сохранялся")
    return {"url": url, "expiresInSec": 300}


@router.delete("/sources/{document_id}")
def delete_source(
    document_id: str,
    _: CurrentSuperuser,
    session: SessionDep,
    keep_verified: bool = Query(default=False, alias="keepVerified"),
) -> dict:
    """Удалить источник вместе с выведенным только из него; `keepVerified` оставляет подтверждённое."""
    doc = session.get(SourceDocument, _uuid(document_id, "document"))
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document не найден")
    report = provenance.remove_document(session, doc, keep_verified=keep_verified)
    session.commit()
    return {
        "fragments": report.fragments,
        "conceptsDeleted": len(report.concepts_deleted),
        "conceptsRejected": len(report.concepts_rejected),
        "conceptsKept": len(report.concepts_kept),
        "edgesDeleted": report.edges_deleted,
        "fileDeleted": report.file_deleted,
    }


@router.post("/sources", status_code=status.HTTP_201_CREATED)
async def upload_source(
    user: CurrentSuperuser,
    session: SessionDep,
    file: UploadFile = File(...),
    domain: str = Form(..., min_length=1, max_length=200),
    title: str | None = Form(None, max_length=300),
    level: str | None = Form(None, max_length=50),
    license: str | None = Form(None, max_length=200),
    origin_url: str | None = Form(None, max_length=1000),
) -> dict:
    """Загрузить учебник (PDF, Markdown, текст) в канон: файл — в хранилище, разбор — в очередь.

    Тот же файл второй раз дубля не создаёт: вернётся прежний документ и его прогресс. Файл без
    текстового слоя (скан) отклоняется сразу с объяснением, а не после часа разбора.
    """
    data = await file.read(settings.max_source_bytes + 1)
    if len(data) > settings.max_source_bytes:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"Файл больше {settings.max_source_bytes // (1024 * 1024)} МБ",
        )
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Файл пустой")
    filename = file.filename or "source"
    # Проверяем читаемость до записи в хранилище: скан и битый PDF не должны оставлять следов.
    try:
        probe = ingest.materials.extract(filename, data)
    except UnsupportedFile as e:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(e)) from e
    except NothingToExtract as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
    try:
        doc, created = provenance.add_document(
            session,
            title=(title or "").strip() or probe.title,
            data=data,
            added_by=user.id,
            domain=domain.strip(),
            level=(level or "").strip() or None,
            license=(license or "").strip() or None,
            origin_url=(origin_url or "").strip() or None,
            meta={"filename": filename},
        )
    except provenance.ProvenanceError as e:
        raise _fail(e) from e
    if created:
        ingest.parse_document(session, doc, filename, data)
    view = ingest.progress(session, doc)
    if view["status"] in ("new", "failed"):
        # Новый документ, а также повторная загрузка после сбоя: разбор в очередь воркера.
        ingest.enqueue_ingest(session, doc, user.id)
        view = ingest.progress(session, doc)
    session.commit()
    return {"created": created, **view}


@router.get("/sources/{document_id}/progress")
def source_progress(document_id: str, _: CurrentSuperuser, session: SessionDep) -> dict:
    doc = session.get(SourceDocument, _uuid(document_id, "document"))
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document не найден")
    return ingest.progress(session, doc)


class CandidateIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    url: str = Field(min_length=8, max_length=1000)
    provider: str = Field(default="manual", max_length=50)
    license: str = Field(default="не определена", max_length=200)
    kind: str = Field(pattern="^(wikitext|pdf|html)$")
    summary: str = Field(default="", max_length=400)


class SearchIn(BaseModel):
    query: str = Field(min_length=3, max_length=300)
    limit: int = Field(default=5, ge=1, le=10)


class FetchIn(BaseModel):
    domain: str = Field(min_length=1, max_length=200)
    candidates: list[CandidateIn] = Field(min_length=1, max_length=10)


@router.post("/sources/search")
def search_sources(body: SearchIn, _: CurrentSuperuser) -> dict:
    """Найти источники в каталогах белого списка (и веб-поиском, если настроен). Ничего не скачивает."""
    try:
        found, problems = source_search.search(body.query, body.limit)
    except source_search.SearchError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
    return {
        "candidates": [c.dump() for c in found],
        "problems": problems,
        "domains": sorted(source_search.allowed_domains()),
    }


@router.post("/sources/fetch", status_code=status.HTTP_202_ACCEPTED)
def fetch_sources(body: FetchIn, user: CurrentSuperuser, session: SessionDep) -> dict:
    """Скачать выбранное и поставить разбор в очередь; адрес вне белого списка отклоняется."""
    candidates = [source_search.Candidate(**c.model_dump()) for c in body.candidates]
    outside = [c.url for c in candidates if not source_search.host_allowed(c.url)]
    if outside:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Адрес вне белого списка доменов: {outside[0]}",
        )
    result = source_search.fetch_and_queue(session, user.id, body.domain.strip(), candidates)
    session.commit()
    return result


class GapRequest(BaseModel):
    area: str = Field(min_length=1, max_length=200)
    query: str = Field(default="", max_length=300)


class GapFillIn(BaseModel):
    target: str = Field(default="understand", max_length=30)
    areas: list[GapRequest] = Field(min_length=1, max_length=10)


@router.get("/sources/gaps/{domain}")
def source_gaps(
    domain: str, user: CurrentSuperuser, session: SessionDep, target: str = "understand"
) -> dict:
    """Базовые области цели, которых нет в графе: готовый запрос и ход разбора их документов."""
    return gap_search.gaps(session, user.id, domain, target)


@router.post("/sources/gaps/{domain}/fill", status_code=status.HTTP_202_ACCEPTED)
def fill_source_gaps(
    domain: str, body: GapFillIn, user: CurrentSuperuser, session: SessionDep
) -> dict:
    """Найти по белому списку и поставить в разбор источники для выбранных пробелов."""
    try:
        results = gap_search.fill(
            session,
            user.id,
            domain,
            body.target,
            [r.model_dump() for r in body.areas],
        )
    except gap_search.GapError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
    session.commit()
    return {"results": results}
