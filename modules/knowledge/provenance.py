"""Происхождение знаний: у каждого утверждения графа есть источник и статус проверки (T-0076).

Понятие и связь, внесённые автоматически из документа, помечены «черновик» (`status = draft`) и
хранят ссылки на фрагменты документа, из которых выведены. Подтверждает их человек — администратор
или специалист; решение пишется в журнал (кто, когда, что). Источник можно удалить вместе со всем,
что выведено только из него; то, по чему уже учатся люди, не удаляется, а отклоняется.

Источники видны только администраторам (R-0045): учащемуся отдаётся статус, но не документ и не
фрагмент. Поэтому всё, что выдаёт тексты и ссылки, живёт здесь и в админских маршрутах.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from core.objects import ObjectStore, get_store
from modules.knowledge.models import (
    Assessment,
    Concept,
    ConceptEdge,
    ConceptLink,
    ConceptSource,
    EdgeSource,
    ReviewLog,
    SourceDocument,
    SourceFragment,
    UserConcept,
)

DRAFT, APPROVED, REJECTED = "draft", "approved", "rejected"
ACTIONS = {"approve": APPROVED, "reject": REJECTED}
ROLES = ("definition", "example", "misconception", "context")


class ProvenanceError(ValueError):
    """Нельзя выполнить действие; `code` — для тестов и ответа API."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# ---- документы и фрагменты ----


def add_document(
    session: Session,
    *,
    title: str,
    data: bytes,
    added_by: uuid.UUID | None = None,
    domain: str | None = None,
    level: str | None = None,
    license: str | None = None,
    origin_url: str | None = None,
    meta: dict[str, Any] | None = None,
    store: ObjectStore | None = None,
) -> tuple[SourceDocument, bool]:
    """Завести документ и сохранить файл. Тот же файл второй раз — вернуть прежний (False)."""
    if not data:
        raise ProvenanceError("empty_file", "Файл пустой")
    digest = hashlib.sha256(data).hexdigest()
    found = session.query(SourceDocument).filter_by(content_hash=digest).one_or_none()
    if found is not None:
        return found, False
    key = f"sources/{digest}"
    (store or get_store()).put(key, data)
    doc = SourceDocument(
        title=title.strip() or "Без названия",
        object_key=key,
        content_hash=digest,
        origin_url=origin_url,
        license=license,
        domain=domain,
        level=level,
        meta=meta or {},
        added_by=added_by,
    )
    session.add(doc)
    session.flush()
    return doc, True


def add_fragments(
    session: Session, doc: SourceDocument, fragments: list[dict[str, Any]]
) -> list[SourceFragment]:
    """Фрагменты документа по порядку: `{text, page?, heading?}`. Пустой текст пропускается."""
    start = (
        session.query(func.coalesce(func.max(SourceFragment.ordinal), -1))
        .filter(SourceFragment.document_id == doc.id)
        .scalar()
        + 1
    )
    out: list[SourceFragment] = []
    for item in fragments:
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        frag = SourceFragment(
            document_id=doc.id,
            ordinal=start + len(out),
            page=item.get("page"),
            heading=(item.get("heading") or None),
            text=text,
        )
        session.add(frag)
        out.append(frag)
    session.flush()
    return out


def _existing_fragments(session: Session, ids: list[uuid.UUID]) -> list[uuid.UUID]:
    if not ids:
        raise ProvenanceError("no_source", "Утверждение без ссылки на фрагмент не принимается")
    found = {
        row[0] for row in session.query(SourceFragment.id).filter(SourceFragment.id.in_(ids)).all()
    }
    missing = [str(i) for i in ids if i not in found]
    if missing:
        raise ProvenanceError("unknown_fragment", f"Нет фрагмента: {', '.join(missing)}")
    return list(dict.fromkeys(ids))


def link_concept(
    session: Session, concept: Concept, fragment_ids: list[uuid.UUID], role: str = "definition"
) -> None:
    """Привязать понятие к фрагментам. Пустой список — отказ: понятие без источника не принимается."""
    if role not in ROLES:
        raise ProvenanceError("bad_role", f"Роль «{role}» не из списка: {', '.join(ROLES)}")
    for fid in _existing_fragments(session, fragment_ids):
        exists = (
            session.query(ConceptSource)
            .filter_by(concept_id=concept.id, fragment_id=fid, role=role)
            .first()
        )
        if exists is None:
            session.add(ConceptSource(concept_id=concept.id, fragment_id=fid, role=role))
    session.flush()


def link_edge(session: Session, edge: ConceptEdge, fragment_ids: list[uuid.UUID]) -> None:
    for fid in _existing_fragments(session, fragment_ids):
        if session.query(EdgeSource).filter_by(edge_id=edge.id, fragment_id=fid).first() is None:
            session.add(EdgeSource(edge_id=edge.id, fragment_id=fid))
    session.flush()


# ---- что показывать администратору ----


def _fragment_view(frag: SourceFragment, doc: SourceDocument, role: str | None = None) -> dict:
    return {
        "fragmentId": str(frag.id),
        "documentId": str(doc.id),
        "document": doc.title,
        "page": frag.page,
        "heading": frag.heading,
        "text": frag.text,
        **({"role": role} if role else {}),
    }


def concept_sources(session: Session, concept_id: uuid.UUID) -> list[dict]:
    """Источники понятия для администратора: документ, страница, текст фрагмента."""
    rows = (
        session.query(SourceFragment, SourceDocument, ConceptSource.role)
        .join(ConceptSource, ConceptSource.fragment_id == SourceFragment.id)
        .join(SourceDocument, SourceDocument.id == SourceFragment.document_id)
        .filter(ConceptSource.concept_id == concept_id)
        .order_by(SourceDocument.title, SourceFragment.ordinal)
        .all()
    )
    return [_fragment_view(f, d, role) for f, d, role in rows]


def edge_sources(session: Session, edge_id: uuid.UUID) -> list[dict]:
    rows = (
        session.query(SourceFragment, SourceDocument)
        .join(EdgeSource, EdgeSource.fragment_id == SourceFragment.id)
        .join(SourceDocument, SourceDocument.id == SourceFragment.document_id)
        .filter(EdgeSource.edge_id == edge_id)
        .order_by(SourceDocument.title, SourceFragment.ordinal)
        .all()
    )
    return [_fragment_view(f, d) for f, d in rows]


def document_link(
    doc: SourceDocument, store: ObjectStore | None = None, ttl: int = 300
) -> str | None:
    """Временная ссылка на исходный файл — только для администратора."""
    if not doc.object_key:
        return None
    return (store or get_store()).link(doc.object_key, ttl)


def learner_status(concept: Concept) -> str:
    """Что видит учащийся: «проверено» или «черновик». Источник и документ — никогда."""
    return "verified" if concept.status == APPROVED else "draft"


# ---- проверка человеком ----


def _log(
    session: Session, target_type: str, target_id: uuid.UUID, reviewer_id, action: str, note
) -> None:
    session.add(
        ReviewLog(
            target_type=target_type,
            target_id=target_id,
            reviewer_id=reviewer_id,
            action=action,
            note=(note or "").strip() or None,
        )
    )


def review_concept(
    session: Session,
    concept: Concept,
    reviewer_id: uuid.UUID | None,
    action: str,
    note: str | None = None,
) -> Concept:
    """Подтвердить или отклонить понятие. Отклонение требует причины."""
    if action not in ACTIONS:
        raise ProvenanceError("bad_action", "Действие: approve или reject")
    if action == "reject" and not (note or "").strip():
        raise ProvenanceError("note_required", "Отклонение требует причины")
    before = concept.status
    concept.status = ACTIONS[action]
    _log(session, "concept", concept.id, reviewer_id, action, note)
    session.flush()
    # Локальный импорт: notifications сам импортирует provenance.
    from modules.knowledge import notifications

    notifications.concept_reviewed(session, concept, before)
    return concept


def review_edge(
    session: Session,
    edge: ConceptEdge,
    reviewer_id: uuid.UUID | None,
    action: str,
    note: str | None = None,
) -> ConceptEdge:
    if action not in ACTIONS:
        raise ProvenanceError("bad_action", "Действие: approve или reject")
    if action == "reject" and not (note or "").strip():
        raise ProvenanceError("note_required", "Отклонение требует причины")
    edge.status = ACTIONS[action]
    _log(session, "edge", edge.id, reviewer_id, action, note)
    session.flush()
    return edge


def history(session: Session, target_type: str, target_id: uuid.UUID) -> list[dict]:
    rows = (
        session.query(ReviewLog)
        .filter_by(target_type=target_type, target_id=target_id)
        .order_by(ReviewLog.created_at)
        .all()
    )
    return [
        {
            "action": r.action,
            "note": r.note,
            "reviewer": str(r.reviewer_id) if r.reviewer_id else None,
            "at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


# ---- удаление источника ----


@dataclass
class RemovalReport:
    fragments: int = 0
    concepts_deleted: list[str] = field(default_factory=list)
    concepts_rejected: list[str] = field(default_factory=list)
    concepts_kept: list[str] = field(default_factory=list)
    edges_deleted: int = 0
    file_deleted: bool = False


def _in_use(session: Session, concept: Concept) -> bool:
    """По понятию уже учатся: на него ссылается персональный слой человека."""
    return session.query(UserConcept).filter_by(base_concept_id=concept.id).first() is not None


def remove_document(
    session: Session,
    doc: SourceDocument,
    *,
    keep_verified: bool = False,
    store: ObjectStore | None = None,
) -> RemovalReport:
    """Удалить источник вместе со всем, что выведено только из него.

    Понятие или связь, у которых есть другие источники, остаются. Выведенное только из этого
    документа удаляется; но если по понятию уже учатся, оно не удаляется, а отклоняется с причиной
    в журнале. `keep_verified` оставляет подтверждённое человеком (с пометкой в журнале).
    """
    report = RemovalReport()
    frag_ids = [row[0] for row in session.query(SourceFragment.id).filter_by(document_id=doc.id)]
    report.fragments = len(frag_ids)

    concept_ids = {
        row[0]
        for row in session.query(ConceptSource.concept_id).filter(
            ConceptSource.fragment_id.in_(frag_ids)
        )
    }
    edge_ids = {
        row[0]
        for row in session.query(EdgeSource.edge_id).filter(EdgeSource.fragment_id.in_(frag_ids))
    }

    # Сначала снимаем ссылки на этот документ, потом смотрим, что осталось без источника.
    session.query(ConceptSource).filter(ConceptSource.fragment_id.in_(frag_ids)).delete(
        synchronize_session=False
    )
    session.query(EdgeSource).filter(EdgeSource.fragment_id.in_(frag_ids)).delete(
        synchronize_session=False
    )

    for edge_id in edge_ids:
        edge = session.get(ConceptEdge, edge_id)
        if edge is None:
            continue
        if session.query(EdgeSource).filter_by(edge_id=edge_id).first() is not None:
            continue
        if keep_verified and edge.status == APPROVED:
            _log(
                session,
                "edge",
                edge.id,
                None,
                "edit",
                f"источник «{doc.title}» удалён, связь оставлена",
            )
            continue
        session.delete(edge)
        report.edges_deleted += 1

    for concept_id in concept_ids:
        concept = session.get(Concept, concept_id)
        if concept is None:
            continue
        if session.query(ConceptSource).filter_by(concept_id=concept_id).first() is not None:
            continue
        if keep_verified and concept.status == APPROVED:
            _log(
                session,
                "concept",
                concept.id,
                None,
                "edit",
                f"источник «{doc.title}» удалён, понятие оставлено",
            )
            report.concepts_kept.append(str(concept.id))
        elif _in_use(session, concept):
            concept.status = REJECTED
            _log(
                session,
                "concept",
                concept.id,
                None,
                "reject",
                f"источник «{doc.title}» удалён, а по понятию уже учатся",
            )
            report.concepts_rejected.append(str(concept.id))
        else:
            _delete_concept(session, concept)
            report.concepts_deleted.append(str(concept_id))

    if doc.object_key:
        try:
            (store or get_store()).delete(doc.object_key)
            report.file_deleted = True
        except Exception:  # noqa: BLE001 — запись о документе важнее файла; файл уйдёт чисткой
            report.file_deleted = False
    session.delete(doc)
    session.flush()
    return report


def _delete_concept(session: Session, concept: Concept) -> None:
    cid = concept.id
    session.query(ConceptEdge).filter(
        (ConceptEdge.from_id == cid) | (ConceptEdge.to_id == cid)
    ).delete(synchronize_session=False)
    session.query(ConceptLink).filter(
        (ConceptLink.from_id == cid) | (ConceptLink.to_id == cid)
    ).delete(synchronize_session=False)
    session.query(Assessment).filter_by(concept_id=cid).delete(synchronize_session=False)
    session.query(ReviewLog).filter_by(target_type="concept", target_id=cid).delete(
        synchronize_session=False
    )
    session.delete(concept)
