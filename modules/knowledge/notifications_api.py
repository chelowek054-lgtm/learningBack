"""API уведомлений о курсе (T-0083): список своих и отметка «прочитано»."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from core.deps import CurrentUser, SessionDep
from modules.knowledge import notifications

router = APIRouter(tags=["notifications"])


class ReadIn(BaseModel):
    ids: list[str] | None = Field(default=None, max_length=100)


@router.get("/notifications")
def my_notifications(
    user: CurrentUser,
    session: SessionDep,
    unread: bool = Query(default=True),
    limit: int = Query(default=50, ge=1, le=100),
) -> list[dict]:
    return notifications.listing(session, user.id, unread, limit)


@router.post("/notifications/read")
def read_notifications(body: ReadIn, user: CurrentUser, session: SessionDep) -> dict:
    ids = None
    if body.ids is not None:
        ids = []
        for raw in body.ids:
            try:
                ids.append(uuid.UUID(raw))
            except ValueError:
                continue
    n = notifications.mark_read(session, user.id, ids)
    session.commit()
    return {"marked": n}
