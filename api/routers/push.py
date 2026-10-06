"""Устройства для push-уведомлений (T-0086): регистрация, отключение, удаление.

Регистрация токена — это согласие человека на push; отключить можно в настройках, не удаляя устройство.
"""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from core import push
from core.deps import CurrentUser, SessionDep

router = APIRouter(prefix="/push", tags=["push"])


class DeviceIn(BaseModel):
    token: str = Field(min_length=10, max_length=300)
    platform: str = Field(max_length=20)


class TokenIn(BaseModel):
    token: str = Field(min_length=1, max_length=300)


class EnabledIn(BaseModel):
    enabled: bool


@router.get("")
def push_state(user: CurrentUser, session: SessionDep) -> dict:
    """Сколько у человека устройств, включён ли push и доступен ли канал на сервере."""
    return push.state(session, user.id)


@router.post("/devices", status_code=status.HTTP_201_CREATED)
def register_device(body: DeviceIn, user: CurrentUser, session: SessionDep) -> dict:
    try:
        push.register(session, user.id, body.token, body.platform)
    except push.PushError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
    session.commit()
    return push.state(session, user.id)


@router.post("/devices/remove")
def remove_device(body: TokenIn, user: CurrentUser, session: SessionDep) -> dict:
    """Выход из аккаунта на устройстве: токен удаляется, чтобы чужой вход не получал чужие уведомления."""
    removed = push.unregister(session, user.id, body.token)
    session.commit()
    return {"removed": removed}


@router.post("/enabled")
def set_enabled(body: EnabledIn, user: CurrentUser, session: SessionDep) -> dict:
    """Настройка «присылать уведомления»: выключенное не отправляется ни на одно устройство."""
    push.set_enabled(session, user.id, body.enabled)
    session.commit()
    return push.state(session, user.id)
