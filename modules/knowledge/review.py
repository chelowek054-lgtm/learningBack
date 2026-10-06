"""Специалисты по областям и очередь проверки черновиков (T-0081, R-0046).

Подтверждать и править знания графа могут администраторы и специалисты. Специалист назначается
администратором на конкретные области, а не на всю систему: видит очередь, источники и действует
только внутри своих областей. Каждое решение уходит в журнал (provenance.ReviewLog).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from core.models import User
from modules.knowledge import merge, notifications, provenance
from modules.knowledge.models import (
    Concept,
    ConceptConflict,
    ConceptEdge,
    DomainSpecialist,
)

MAX_QUEUE = 200


# ---- кто может проверять ----


def reviewable_domains(session: Session, user: User) -> set[str] | None:
    """Области проверяющего: None — все (администратор), иначе множество (может быть пустым)."""
    if user.is_superuser:
        return None
    return {
        row[0] for row in session.query(DomainSpecialist.domain).filter_by(user_id=user.id).all()
    }


def can_review(session: Session, user: User, domain: str) -> bool:
    domains = reviewable_domains(session, user)
    return domains is None or domain in domains


def require_reviewer(session: Session, user: User, domain: str) -> None:
    """Отказ 403 без подробностей: чужая область неотличима от несуществующей по роли."""
    if not can_review(session, user, domain):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Нет прав проверять эту область")


def require_any_reviewer(session: Session, user: User) -> set[str] | None:
    domains = reviewable_domains(session, user)
    if domains is not None and not domains:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Нет прав проверять знания")
    return domains


# ---- назначение специалистов (только администратор) ----


class SpecialistError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def grant(session: Session, admin: User, email: str, domain: str) -> DomainSpecialist:
    domain = domain.strip()
    if not domain:
        raise SpecialistError("no_domain", "Не указана область")
    user = session.query(User).filter(func.lower(User.email) == email.strip().lower()).first()
    if user is None:
        raise SpecialistError("unknown_user", "Пользователь с таким email не найден")
    row = session.query(DomainSpecialist).filter_by(user_id=user.id, domain=domain).first()
    if row is None:
        row = DomainSpecialist(user_id=user.id, domain=domain, granted_by=admin.id)
        session.add(row)
        session.flush()
    return row


def revoke(session: Session, specialist_id: uuid.UUID) -> bool:
    row = session.get(DomainSpecialist, specialist_id)
    if row is None:
        return False
    session.delete(row)
    session.flush()
    return True


def specialists(session: Session) -> list[dict[str, Any]]:
    rows = (
        session.query(DomainSpecialist, User.email)
        .join(User, User.id == DomainSpecialist.user_id)
        .order_by(DomainSpecialist.domain, User.email)
        .all()
    )
    return [
        {"id": str(s.id), "userId": str(s.user_id), "email": email, "domain": s.domain}
        for s, email in rows
    ]


# ---- очередь ----


def _concept_item(session: Session, c: Concept) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "domain": c.domain,
        "title": c.title,
        "tier": c.tier,
        "status": c.status,
        "confidence": c.confidence,
        "summary": (c.content or {}).get("summary", ""),
        "sources": provenance.concept_sources(session, c.id),
    }


def queue(
    session: Session, user: User, domain: str | None = None, limit: int = 50
) -> dict[str, Any]:
    """Что ждёт проверки в доступных областях: конфликты, черновые понятия и связи."""
    allowed = require_any_reviewer(session, user)
    if domain is not None:
        require_reviewer(session, user, domain)
        scope: set[str] | None = {domain}
    else:
        scope = allowed
    limit = max(1, min(limit, MAX_QUEUE))

    def in_scope(q, column):
        return q if scope is None else q.filter(column.in_(scope))

    concepts = (
        in_scope(session.query(Concept).filter(Concept.status == provenance.DRAFT), Concept.domain)
        .order_by(Concept.confidence.asc(), Concept.title)
        .limit(limit)
        .all()
    )
    edge_q = (
        session.query(ConceptEdge)
        .join(Concept, Concept.id == ConceptEdge.from_id)
        .filter(ConceptEdge.status == provenance.DRAFT)
    )
    edges = in_scope(edge_q, Concept.domain).order_by(ConceptEdge.type).limit(limit).all()
    conflict_q = (
        session.query(ConceptConflict)
        .join(Concept, Concept.id == ConceptConflict.a_id)
        .filter(ConceptConflict.status == "open")
    )
    conflicts = in_scope(conflict_q, Concept.domain).limit(limit).all()

    def edge_item(e: ConceptEdge) -> dict[str, Any]:
        a, b = session.get(Concept, e.from_id), session.get(Concept, e.to_id)
        return {
            "id": str(e.id),
            "domain": a.domain,
            "type": e.type,
            "status": e.status,
            "from": {"id": str(a.id), "title": a.title},
            "to": {"id": str(b.id), "title": b.title},
            "sources": provenance.edge_sources(session, e.id),
        }

    def conflict_item(c: ConceptConflict) -> dict[str, Any]:
        a, b = session.get(Concept, c.a_id), session.get(Concept, c.b_id)
        return {
            "id": str(c.id),
            "domain": a.domain,
            "reason": c.reason,
            "a": _concept_item(session, a),
            "b": _concept_item(session, b),
        }

    return {
        "conflicts": [conflict_item(c) for c in conflicts],
        "concepts": [_concept_item(session, c) for c in concepts],
        "edges": [edge_item(e) for e in edges],
        "domains": sorted(scope) if scope is not None else None,
    }


# ---- действия ----


def edit_concept(
    session: Session,
    concept: Concept,
    reviewer: User,
    *,
    title: str | None = None,
    summary: str | None = None,
    note: str | None = None,
) -> Concept:
    """Исправить название или изложение; статус не меняется, правка пишется в журнал."""
    require_reviewer(session, reviewer, concept.domain)
    changes = []
    meaning_changed = False
    if title is not None and title.strip() and title.strip() != concept.title:
        changes.append(f"название: «{concept.title}» → «{title.strip()}»")
        concept.title = title.strip()[:200]
    if summary is not None and summary.strip():
        content = dict(concept.content or {})
        if summary.strip() != content.get("summary"):
            changes.append("изложение исправлено")
            meaning_changed = True
            content["summary"] = summary.strip()
            concept.content = content
    if not changes:
        raise provenance.ProvenanceError("nothing_to_edit", "Нечего исправлять: поля не изменились")
    text = "; ".join(changes) + (f" — {note.strip()}" if note and note.strip() else "")
    provenance._log(session, "concept", concept.id, reviewer.id, "edit", text)  # noqa: SLF001
    session.flush()
    if meaning_changed:
        # Переименование смысла не меняет; исправленное изложение — меняет, и прошедшие должны знать.
        notifications.concept_changed(
            session,
            concept,
            f"Изложение исправлено — {note.strip()}"
            if note and note.strip()
            else "Изложение исправлено",
        )
    return concept


RESOLUTIONS = ("merge", "keep_both", "reject_a", "reject_b")


def resolve_conflict(
    session: Session, conflict: ConceptConflict, reviewer: User, resolution: str, note: str | None
) -> dict[str, Any]:
    """Закрыть противоречие решением человека: слить, оставить оба, отклонить одно из понятий."""
    conflict_id = conflict.id
    a, b = session.get(Concept, conflict.a_id), session.get(Concept, conflict.b_id)
    require_reviewer(session, reviewer, a.domain)
    if resolution not in RESOLUTIONS:
        raise provenance.ProvenanceError("bad_resolution", f"Решение: {', '.join(RESOLUTIONS)}")
    if resolution in ("reject_a", "reject_b") and not (note or "").strip():
        raise provenance.ProvenanceError("note_required", "Отклонение требует причины")
    if conflict.status != "open":
        raise provenance.ProvenanceError("already_resolved", "Противоречие уже разобрано")
    if resolution == "merge":
        keeper, loser = merge.pick_keeper(session, a, b)
        merge.merge_into(session, keeper, loser, (note or "решение проверяющего").strip())
    elif resolution == "reject_a":
        provenance.review_concept(session, a, reviewer.id, "reject", note)
    elif resolution == "reject_b":
        provenance.review_concept(session, b, reviewer.id, "reject", note)
    # Слияние удаляет вливаемое понятие, а вместе с ним по каскаду и строку конфликта.
    row = session.query(ConceptConflict).filter_by(id=conflict_id).first()
    if row is not None:
        row.status = "resolved"
    session.flush()
    return {"id": str(conflict_id), "resolution": resolution}
