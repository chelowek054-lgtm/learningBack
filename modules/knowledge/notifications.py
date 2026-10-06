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

from core import push
from core.models import Activity, Job

from modules.knowledge import provenance
from modules.knowledge.models import Concept, Course, Notification

READY, EXTENDED = "course_ready", "course_extended"
VERIFIED, CHANGED = "concept_verified", "concept_changed"
MAX_NAMED = 3  # сколько названий понятий перечислять в тексте, остальные — «и ещё N»
UNVERIFIED = "Не проверено специалистом: содержание собрано автоматически."


def _plural(n: int, one: str, few: str, many: str) -> str:
    m10, m100 = n % 10, n % 100
    if m10 == 1 and m100 != 11:
        return one
    if 2 <= m10 <= 4 and not 12 <= m100 <= 14:
        return few
    return many


def announce(session: Session, note: Notification) -> None:
    """Сообщить о новом уведомлении вне приложения. Сбой канала сборку курса не ломает."""
    push.enqueue(session, note.user_id, {"notificationId": str(note.id)})


def push_job(session: Session, job: Job, gateway: Any) -> dict[str, Any]:
    """Задача отправки push: ValueError — навсегда (нет уведомления), остальное — повтор по правилам очереди.

    Текст общий: вид уведомления и область, без названий понятий и источников.
    """
    try:
        note = session.get(
            Notification, uuid.UUID(str((job.input_ref or {}).get("notificationId")))
        )
    except ValueError as e:
        raise ValueError("notificationId некорректен") from e
    if note is None:
        raise ValueError("Уведомление не найдено")
    if note.read_at is not None:
        return {"sent": 0, "skipped": "already_read"}
    return push.deliver(
        session,
        note.user_id,
        lambda token: {
            "to": token,
            "title": note.title,
            "body": f"Область: {note.domain}",
            "sound": "default",
            "data": {"notificationId": str(note.id), "kind": note.kind, "domain": note.domain},
        },
    )


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
    announce(session, note)
    return note


def course_extended(session: Session, domain: str, added: int, drafts: int) -> int:
    """Граф области пополнился: сообщить всем, у кого по ней есть курс. Вернуть, скольким."""
    if added <= 0:
        return 0
    user_ids = [row[0] for row in session.query(Course.user_id).filter_by(domain=domain).all()]
    fresh: list[Notification] = []
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
            fresh.append(note)
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
    for note in fresh:  # push — только о новом уведомлении: накопление непрочитанного молчит
        announce(session, note)
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


# ---- проверка и правка понятий (T-0085) ----


def _learners(
    session: Session, concept_id: uuid.UUID, *, completed: bool
) -> list[tuple[uuid.UUID, str]]:
    """(человек, область курса): у кого понятие в пути курса или, если `completed`, уже пройдено."""
    cid = str(concept_id)
    q = session.query(Course.user_id, Course.domain)
    q = q.filter(
        Course.progress.contains({"completed": [cid]})
        if completed
        else Course.path.contains([{"conceptId": cid}])
    )
    return [(uid, domain) for uid, domain in q.all()]


def _names(entries: list[dict[str, Any]]) -> str:
    shown = ", ".join(f"«{e['title']}»" for e in entries[:MAX_NAMED])
    rest = len(entries) - MAX_NAMED
    return shown + (f" и ещё {rest}" if rest > 0 else "")


def _collect(
    session: Session,
    user_id: uuid.UUID,
    kind: str,
    domain: str,
    entry: dict[str, Any],
) -> Notification:
    """Прибавить понятие к непрочитанному уведомлению того же вида; нового не плодить."""
    note = (
        session.query(Notification)
        .filter_by(user_id=user_id, kind=kind, domain=domain, read_at=None)
        .first()
    )
    is_new = note is None
    if note is None:
        note = Notification(user_id=user_id, kind=kind, domain=domain, title="", body="", data={})
        session.add(note)
    entries = list((note.data or {}).get("concepts", []))
    entries = [e for e in entries if e["id"] != entry["id"]] + [entry]
    note.data = {"concepts": entries}
    if kind == VERIFIED:
        note.title = "Понятие проверено"
        note.body = f"Специалист проверил: {_names(entries)}. Пометка «Черновик» снята."
    else:
        note.title = "Понятие изменено"
        what = entries[-1].get("what") or "изменено изложение"
        note.body = (
            f"В понятиях, которые вы уже прошли, изменился смысл: {_names(entries)}. "
            f"{what[:200]}. Рекомендуем повторить шаг."
        )
    note.created_at = datetime.now(timezone.utc)
    if is_new:
        session.flush()
        announce(session, note)
    return note


def sync_activity_status(session: Session, concept: Concept) -> int:
    """Обновить пометку «проверено/черновик» в уже выданных заданиях шага (уйдёт при синхронизации)."""
    status = "verified" if concept.status == provenance.APPROVED else "draft"
    changed = 0
    for act in session.query(Activity).filter(
        Activity.payload["conceptId"].as_string() == str(concept.id)
    ):
        if (act.payload or {}).get("status") not in (None, status):
            act.payload = {**act.payload, "status": status}
            changed += 1
    session.flush()
    return changed


def concept_reviewed(session: Session, concept: Concept, before: str) -> int:
    """Понятие проверили или отклонили. Подтверждение уведомляет тех, у кого оно в курсе."""
    sync_activity_status(session, concept)
    if before == provenance.APPROVED or concept.status != provenance.APPROVED:
        return 0
    people = _learners(session, concept.id, completed=False)
    for uid, domain in people:
        _collect(session, uid, VERIFIED, domain, {"id": str(concept.id), "title": concept.title})
    session.flush()
    return len(people)


def concept_changed(session: Session, concept: Concept, what: str) -> int:
    """Смысл понятия изменился: сообщить только тем, кто его уже прошёл."""
    people = _learners(session, concept.id, completed=True)
    for uid, domain in people:
        _collect(
            session,
            uid,
            CHANGED,
            domain,
            {"id": str(concept.id), "title": concept.title, "what": what},
        )
    session.flush()
    return len(people)
