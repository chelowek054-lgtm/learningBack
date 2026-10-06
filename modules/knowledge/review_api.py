"""API специалистов и очереди проверки (T-0081, R-0046).

Назначать специалистов может только администратор. Очередь, источники понятий и решения доступны
администратору и специалисту в его областях; учащемуся — ничего из этого.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from core.deps import CurrentSuperuser, CurrentUser, SessionDep
from modules.knowledge import provenance, review
from modules.knowledge.models import Concept, ConceptConflict, ConceptEdge

router = APIRouter(tags=["review"])


class SpecialistIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    domain: str = Field(min_length=1, max_length=200)


class EditIn(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    summary: str | None = Field(default=None, max_length=4000)
    note: str | None = Field(default=None, max_length=500)


class ResolveIn(BaseModel):
    resolution: str
    note: str | None = Field(default=None, max_length=500)


def _uuid(raw: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{what} не найден") from e


@router.get("/specialists")
def list_specialists(_: CurrentSuperuser, session: SessionDep) -> list[dict]:
    return review.specialists(session)


@router.post("/specialists", status_code=status.HTTP_201_CREATED)
def grant_specialist(body: SpecialistIn, admin: CurrentSuperuser, session: SessionDep) -> dict:
    try:
        row = review.grant(session, admin, body.email, body.domain)
    except review.SpecialistError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
    session.commit()
    return {"id": str(row.id), "domain": row.domain}


@router.delete("/specialists/{specialist_id}", status_code=status.HTTP_204_NO_CONTENT)
def revoke_specialist(specialist_id: str, _: CurrentSuperuser, session: SessionDep) -> None:
    if not review.revoke(session, _uuid(specialist_id, "specialist")):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "specialist не найден")
    session.commit()


@router.get("/review/queue")
def review_queue(
    user: CurrentUser,
    session: SessionDep,
    domain: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=review.MAX_QUEUE),
) -> dict:
    return review.queue(session, user, domain, limit)


@router.post("/canon/nodes/{concept_id}/edit")
def edit_node(concept_id: str, body: EditIn, user: CurrentUser, session: SessionDep) -> dict:
    concept = session.get(Concept, _uuid(concept_id, "concept"))
    if concept is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "concept не найден")
    try:
        review.edit_concept(
            session, concept, user, title=body.title, summary=body.summary, note=body.note
        )
    except provenance.ProvenanceError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
    session.commit()
    return {"id": str(concept.id), "title": concept.title, "status": concept.status}


@router.post("/review/conflicts/{conflict_id}/resolve")
def resolve_conflict(
    conflict_id: str, body: ResolveIn, user: CurrentUser, session: SessionDep
) -> dict:
    conflict = session.get(ConceptConflict, _uuid(conflict_id, "conflict"))
    if conflict is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conflict не найден")
    try:
        result = review.resolve_conflict(session, conflict, user, body.resolution, body.note)
    except provenance.ProvenanceError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
    session.commit()
    return result


__all__ = ["ConceptEdge", "router"]
