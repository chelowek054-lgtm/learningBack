"""Хранение и фоновое построение профиля навыка (T-0088): статусы, задача, правка и подтверждение."""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from core.ai_gateway import get_ai_gateway, has_llm
from core.config import settings
from core.models import Job
from modules.knowledge import (
    goal_intake,
    profile_build,
    profile_match,
    profile_sources,
    skill_profile,
)
from modules.knowledge.models import SkillProfile

log = logging.getLogger(__name__)

JOB_TYPE = "skill_profile"
BUILDING, DRAFT, CONFIRMED, FAILED = "building", "draft", "confirmed", "failed"


class ProfileError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def get(session: Session, user_id: uuid.UUID, domain: str) -> SkillProfile | None:
    return session.query(SkillProfile).filter_by(user_id=user_id, domain=domain).one_or_none()


def view(row: SkillProfile | None) -> dict[str, Any]:
    if row is None:
        return {"exists": False, "status": None, "profile": None}
    return {
        "exists": True,
        "status": row.status,
        "profile": row.profile or None,
        "concepts": skill_profile.concept_count(row.profile or {}),
        "error": row.error,
        "confirmedAt": row.confirmed_at.isoformat() if row.confirmed_at else None,
        "updatedAt": row.updated_at.isoformat() if row.updated_at else None,
        "stale": is_stale(row),
    }


def is_stale(row: SkillProfile) -> bool:
    """«Строится», но задача давно не двигалась: процесс перезапустили, и ждать нечего."""
    if row.status != BUILDING or row.updated_at is None:
        return False
    return datetime.now(timezone.utc) - row.updated_at > timedelta(
        minutes=settings.job_stale_minutes
    )


def request(
    session: Session, user_id: uuid.UUID, domain: str, *, with_graph: bool = False
) -> tuple[SkillProfile, Job]:
    """Поставить построение профиля по подтверждённой цели; прежний профиль заменяется заново.

    `with_graph` — после профиля та же задача строит и скелет графа (онбординг), без второго шага человека.
    """
    goal = goal_intake.get_confirmed(session, user_id, domain)
    if goal is None:
        raise ProfileError("goal_not_confirmed", "Сначала подтвердите цель")
    row = get(session, user_id, domain)
    if row is not None and row.status == BUILDING and not is_stale(row):
        raise ProfileError("already_building", "Профиль уже строится")
    if row is None:
        row = SkillProfile(user_id=user_id, domain=domain)
        session.add(row)
    row.status, row.error, row.confirmed_at = BUILDING, None, None
    row.updated_at = datetime.now(timezone.utc)
    job = Job(
        user_id=user_id,
        type=JOB_TYPE,
        status="pending",
        input_ref={"domain": domain, "graph": with_graph},
    )
    session.add(job)
    session.flush()
    return row, job


def profile_job(session: Session, job: Job, gateway: Any) -> dict[str, Any]:
    """Обработчик задачи: ValueError — навсегда (нет цели), остальное — повтор по правилам очереди."""
    domain = (job.input_ref or {}).get("domain")
    row = get(session, job.user_id, domain) if domain else None
    goal = goal_intake.get_confirmed(session, job.user_id, domain) if domain else None
    if row is None or goal is None:
        if row is not None:
            row.status, row.error = FAILED, "Нет подтверждённой цели"
            session.flush()
        raise ValueError("Нет подтверждённой цели или профиля")
    try:
        summary = goal.summary
        profile = skill_profile.build_profile(
            summary.get("area") or domain,
            goal_intake.as_goal_text(summary),
            summary.get("level"),
        )
    except Exception as exc:  # noqa: BLE001
        # Попытки исчерпаны — человеку видно «не получилось», а не вечная «строится».
        if job.attempts >= settings.job_max_attempts:
            row.status, row.error = FAILED, str(exc)[:300]
            session.flush()
        raise
    with_graph = bool((job.input_ref or {}).get("graph"))
    # Со скелетом статус остаётся «строится» до его конца: иначе экран на секунды видит «черновик» и бросает ход.
    row.profile, row.status, row.error = profile, BUILDING if with_graph else DRAFT, None
    row.updated_at = datetime.now(timezone.utc)
    session.flush()
    result = {"areas": len(profile["areas"]), "concepts": skill_profile.concept_count(profile)}
    if with_graph:
        # Профиль сохраняем до скелета: сбой графа не должен стирать минуты работы модели.
        session.commit()
        try:
            with session.begin_nested():
                report = build_graph(
                    session, row, gateway if has_llm() else None, allow_building=True
                )
            result["graph"] = {"created": report["created"], "reused": report["reused"]}
        except Exception as exc:  # noqa: BLE001 — профиль цел, человек увидит причину и повторит
            log.warning("Граф по профилю не построен", exc_info=True)
            row.status, row.error = FAILED, f"Профиль готов, граф не построился: {str(exc)[:200]}"
            session.flush()
    return result


def dispatch(job_id: uuid.UUID) -> None:
    """Запустить задачу в фоне, не держа запрос: в inline-режиме — потоком, иначе её возьмёт воркер."""
    if settings.jobs_mode == "inline" and settings.jobs_inline_thread:
        threading.Thread(target=_run_in_thread, args=(job_id,), daemon=True).start()


def _run_in_thread(job_id: uuid.UUID) -> None:
    """Исполнить задачу своей сессией; временные сбои повторяются с отсрочкой, как в очереди."""
    from core.db import SessionLocal
    from core.jobs import process_job

    try:
        with SessionLocal() as session:
            job = session.get(Job, job_id)
            gateway = get_ai_gateway()
            while job is not None and job.status == "pending":
                process_job(session, job, gateway)
                session.commit()
                if job.status == "pending" and job.retry_after is not None:
                    time.sleep(
                        max(0.0, (job.retry_after - datetime.now(timezone.utc)).total_seconds())
                    )
    except Exception:  # noqa: BLE001 — поток не должен падать молча без следа
        log.exception("Фоновая задача профиля упала")


def run_now_if_inline(session: Session, job: Job) -> None:
    """Dev-режим: задачи идут при синхронизации, а профиль нужен сразу, поэтому исполняем на запросе."""
    if settings.jobs_mode == "inline":
        from core.jobs import process_job

        process_job(session, job, get_ai_gateway())


def save_edit(session: Session, row: SkillProfile, raw: Any) -> SkillProfile:
    """Правка человека: профиль чистится теми же правилами, подтверждение сбрасывается."""
    if row.status == BUILDING:
        raise ProfileError("building", "Профиль ещё строится")
    cleaned = skill_profile.clean_profile(raw)
    if not cleaned["areas"]:
        raise ProfileError("empty", "В профиле не осталось ни одной области")
    row.profile, row.status, row.confirmed_at = cleaned, DRAFT, None
    row.updated_at = datetime.now(timezone.utc)
    session.flush()
    return row


def match(
    session: Session, row: SkillProfile, gateway: Any = None, *, allow_building: bool = False
) -> dict[str, Any]:
    """Сопоставить профиль с графом и сохранить решения в профиле: они видны человеку и потом строят граф."""
    if (row.status == BUILDING and not allow_building) or not row.profile:
        raise ProfileError("not_ready", "Профиль ещё не построен")
    decisions = profile_match.match_profile(session, row.profile, gateway)
    row.profile = {**row.profile, "match": decisions}
    row.updated_at = datetime.now(timezone.utc)
    session.flush()
    return decisions


def _queue_sources(session: Session, row: SkillProfile, report: dict[str, Any]) -> list[str]:
    """Для каждой новой области скелета поставить в фон поиск источников; вернуть области, по которым поставлен."""
    areas = {a["key"]: a for a in row.profile.get("areas", [])}
    queued: list[str] = []
    for item in report["areas"]:
        area = areas.get(item["key"])
        if area is None or not item["new"] or not item["created"]:
            continue
        queries = profile_sources.queries_for(
            area["title"],
            [s["title"] for s in area["stages"]],
            [c["title"] for c in area["concepts"]],
        )
        if profile_sources.enqueue(session, row.user_id, item["domain"], queries):
            queued.append(item["domain"])
    return queued


def build_graph(
    session: Session, row: SkillProfile, gateway: Any = None, *, allow_building: bool = False
) -> dict[str, Any]:
    """Построить скелет графа по профилю: сопоставить (если ещё не), завести области и понятия."""
    if (row.status == BUILDING and not allow_building) or not row.profile:
        raise ProfileError("not_ready", "Профиль ещё не построен")
    if not (row.profile.get("match") or {}).get("areas"):
        match(session, row, gateway, allow_building=allow_building)
    report = profile_build.build_skeleton(
        session, row.domain, row.profile, row.profile.get("match")
    )
    row.status, row.confirmed_at = CONFIRMED, datetime.now(timezone.utc)
    session.flush()
    report["sources"] = _queue_sources(session, row, report)
    return report


def profile_from_goal(session: Session, user_id: uuid.UUID, domain: str) -> SkillProfile:
    """Профиль по подтверждённой цели (долгий запрос к модели); граф по нему строится отдельным шагом."""
    goal = goal_intake.get_confirmed(session, user_id, domain)
    if goal is None:
        raise ProfileError("goal_not_confirmed", "Сначала подтвердите цель")
    summary = goal.summary
    row = get(session, user_id, domain)
    if row is None:
        row = SkillProfile(user_id=user_id, domain=domain)
        session.add(row)
    row.profile = skill_profile.build_profile(
        summary.get("area") or domain, goal_intake.as_goal_text(summary), summary.get("level")
    )
    row.status, row.error = DRAFT, None
    session.flush()
    return row


def build_from_goal(
    session: Session, user_id: uuid.UUID, domain: str, gateway: Any = None
) -> dict[str, Any]:
    """Профиль и скелет графа одним вызовом (тесты, скрипты); запрос онбординга делит их на два шага."""
    return build_graph(session, profile_from_goal(session, user_id, domain), gateway)


def confirm(session: Session, row: SkillProfile) -> SkillProfile:
    if row.status not in (DRAFT, CONFIRMED) or not row.profile:
        raise ProfileError("not_ready", "Подтверждать пока нечего")
    row.status, row.confirmed_at = CONFIRMED, datetime.now(timezone.utc)
    session.flush()
    return row
