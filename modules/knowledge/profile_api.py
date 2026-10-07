"""API профиля навыка (T-0088): построить, прочитать, поправить, подтвердить."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from core.ai_gateway import get_ai_gateway, has_llm
from core.deps import CurrentUser, SessionDep
from modules.knowledge import profile_coverage, profile_store

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


@router.post("/profile/{domain}/start", status_code=status.HTTP_202_ACCEPTED)
def start_profile_outline(domain: str, user: CurrentUser, session: SessionDep) -> dict:
    """Быстрый контур навыка по цели: области и этапы. Дальше человек правит его и просит «собрать карту»."""
    try:
        row, job = profile_store.request(
            session, user.id, domain, phase=profile_store.PHASE_OUTLINE
        )
    except profile_store.ProfileError as e:
        raise _fail(e) from e
    job_id = job.id
    session.commit()
    profile_store.dispatch(job_id)
    session.refresh(row)
    return profile_store.view(row)


@router.post("/profile/{domain}/fill", status_code=status.HTTP_202_ACCEPTED)
def fill_profile_graph(domain: str, user: CurrentUser, session: SessionDep) -> dict:
    """Подтвердить контур и собрать карту в фоне: понятия по областям параллельно, затем граф."""
    try:
        row, job = profile_store.request_fill(session, user.id, domain)
    except profile_store.ProfileError as e:
        raise _fail(e) from e
    job_id = job.id
    session.commit()
    profile_store.dispatch(job_id)
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


@router.post("/profile/{domain}/match")
def match_profile(domain: str, user: CurrentUser, session: SessionDep) -> dict:
    """Сопоставить профиль с графом по близости: что уже есть, что спорно, что новое."""
    row = profile_store.get(session, user.id, domain)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "профиль не построен")
    try:
        gateway = get_ai_gateway() if has_llm() else None
        profile_store.match(session, row, gateway)
    except profile_store.ProfileError as e:
        raise _fail(e) from e
    session.commit()
    return profile_store.view(row)


@router.get("/profile/{domain}/coverage")
def profile_coverage_report(domain: str, user: CurrentUser, session: SessionDep) -> dict:
    """Чего из профиля в графе уже есть; разбивка по источникам — только администратору."""
    row = profile_store.get(session, user.id, domain)
    if row is None or not row.profile:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "профиль не построен")
    return profile_coverage.report(session, row, admin=user.is_superuser)


@router.post("/profile/{domain}/build")
def build_graph(domain: str, user: CurrentUser, session: SessionDep) -> dict:
    """Построить скелет графа по профилю: области, этапы, понятия, связи; что уже есть — не дублируется."""
    row = profile_store.get(session, user.id, domain)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "профиль не построен")
    try:
        gateway = get_ai_gateway() if has_llm() else None
        report = profile_store.build_graph(session, row, gateway)
    except profile_store.ProfileError as e:
        raise _fail(e) from e
    session.commit()
    return {**profile_store.view(row), "build": report}


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
