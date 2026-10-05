"""Управление модулями: список и жизненный цикл (C-0001, T-0051) — только администратор."""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from core import modules
from core.deps import CurrentSuperuser, SessionDep

router = APIRouter(prefix="/modules", tags=["modules"])

_CODES = {
    "unknown_module": status.HTTP_404_NOT_FOUND,
    "not_installed": status.HTTP_409_CONFLICT,
    "dependency_disabled": status.HTTP_409_CONFLICT,
    "has_dependents": status.HTTP_409_CONFLICT,
    "still_enabled": status.HTTP_409_CONFLICT,
    "purge_unsupported": status.HTTP_409_CONFLICT,
    "confirmation_required": status.HTTP_400_BAD_REQUEST,
}


def _fail(e: modules.LifecycleError) -> HTTPException:
    return HTTPException(
        _CODES.get(e.code, status.HTTP_409_CONFLICT), {"code": e.code, "detail": str(e)}
    )


@router.get("")
def list_modules(_: CurrentSuperuser, session: SessionDep) -> list[dict]:
    """Подключённые модули: манифест, версия, включён ли."""
    return modules.module_status(session)


@router.post("/{module_id}/enable")
def enable_module(module_id: str, _: CurrentSuperuser, session: SessionDep) -> dict:
    try:
        modules.set_enabled(session, module_id, True)
    except modules.LifecycleError as e:
        raise _fail(e) from e
    session.commit()
    return {"id": module_id, "enabled": True}


@router.post("/{module_id}/approve")
def approve_module(module_id: str, _: CurrentSuperuser, session: SessionDep) -> dict:
    """Согласиться на запросы и сеть модуля (после установки или обновления) и включить его."""
    try:
        granted = modules.approve(session, module_id)
    except modules.LifecycleError as e:
        raise _fail(e) from e
    session.commit()
    return {"id": module_id, "approved": granted, "enabled": True}


@router.post("/{module_id}/disable")
def disable_module(module_id: str, _: CurrentSuperuser, session: SessionDep) -> dict:
    """Отключить: маршруты отвечают 503, рубрики и стартовый контент не выдаются, данные целы."""
    try:
        modules.set_enabled(session, module_id, False)
    except modules.LifecycleError as e:
        raise _fail(e) from e
    session.commit()
    return {"id": module_id, "enabled": False}


class UninstallIn(BaseModel):
    confirm: bool = False


@router.post("/{module_id}/uninstall")
def uninstall_module(
    module_id: str, body: UninstallIn, _: CurrentSuperuser, session: SessionDep
) -> dict:
    """Удалить данные модуля. Необратимо: нужен `confirm`, и модуль должен быть отключён."""
    try:
        modules.uninstall(session, module_id, confirm=body.confirm)
    except modules.LifecycleError as e:
        raise _fail(e) from e
    session.commit()
    return {"id": module_id, "uninstalled": True}
