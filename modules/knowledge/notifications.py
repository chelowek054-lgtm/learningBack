"""Уведомления о курсе: «готов» и «дополнен» с честной пометкой о непроверенном (T-0083, R-0044).

Человек не ждёт разбора источников: курс собирается из имеющегося, а когда граф области
пополняется (разобрана книга), он узнаёт, что добавлено и что это ещё черновик. Уведомления
копятся: пока предыдущее «дополнен» не прочитано, новое прибавляется к нему, а не создаёт ещё одно.
Лежат в базе и отдаются приложению при синхронизации; канал push появится отдельно.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from modules.knowledge import provenance
from modules.knowledge.models import Concept, Course, Notification

READY, EXTENDED = "course_ready", "course_extended"
UNVERIFIED = "Не проверено специалистом: содержание собрано автоматически."


def _plural(n: int, one: str, few: str, many: str) -> str:
    m10, m100 = n % 10, n % 100
    if m10 == 1 and m100 != 11:
        return one
    if 2 <= m10 <= 4 and not 12 <= m100 <= 14:
        return few
    return many


def course_ready(
    session: Session, user_id: uuid.UUID, domain: str, steps: int, drafts: int
) -> Notification | None:
    """Курс собран. Если непрочитанное «готов» по области уже есть, второе не создаётся."""
    exists = (
        session.query(Notification)
        .filter_by(user_id=user_id, kind=READY, domain=domain, read_at=None)
        .first()
    )
    if exists is not None:
        return None
    body = f"В курсе {steps} {_plural(steps, 'шаг', 'шага', 'шагов')}, можно приступать."
    if drafts:
        body += f" {UNVERIFIED} Черновых шагов: {drafts}."
    note = Notification(
        user_id=user_id,
        kind=READY,
        domain=domain,
        title="Курс готов",
        body=body,
        data={"steps": steps, "drafts": drafts},
    )
    session.add(note)
    session.flush()
    return note


def course_extended(session: Session, domain: str, added: int, drafts: int) -> int:
    """Граф области пополнился: сообщить всем, у кого по ней есть курс. Вернуть, скольким."""
    if added <= 0:
        return 0
    user_ids = [row[0] for row in session.query(Course.user_id).filter_by(domain=domain).all()]
    for uid in user_ids:
        note = (
            session.query(Notification)
            .filter_by(user_id=uid, kind=EXTENDED, domain=domain, read_at=None)
            .first()
        )
        if note is None:
            note = Notification(
                user_id=uid, kind=EXTENDED, domain=domain, title="", body="", data={}
            )
            session.add(note)
        total = int((note.data or {}).get("added", 0)) + added
        unverified = int((note.data or {}).get("drafts", 0)) + drafts
        note.data = {"added": total, "drafts": unverified}
        note.title = "Курс дополнен"
        note.body = (
            f"В область добавлено {total} {_plural(total, 'понятие', 'понятия', 'понятий')}."
        )
        if unverified:
            note.body += f" {UNVERIFIED} Из них черновых: {unverified}."
        note.created_at = datetime.now(timezone.utc)
    session.flush()
    return len(user_ids)


def draft_count(session: Session, domain: str, concept_ids: list[uuid.UUID]) -> int:
    if not concept_ids:
        return 0
    return (
        session.query(Concept)
        .filter(
            Concept.id.in_(concept_ids),
            Concept.domain == domain,
            Concept.status == provenance.DRAFT,
        )
        .count()
    )


def view(n: Notification) -> dict[str, Any]:
    return {
        "id": str(n.id),
        "kind": n.kind,
        "domain": n.domain,
        "title": n.title,
        "body": n.body,
        "data": n.data or {},
        "createdAt": n.created_at.isoformat() if n.created_at else None,
        "read": n.read_at is not None,
    }


def listing(
    session: Session, user_id: uuid.UUID, unread_only: bool = True, limit: int = 50
) -> list[dict]:
    q = session.query(Notification).filter_by(user_id=user_id)
    if unread_only:
        q = q.filter(Notification.read_at.is_(None))
    return [view(n) for n in q.order_by(Notification.created_at.desc()).limit(limit).all()]


def mark_read(session: Session, user_id: uuid.UUID, ids: list[uuid.UUID] | None = None) -> int:
    """Отметить прочитанными свои уведомления (все, если ids не заданы). Чужие не трогаются."""
    q = session.query(Notification).filter_by(user_id=user_id, read_at=None)
    if ids is not None:
        q = q.filter(Notification.id.in_(ids))
    rows = q.all()
    now = datetime.now(timezone.utc)
    for n in rows:
        n.read_at = now
    session.flush()
    return len(rows)
