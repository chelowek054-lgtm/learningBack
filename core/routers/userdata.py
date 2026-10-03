"""«Мои данные»: доступ модулей, журнал, выгрузка и удаление (T-0054, R-0031)."""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from core import modules, userdata
from core.deps import CurrentUser, SessionDep

router = APIRouter(prefix="/me/data", tags=["my-data"])


def _registry() -> dict[str, userdata.DataType]:
    return userdata.types(modules.enabled_modules())


class PermissionIn(BaseModel):
    module: str
    type: str
    mode: str
    granted: bool


class EraseIn(BaseModel):
    confirm: bool = False


@router.get("/types")
def data_types(_: CurrentUser) -> list[dict]:
    """Какие данные хранятся, кто ими владеет, зачем и сколько."""
    return [t.describe() for t in _registry().values()]


@router.get("/permissions")
def data_permissions(user: CurrentUser, session: SessionDep) -> list[dict]:
    return userdata.permissions(session, user.id, modules.enabled_modules())


@router.put("/permissions")
def set_data_permission(body: PermissionIn, user: CurrentUser, session: SessionDep) -> dict:
    """Выдать или отозвать разрешение модулю на тип данных."""
    module = next((m for m in modules.enabled_modules() if m.id == body.module), None)
    if module is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Модуль не найден")
    try:
        userdata.set_permission(session, user.id, module, body.type, body.mode, body.granted)
    except userdata.DataAccessError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
    session.commit()
    return {"module": body.module, "type": body.type, "mode": body.mode, "granted": body.granted}


@router.get("/access-log")
def data_access_log(user: CurrentUser, session: SessionDep, limit: int = 100) -> list[dict]:
    """Кто из модулей и когда обращался к данным человека."""
    return userdata.access_log(session, user.id, max(1, min(limit, 500)))


@router.get("/export")
def export_data(user: CurrentUser, session: SessionDep) -> dict:
    """Все данные человека одним документом."""
    return userdata.export_all(session, user.id, _registry())


@router.post("/erase")
def erase_data(body: EraseIn, user: CurrentUser, session: SessionDep) -> dict:
    """Удалить все данные человека; без явного подтверждения не делает ничего."""
    if not body.confirm:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Нужно подтверждение удаления")
    counts = userdata.erase_all(session, user.id, _registry())
    session.commit()
    return {"erased": counts}
