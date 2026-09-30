"""Учёт расхода токенов LLM (FR-AI-05, NFR-09).

Gateway пишет строку `llm_usage` на каждый ответ провайдера. Кто платит, он не
знает: пользователя берёт из контекстной переменной, которую ставит ASGI-слой
по токену запроса. Запись идёт отдельной короткой сессией и никогда не ломает
сам вызов модели: учёт — побочный эффект, а не условие ответа.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from contextvars import ContextVar
from datetime import datetime
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from core.db import SessionLocal
from core.models import LlmUsage
from core.security import decode_access_token

log = logging.getLogger(__name__)

_current_user: ContextVar[uuid.UUID | None] = ContextVar("llm_usage_user", default=None)


def set_user(user_id: uuid.UUID | None):
    return _current_user.set(user_id)


def reset_user(token) -> None:
    _current_user.reset(token)


def record(
    *,
    model: str,
    purpose: str,
    usage: dict[str, Any] | None,
    session_factory: Callable[[], Session] | None = None,
) -> None:
    """Записать расход одного ответа провайдера. Ошибки учёта глотаем."""
    usage = usage or {}
    try:
        with (session_factory or SessionLocal)() as session:
            session.add(
                LlmUsage(
                    user_id=_current_user.get(),
                    purpose=purpose,
                    model=model,
                    prompt_tokens=int(usage.get("prompt_tokens") or 0),
                    completion_tokens=int(usage.get("completion_tokens") or 0),
                )
            )
            session.commit()
    except Exception:  # noqa: BLE001
        log.warning("Не удалось записать расход токенов", exc_info=True)


def summary(
    session: Session, since: datetime | None = None, until: datetime | None = None
) -> list[dict[str, Any]]:
    """Расход по паре (пользователь, назначение) за период."""
    q = session.query(
        LlmUsage.user_id,
        LlmUsage.purpose,
        func.count().label("calls"),
        func.coalesce(func.sum(LlmUsage.prompt_tokens), 0).label("prompt_tokens"),
        func.coalesce(func.sum(LlmUsage.completion_tokens), 0).label("completion_tokens"),
    )
    if since is not None:
        q = q.filter(LlmUsage.created_at >= since)
    if until is not None:
        q = q.filter(LlmUsage.created_at < until)
    rows = q.group_by(LlmUsage.user_id, LlmUsage.purpose).order_by(LlmUsage.purpose).all()
    return [
        {
            "userId": str(r.user_id) if r.user_id else None,
            "purpose": r.purpose,
            "calls": r.calls,
            "promptTokens": int(r.prompt_tokens),
            "completionTokens": int(r.completion_tokens),
        }
        for r in rows
    ]


class UserContextMiddleware:
    """Кладёт пользователя из Bearer-токена в контекст на время запроса."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        user_id = None
        for name, value in scope.get("headers", []):
            if name == b"authorization" and value[:7].lower() == b"bearer ":
                sub = decode_access_token(value[7:].decode("latin-1"))
                try:
                    user_id = uuid.UUID(sub) if sub else None
                except ValueError:
                    user_id = None
        token = set_user(user_id)
        try:
            await self.app(scope, receive, send)
        finally:
            reset_user(token)
