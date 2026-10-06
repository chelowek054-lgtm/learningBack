"""Админский API происхождения знаний (T-0076, R-0045): источники, проверка, удаление источника.

Всё здесь только для администратора: учащийся не получает ни документов, ни ссылок, ни текстов
фрагментов. Статус «черновик» или «проверено» он видит в курсе, но не отсюда.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from core.deps import CurrentSuperuser, SessionDep
from modules.knowledge import provenance
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
def node_sources(concept_id: str, _: CurrentSuperuser, session: SessionDep) -> dict:
    cid = _uuid(concept_id, "concept")
    concept = session.get(Concept, cid)
    if concept is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "concept не найден")
    return {
        "status": concept.status,
        "sources": provenance.concept_sources(session, cid),
        "history": provenance.history(session, "concept", cid),
    }


@router.get("/canon/edges/{edge_id}/sources")
def edge_sources(edge_id: str, _: CurrentSuperuser, session: SessionDep) -> dict:
    eid = _uuid(edge_id, "edge")
    edge = session.get(ConceptEdge, eid)
    if edge is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "edge не найден")
    return {
        "status": edge.status,
        "sources": provenance.edge_sources(session, eid),
        "history": provenance.history(session, "edge", eid),
    }


@router.post("/canon/nodes/{concept_id}/review")
def review_node(
    concept_id: str, body: ReviewIn, user: CurrentSuperuser, session: SessionDep
) -> dict:
    concept = session.get(Concept, _uuid(concept_id, "concept"))
    if concept is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "concept не найден")
    try:
        provenance.review_concept(session, concept, user.id, body.action, body.note)
    except provenance.ProvenanceError as e:
        raise _fail(e) from e
    session.commit()
    return {"id": str(concept.id), "status": concept.status}


@router.post("/canon/edges/{edge_id}/review")
def review_edge(edge_id: str, body: ReviewIn, user: CurrentSuperuser, session: SessionDep) -> dict:
    edge = session.get(ConceptEdge, _uuid(edge_id, "edge"))
    if edge is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "edge не найден")
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
