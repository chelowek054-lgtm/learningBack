"""Выдача контента (материалы, рубрики). WS4/WS2 — защищено токеном."""

from fastapi import APIRouter

from core.deps import CurrentUser, SessionDep
from core.models import Material

router = APIRouter(prefix="/content", tags=["content"])


@router.get("/materials")
def list_materials(user: CurrentUser, session: SessionDep) -> list[dict]:
    rows = (
        session.query(Material)
        .filter((Material.user_id == user.id) | (Material.user_id.is_(None)))
        .all()
    )
    return [
        {"id": str(m.id), "module": m.module, "title": m.title, "content": m.content}
        for m in rows
    ]
