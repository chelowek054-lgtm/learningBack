"""Расход токенов LLM — только для администратора (FR-AI-05)."""

from datetime import datetime

from fastapi import APIRouter

from core import usage
from core.deps import CurrentSuperuser, SessionDep

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("/summary")
def usage_summary(
    _: CurrentSuperuser,
    session: SessionDep,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[dict]:
    """Токены за период по пользователю и назначению вызова."""
    return usage.summary(session, since, until)
