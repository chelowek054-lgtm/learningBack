"""Фоновый воркер AI-задач (T-0049, R-0023, R-0025, A-0007).

Долгие задачи (распознавание речи, генерация) не помещаются в запрос `/sync/push`. В режиме
`jobs_mode=worker` push только ставит задачу в очередь, а этот процесс берёт её отсюда:

- задачу берёт `SELECT ... FOR UPDATE SKIP LOCKED`, поэтому несколько воркеров не берут одну и ту же;
- каждая задача — в своей транзакции: упал воркер посреди работы — строка откатилась и осталась в очереди;
- брошенное в `running` (воркер убит между коммитами) возвращается в очередь по таймауту;
- ошибки и повторы — те же, что при обработке на push (core.jobs.process_job).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_
from sqlalchemy.orm import Session

from core import usage
from core.ai_gateway import AIGateway, get_ai_gateway
from core.config import settings
from core.db import SessionLocal
from core.jobs import process_job
from core.models import Job

log = logging.getLogger(__name__)


def claim_next(session: Session, now: datetime | None = None) -> Job | None:
    """Взять следующую готовую задачу с блокировкой строки; занятые другим воркером пропускаются."""
    now = now or datetime.now(timezone.utc)
    return (
        session.query(Job)
        .filter(Job.status == "pending", or_(Job.retry_after.is_(None), Job.retry_after <= now))
        .order_by(Job.created_at)
        .with_for_update(skip_locked=True)
        .first()
    )


def requeue_stale(session: Session, now: datetime | None = None) -> int:
    """Вернуть в очередь задачи, застрявшие в running дольше `job_stale_minutes`."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=settings.job_stale_minutes)
    n = (
        session.query(Job)
        .filter(Job.status == "running", Job.updated_at < cutoff)
        .update({Job.status: "pending", Job.retry_after: None}, synchronize_session="fetch")
    )
    session.flush()
    return n


def work_one(session: Session, gateway: AIGateway, now: datetime | None = None) -> Job | None:
    """Взять и выполнить одну задачу; None — очередь пуста. Транзакцией управляет вызывающий."""
    job = claim_next(session, now)
    if job is None:
        return None
    # Расход токенов записывается на человека, чья это задача.
    token = usage.set_user(job.user_id)
    try:
        process_job(session, job, gateway)
    finally:
        usage.reset_user(token)
    session.flush()
    return job


def run_loop(
    gateway: AIGateway | None = None,
    *,
    session_factory: Callable[[], Session] = SessionLocal,
    poll_seconds: float = 2.0,
    should_stop: Callable[[], bool] = lambda: False,
) -> int:
    """Работать, пока не попросят остановиться; вернуть число выполненных задач."""
    gateway = gateway or get_ai_gateway()
    done = 0
    while not should_stop():
        try:
            with session_factory() as session:
                requeue_stale(session)
                job = work_one(session, gateway)
                session.commit()
        except Exception:  # noqa: BLE001 — воркер не должен падать из-за одной задачи
            log.exception("Сбой воркера, продолжаю")
            time.sleep(poll_seconds)
            continue
        if job is None:
            time.sleep(poll_seconds)
        else:
            done += 1
    return done
