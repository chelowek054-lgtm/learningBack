"""API профиля навыка (T-0088): построить, прочитать, поправить, подтвердить."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from core.deps import CurrentUser, SessionDep
from modules.knowledge import profile_store

router = APIRouter(tags=["skill-profile"])


class ProfileIn(BaseModel):
    skill: str = ""
    level: str | None = None
    areas: list[dict[str, Any]]


def _fail(e: profile_store.ProfileError) -> HTTPException:
    code = status.HTTP_409_CONFLICT if e.code in ("already_building", "building") else 422
    return HTTPException(code, str(e))


@router.post("/profile/{domain}", status_code=status.HTTP_202_ACCEPTED)
def build_profile(domain: str, user: CurrentUser, session: SessionDep) -> dict:
    """Начать построение профиля навыка по подтверждённой цели; ход видно через GET."""
    try:
        row, job = profile_store.request(session, user.id, domain)
    except profile_store.ProfileError as e:
        raise _fail(e) from e
    session.commit()
    profile_store.run_now_if_inline(session, job)
    session.commit()
    session.refresh(row)
    return profile_store.view(row)


@router.get("/profile/{domain}")
def read_profile(domain: str, user: CurrentUser, session: SessionDep) -> dict:
    return profile_store.view(profile_store.get(session, user.id, domain))


@router.put("/profile/{domain}")
def edit_profile(domain: str, body: ProfileIn, user: CurrentUser, session: SessionDep) -> dict:
    row = profile_store.get(session, user.id, domain)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "профиль не построен")
    try:
        profile_store.save_edit(session, row, body.model_dump())
    except profile_store.ProfileError as e:
        raise _fail(e) from e
    session.commit()
    return profile_store.view(row)


@router.post("/profile/{domain}/confirm")
def confirm_profile(domain: str, user: CurrentUser, session: SessionDep) -> dict:
    row = profile_store.get(session, user.id, domain)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "профиль не построен")
    try:
        profile_store.confirm(session, row)
    except profile_store.ProfileError as e:
        raise _fail(e) from e
    session.commit()
    return profile_store.view(row)
