"""Сверка новых утверждений с узлом и пул проблем (R-0074, T-0157, T-0158, T-0165).

Правило узла — в нём нет расхождений. Один запрос к модели на узел на пачку добавлений: принятые
утверждения с номерами и добавляемые рядом. По каждому добавляемому — «совпадает с №», «нейтрально» или
«противоречит №». Совпало — к принятому добавляется источник; нейтрально — принимается как новое;
противоречит — проблема в пул для администраторов, утверждение в теорию не идёт. Истину модель не решает:
она только находит противоречие и описывает его; решает человек (resolve).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from core.config import settings
from core.models import Job
from modules.knowledge import provenance
from modules.knowledge.models import (
    Claim,
    ClaimConflict,
    ClaimSource,
    Concept,
    ReviewLog,
    SourceDocument,
    SourceFragment,
)

log = logging.getLogger("praxis.reconcile")

JOB_TYPE = "reconcile_domain"
VERDICTS = ("same", "neutral", "contradicts")

RECONCILE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "номер добавляемого утверждения, например N1",
                    },
                    "verdict": {"type": "string", "enum": list(VERDICTS)},
                    "target": {
                        "type": "string",
                        "description": "номер принятого утверждения (A1…) при same и contradicts, иначе пусто",
                    },
                    "reason": {"type": "string", "description": "одно предложение"},
                },
                "required": ["id", "verdict"],
            },
        }
    },
    "required": ["verdicts"],
}

# Сколько принятых утверждений узла показывать модели за один запрос.
MAX_ACCEPTED = 80


@dataclass
class ReconcileReport:
    concepts: int = 0
    calls: int = 0
    accepted: int = 0
    merged: int = 0
    conflicts: int = 0
    published: int = 0


def _prompt(concept: Concept, accepted: list[Claim], new: list[Claim]) -> str:
    a = "\n".join(f"A{i}. [{c.kind}] {c.text}" for i, c in enumerate(accepted, start=1))
    n = "\n".join(
        f"N{i}. [{c.kind}] {c.text}"
        + (f" (применимо: {c.applicability})" if c.applicability else "")
        for i, c in enumerate(new, start=1)
    )
    return (
        f"Понятие «{concept.title}». Ниже уже принятые утверждения узла (A) и добавляемые из нового "
        "источника (N). Для КАЖДОГО добавляемого реши: same — то же самое утверждение, что принятое "
        "(другими словами; укажи target); neutral — новое, не пересекается и не противоречит ничему из "
        "принятого; contradicts — утверждает несовместимое с принятым (укажи target и кратко в чём "
        "расхождение). Разные формулировки одного факта — это same, а не contradicts. Если утверждения "
        "применимы при разных условиях, это neutral. Сомневаешься между same и neutral — neutral.\n\n"
        f"Принятые:\n{a}\n\nДобавляемые:\n{n}"
    )


def _add_sources(session: Session, src_claim: Claim, dst_claim: Claim) -> None:
    have = {
        row[0] for row in session.query(ClaimSource.fragment_id).filter_by(claim_id=dst_claim.id)
    }
    for cs in session.query(ClaimSource).filter_by(claim_id=src_claim.id).all():
        if cs.fragment_id not in have:
            session.add(
                ClaimSource(
                    claim_id=dst_claim.id,
                    fragment_id=cs.fragment_id,
                    quote=cs.quote,
                    char_start=cs.char_start,
                    char_end=cs.char_end,
                    verified=cs.verified,
                )
            )
    session.flush()


def _accept(claim: Claim) -> None:
    claim.status = "accepted"
    if claim.review_after is None and claim.as_of is not None:
        days = {"stable": None, "slow": 365 * 2, "fast": 270}.get(claim.volatility)
        if days:
            claim.review_after = claim.as_of + timedelta(days=days)


def reconcile_concept(session: Session, concept: Concept, gateway, report: ReconcileReport) -> None:
    new = (
        session.query(Claim)
        .filter_by(concept_id=concept.id, status="proposed")
        .order_by(Claim.created_at)
        .all()
    )
    if not new:
        return
    report.concepts += 1
    accepted = (
        session.query(Claim)
        .filter_by(concept_id=concept.id, status="accepted")
        .order_by(Claim.created_at)
        .limit(MAX_ACCEPTED)
        .all()
    )
    if not accepted:
        # Первый источник узла: сверять не с чем, принимаем (одинаковые тексты внутри пачки — один раз).
        seen: dict[str, Claim] = {}
        for c in new:
            key = " ".join(c.text.lower().split())
            if key in seen:
                _add_sources(session, c, seen[key])
                session.delete(c)
                report.merged += 1
                continue
            seen[key] = c
            _accept(c)
            report.accepted += 1
        session.flush()
        return

    report.calls += 1
    raw = gateway.structured(
        "reconcile_claims",
        "Сверить добавляемые утверждения с принятыми утверждениями узла.",
        RECONCILE_SCHEMA,
        _prompt(concept, accepted, new),
    )
    by_id = {}
    for item in (raw or {}).get("verdicts") or []:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            by_id[item["id"].strip().upper()] = item
    for i, claim in enumerate(new, start=1):
        item = by_id.get(f"N{i}") or {}
        verdict = item.get("verdict") if item.get("verdict") in VERDICTS else "neutral"
        target = None
        tid = str(item.get("target") or "").strip().upper()
        if tid.startswith("A") and tid[1:].isdigit() and 1 <= int(tid[1:]) <= len(accepted):
            target = accepted[int(tid[1:]) - 1]
        if verdict in ("same", "contradicts") and target is None:
            verdict = "neutral"  # без указанной пары вывод не применяем: хуже пропуск, чем догадка
        if verdict == "contradicts" and target is not None:
            # Разные условия применимости — не противоречие.
            if (
                claim.applicability
                and target.applicability
                and claim.applicability != target.applicability
            ):
                verdict = "neutral"
        if verdict == "same" and target is not None:
            _add_sources(session, claim, target)
            session.delete(claim)
            report.merged += 1
        elif verdict == "contradicts" and target is not None:
            claim.status = "disputed"
            session.add(
                ClaimConflict(
                    a_id=target.id,
                    b_id=claim.id,
                    reason=str(item.get("reason") or "")[:500],
                    status="open",
                )
            )
            report.conflicts += 1
        else:
            _accept(claim)
            report.accepted += 1
    session.flush()


def maybe_publish(session: Session, concept: Concept) -> bool:
    """Узел публикуется сам, если все принятые утверждения подтверждены ≥2 источниками A/B и проблем нет."""
    if concept.status == provenance.APPROVED or not settings.auto_publish:
        return False
    claims = session.query(Claim).filter_by(concept_id=concept.id).all()
    live = [c for c in claims if c.status == "accepted"]
    if not live or any(c.status == "disputed" for c in claims):
        return False
    for c in live:
        docs = (
            session.query(SourceDocument.id, SourceDocument.trust_tier)
            .join(SourceFragment, SourceFragment.document_id == SourceDocument.id)
            .join(ClaimSource, ClaimSource.fragment_id == SourceFragment.id)
            .filter(ClaimSource.claim_id == c.id)
            .distinct()
            .all()
        )
        strong = {d[0] for d in docs if d[1] in ("A", "B")}
        if len(strong) < 2:
            return False
    concept.status = provenance.APPROVED
    session.add(
        ReviewLog(
            target_type="concept",
            target_id=concept.id,
            reviewer_id=None,
            action="approve",
            note="автопубликация: каждое утверждение подтверждено двумя источниками A/B",
        )
    )
    session.flush()
    return True


def reconcile_domain(session: Session, domain: str, gateway) -> ReconcileReport:
    """Сверить все узлы области, у которых есть ожидающие утверждения."""
    report = ReconcileReport()
    ids = [
        row[0]
        for row in session.query(Claim.concept_id)
        .join(Concept, Concept.id == Claim.concept_id)
        .filter(Concept.domain == domain, Claim.status == "proposed")
        .distinct()
        .all()
    ]
    for cid in ids:
        concept = session.get(Concept, cid)
        if concept is None:
            continue
        reconcile_concept(session, concept, gateway, report)
        if maybe_publish(session, concept):
            report.published += 1
    return report


# ---- пул проблем (T-0158, T-0165) ----

RESOLUTIONS = ("a", "b", "both", "disputed", "outdated")


def pool(
    session: Session, domain: str | None = None, status: str = "open", limit: int = 200
) -> list[dict]:
    """Проблемы для администраторов: оба утверждения, их источники с цитатами, объяснение."""
    q = session.query(ClaimConflict).filter_by(status=status).order_by(ClaimConflict.created_at)
    out = []
    for cf in q.limit(limit).all():
        a, b = session.get(Claim, cf.a_id), session.get(Claim, cf.b_id)
        if a is None or b is None:
            continue
        concept = session.get(Concept, a.concept_id)
        if domain and (concept is None or concept.domain != domain):
            continue
        out.append(
            {
                "id": str(cf.id),
                "concept": concept.title if concept else None,
                "conceptId": str(a.concept_id),
                "domain": concept.domain if concept else None,
                "reason": cf.reason,
                "status": cf.status,
                "resolution": cf.resolution,
                "a": _claim_view(session, a),
                "b": _claim_view(session, b),
                "createdAt": cf.created_at.isoformat() if cf.created_at else None,
            }
        )
    return out


def _claim_view(session: Session, claim: Claim) -> dict:
    rows = (
        session.query(ClaimSource, SourceFragment, SourceDocument)
        .join(SourceFragment, SourceFragment.id == ClaimSource.fragment_id)
        .join(SourceDocument, SourceDocument.id == SourceFragment.document_id)
        .filter(ClaimSource.claim_id == claim.id)
        .all()
    )
    return {
        "id": str(claim.id),
        "text": claim.text,
        "kind": claim.kind,
        "status": claim.status,
        "sources": [
            {
                "document": d.title,
                "tier": d.trust_tier,
                "page": f.page,
                "heading": f.heading,
                "quote": cs.quote,
            }
            for cs, f, d in rows
        ],
    }


def resolve(
    session: Session,
    conflict_id: uuid.UUID,
    resolution: str,
    user_id: uuid.UUID | None,
    note: str = "",
) -> ClaimConflict:
    """Решение человека: a — верно принятое; b — верно новое; both — верны при разных условиях; disputed — спорно;
    outdated — консенсус устарел (то же, что b, с причиной). Решение пишется в журнал."""
    if resolution not in RESOLUTIONS:
        raise ValueError("Неизвестное решение")
    cf = session.get(ClaimConflict, conflict_id)
    if cf is None:
        raise LookupError("Проблема не найдена")
    a, b = session.get(Claim, cf.a_id), session.get(Claim, cf.b_id)
    if a is None or b is None:
        raise LookupError("Утверждение удалено")
    if resolution == "a":
        b.status = "replaced"
        b.replaced_by = a.id
    elif resolution in ("b", "outdated"):
        a.status = "replaced"
        a.replaced_by = b.id
        b.status = "accepted"
    elif resolution == "both":
        b.status = "accepted"
    else:  # disputed: обе позиции остаются, в теорию идёт только принятое
        b.status = "disputed"
    cf.status, cf.resolution, cf.resolved_by, cf.note = "resolved", resolution, user_id, note[:500]
    session.add(
        ReviewLog(
            target_type="claim_conflict",
            target_id=cf.id,
            reviewer_id=user_id,
            action="resolve",
            note=f"{resolution}: {note}"[:500],
        )
    )
    # Узел собрали из старого набора утверждений: сбрасываем хэш, сборка пересоберёт его.
    concept = session.get(Concept, a.concept_id)
    if concept is not None:
        content = dict(concept.content or {})
        content.pop("claims_hash", None)
        concept.content = content
    session.flush()
    return cf


# ---- задача ----


def enqueue(session: Session, domain: str, user_id: uuid.UUID) -> Job:
    job = Job(user_id=user_id, type=JOB_TYPE, status="pending", input_ref={"domain": domain})
    session.add(job)
    session.flush()
    return job


def reconcile_job(session: Session, job: Job, gateway) -> dict[str, Any]:
    domain = str((job.input_ref or {}).get("domain") or "").strip()
    if not domain:
        raise ValueError("Не указана область")
    r = reconcile_domain(session, domain, gateway)
    # Дальше — сборка узлов области: теория и банк заданий.
    from modules.knowledge import node_compile

    node_compile.enqueue(session, domain, job.user_id)
    return {
        "domain": domain,
        "concepts": r.concepts,
        "calls": r.calls,
        "accepted": r.accepted,
        "merged": r.merged,
        "conflicts": r.conflicts,
        "published": r.published,
    }
