"""Мониторинг: приём ошибок клиента и сводка со статусом алертов (T-0050)."""

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from core import monitoring
from core.deps import CurrentSuperuser, CurrentUser, SessionDep
from core.models import ClientError
from core.ratelimit import SlidingWindowLimiter

router = APIRouter(tags=["monitoring"])

# Сбойное приложение может сыпать одну и ту же ошибку в цикле: лимит на человека,
# чтобы такой цикл не превратился в нагрузку на сервер и в мусор в таблице.
CLIENT_ERROR_LIMIT = 30
CLIENT_ERROR_WINDOW_SECONDS = 60
_limiter = SlidingWindowLimiter()


class ClientErrorIn(BaseModel):
    message: str = Field(min_length=1)
    stack: str | None = None
    app_version: str | None = Field(None, alias="appVersion")
    fatal: bool = False
    context: dict[str, Any] = Field(default_factory=dict)

    model_config = {"populate_by_name": True}


@router.post("/client-errors", status_code=status.HTTP_202_ACCEPTED)
def report_client_error(body: ClientErrorIn, user: CurrentUser, session: SessionDep) -> dict:
    """Необработанная ошибка клиента. Принимается только от вошедшего человека."""
    if not _limiter.allow(str(user.id), CLIENT_ERROR_LIMIT, CLIENT_ERROR_WINDOW_SECONDS):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много сообщений об ошибках")
    context = dict(list(body.context.items())[: monitoring.MAX_CONTEXT_KEYS])
    session.add(
        ClientError(
            user_id=user.id,
            app_version=monitoring.clip(body.app_version, 40),
            message=monitoring.clip(body.message, monitoring.MAX_MESSAGE) or "",
            stack=monitoring.clip(body.stack, monitoring.MAX_STACK),
            fatal=body.fatal,
            context=context,
        )
    )
    session.commit()
    return {"status": "accepted"}


@router.get("/monitoring")
def monitoring_snapshot(_: CurrentSuperuser, session: SessionDep) -> dict:
    """Здоровье системы: `status` и список сработавших алертов — для внешнего монитора."""
    return monitoring.snapshot(session)
