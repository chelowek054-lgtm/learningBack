"""Моя библиотека: предметы человека со статусом (T-0119, R-0062)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from core.deps import CurrentUser, SessionDep
from modules.knowledge.models import UserSubject

router = APIRouter(tags=["subjects"])
STATUSES = ("building", "active", "paused", "reached")


class SubjectIn(BaseModel):
    domain: str
    title: str
    target: str = "understand"


class SubjectPatch(BaseModel):
    status: str | None = None
    target: str | None = None


def _view(s: UserSubject) -> dict:
    return {
        "id": str(s.id),
        "domain": s.domain,
        "title": s.title,
        "target": s.target,
        "status": s.status,
    }


def _own(session, user, subject_id: str) -> UserSubject:
    try:
        key = uuid.UUID(subject_id)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Предмет не найден") from None
    row = session.get(UserSubject, key)
    if row is None or row.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Предмет не найден")
    return row


@router.get("/subjects")
def list_subjects(user: CurrentUser, session: SessionDep) -> list[dict]:
    rows = (
        session.query(UserSubject)
        .filter(UserSubject.user_id == user.id)
        .order_by(UserSubject.created_at)
        .all()
    )
    return [_view(r) for r in rows]


@router.post("/subjects", status_code=status.HTTP_201_CREATED)
def add_subject(body: SubjectIn, user: CurrentUser, session: SessionDep) -> dict:
    row = session.query(UserSubject).filter_by(user_id=user.id, domain=body.domain).one_or_none()
    if row is None:
        row = UserSubject(user_id=user.id, domain=body.domain, title=body.title, target=body.target)
        session.add(row)
        session.commit()
    return _view(row)


@router.patch("/subjects/{subject_id}")
def patch_subject(
    subject_id: str, body: SubjectPatch, user: CurrentUser, session: SessionDep
) -> dict:
    row = _own(session, user, subject_id)
    if body.status is not None:
        if body.status not in STATUSES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Неизвестный статус")
        row.status = body.status
    if body.target is not None:
        row.target = body.target
    session.commit()
    return _view(row)


@router.delete("/subjects/{subject_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_subject(subject_id: str, user: CurrentUser, session: SessionDep) -> None:
    session.delete(_own(session, user, subject_id))
    session.commit()
