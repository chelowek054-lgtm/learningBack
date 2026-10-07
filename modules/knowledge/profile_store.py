"""Хранение и фоновое построение профиля навыка (T-0088): статусы, задача, правка и подтверждение."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

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
    }


def request(session: Session, user_id: uuid.UUID, domain: str) -> tuple[SkillProfile, Job]:
    """Поставить построение профиля по подтверждённой цели; прежний профиль заменяется заново."""
    goal = goal_intake.get_confirmed(session, user_id, domain)
    if goal is None:
        raise ProfileError("goal_not_confirmed", "Сначала подтвердите цель")
    row = get(session, user_id, domain)
    if row is not None and row.status == BUILDING:
        raise ProfileError("already_building", "Профиль уже строится")
    if row is None:
        row = SkillProfile(user_id=user_id, domain=domain)
        session.add(row)
    row.status, row.error, row.confirmed_at = BUILDING, None, None
    row.updated_at = datetime.now(timezone.utc)
    job = Job(user_id=user_id, type=JOB_TYPE, status="pending", input_ref={"domain": domain})
    session.add(job)
    session.flush()
    return row, job


def profile_job(session: Session, job: Job, gateway: Any) -> dict[str, Any]:
    """Обработчик задачи: ValueError — навсегда (нет цели), остальное — повтор по правилам очереди."""
    domain = (job.input_ref or {}).get("domain")
    row = get(session, job.user_id, domain) if domain else None
    goal = goal_intake.get_confirmed(session, job.user_id, domain) if domain else None
    if row is None or goal is None:
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
    row.profile, row.status, row.error = profile, DRAFT, None
    row.updated_at = datetime.now(timezone.utc)
    session.flush()
    return {"areas": len(profile["areas"]), "concepts": skill_profile.concept_count(profile)}


def run_now_if_inline(session: Session, job: Job) -> None:
    """Dev-режим: задачи идут при синхронизации, а профиль нужен сразу, поэтому исполняем на запросе."""
    if settings.jobs_mode == "inline":
        from core.ai_gateway import get_ai_gateway
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


def match(session: Session, row: SkillProfile, gateway: Any = None) -> dict[str, Any]:
    """Сопоставить профиль с графом и сохранить решения в профиле: они видны человеку и потом строят граф."""
    if row.status == BUILDING or not row.profile:
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


def build_graph(session: Session, row: SkillProfile, gateway: Any = None) -> dict[str, Any]:
    """Построить скелет графа по профилю: сопоставить (если ещё не), завести области и понятия."""
    if row.status == BUILDING or not row.profile:
        raise ProfileError("not_ready", "Профиль ещё не построен")
    if not (row.profile.get("match") or {}).get("areas"):
        match(session, row, gateway)
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
