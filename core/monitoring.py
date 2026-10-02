"""Мониторинг (T-0050): сводка здоровья и алерты по уже накопленным данным.

Отдельной аналитики нет: доля упавших задач читается из `job`, расход токенов — из
`llm_usage`, ошибки клиента — из `client_error`. Алерт здесь — не письмо, а состояние
в ответе: `GET /v1/monitoring` отдаёт `status: "ok" | "alert"`, и внешний монитор
(uptime-сервис, cron) опрашивает его и поднимает тревогу сам. Так не нужен ни планировщик
внутри API, ни почтовый канал, которого пока нет.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from core.config import settings
from core.models import ClientError, Job, LlmUsage


def _since(hours: int, now: datetime | None = None) -> datetime:
    return (now or datetime.now(timezone.utc)) - timedelta(hours=hours)


def jobs_health(session: Session, now: datetime | None = None) -> dict[str, Any]:
    """Доля задач в `failed` среди завершённых за окно.

    Незавершённые (pending/running) не считаются: они ещё не успели ни упасть, ни
    получиться, и в знаменателе делали бы долю заниженной.
    """
    since = _since(settings.alert_window_hours, now)
    rows = dict(
        session.query(Job.status, func.count())
        .filter(Job.created_at >= since, Job.status.in_(("done", "failed")))
        .group_by(Job.status)
        .all()
    )
    done, failed = rows.get("done", 0), rows.get("failed", 0)
    total = done + failed
    ratio = failed / total if total else 0.0
    # Малая выборка не тревожит: одна упавшая из двух — 50%, но это не авария.
    alert = total >= settings.alert_min_jobs and ratio >= settings.alert_failed_jobs_ratio
    return {
        "windowHours": settings.alert_window_hours,
        "finished": total,
        "failed": failed,
        "ratio": round(ratio, 4),
        "threshold": settings.alert_failed_jobs_ratio,
        "alert": alert,
    }


def tokens_health(session: Session, now: datetime | None = None) -> dict[str, Any]:
    """Расход токенов за окно против лимита; лимит 0 — не задан, алерт выключен."""
    since = _since(settings.alert_window_hours, now)
    used = (
        session.query(
            func.coalesce(func.sum(LlmUsage.prompt_tokens + LlmUsage.completion_tokens), 0)
        )
        .filter(LlmUsage.created_at >= since)
        .scalar()
    )
    limit = settings.alert_tokens_per_window
    return {
        "windowHours": settings.alert_window_hours,
        "used": int(used),
        "limit": limit or None,
        "alert": bool(limit) and used >= limit,
    }


def client_errors_health(session: Session, now: datetime | None = None) -> dict[str, Any]:
    since = _since(settings.alert_window_hours, now)
    count = (
        session.query(func.count(ClientError.id)).filter(ClientError.created_at >= since).scalar()
    )
    limit = settings.alert_client_errors
    return {
        "windowHours": settings.alert_window_hours,
        "count": int(count),
        "limit": limit or None,
        "alert": bool(limit) and count >= limit,
    }


def snapshot(session: Session, now: datetime | None = None) -> dict[str, Any]:
    jobs = jobs_health(session, now)
    tokens = tokens_health(session, now)
    errors = client_errors_health(session, now)
    alerts = [
        name
        for name, part in (("failed_jobs", jobs), ("tokens", tokens), ("client_errors", errors))
        if part["alert"]
    ]
    return {
        "status": "alert" if alerts else "ok",
        "alerts": alerts,
        "jobs": jobs,
        "tokens": tokens,
        "clientErrors": errors,
    }


# Поля ошибки клиента режутся: стек приходит с устройства, и размер ему не гарантирован.
MAX_MESSAGE = 500
MAX_STACK = 8000
MAX_CONTEXT_KEYS = 20


def clip(text: str | None, limit: int) -> str | None:
    if text is None:
        return None
    return text if len(text) <= limit else text[:limit] + "…"
