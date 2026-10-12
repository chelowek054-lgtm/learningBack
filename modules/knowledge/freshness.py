"""Протухание знаний и доверие к источникам по исходам споров (R-0076, R-0077, T-0163, T-0164, T-0167).

Всё здесь работает без модели: находит просроченное по датам, ходит по адресам источников, считает
проигранные споры. Запускается задачами очереди; расписание — внешнее (cron вызывает
`scripts/refresh_knowledge.py`), приложение само по часам ничего не делает.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import func
from sqlalchemy.orm import Session

from core.models import Job
from modules.knowledge import provenance
from modules.knowledge.models import (
    Claim,
    ClaimSource,
    Concept,
    SourceDocument,
    SourceFragment,
)

log = logging.getLogger("praxis.freshness")

REFRESH_JOB = "refresh_stale"
LINKS_JOB = "check_links"

# Сколько проигранных споров понижает уровень доверия источника и сколько отправляет в карантин.
LOST_TO_B = 3
LOST_TO_C = 5
LOST_TO_QUARANTINE = 8

LINK_RECHECK_DAYS = 30
LINK_TIMEOUT = 10.0
LINK_BATCH = 200


# ---- протухание: просроченные утверждения → поиск свежих источников ----


def stale_by_domain(session: Session, now: datetime | None = None) -> dict[str, list[str]]:
    """Область → названия понятий, у которых есть принятые утверждения с истёкшим «пересмотреть после»."""
    now = now or datetime.now(timezone.utc)
    rows = (
        session.query(Concept.domain, Concept.title)
        .join(Claim, Claim.concept_id == Concept.id)
        .filter(
            Claim.status == "accepted",
            Claim.review_after.isnot(None),
            Claim.review_after <= now,
            Concept.status != provenance.REJECTED,
        )
        .distinct()
        .all()
    )
    out: dict[str, list[str]] = {}
    for domain, title in rows:
        out.setdefault(domain, []).append(title)
    return out


def enqueue_refresh(session: Session, user_id: uuid.UUID) -> Job:
    job = Job(user_id=user_id, type=REFRESH_JOB, status="pending", input_ref={})
    session.add(job)
    session.flush()
    return job


def refresh_job(session: Session, job: Job, gateway: Any) -> dict[str, Any]:
    """По каждой области с просроченным ставит поиск свежих источников; дальше — обычный конвейер.

    Новые утверждения пройдут сверку: у быстро устаревающих более свежая дата заменяет прежнее
    (reconcile), у остальных расхождение уходит в пул проблем.
    """
    from modules.knowledge import profile_sources

    stale = stale_by_domain(session)
    queued: list[str] = []
    for domain, titles in sorted(stale.items()):
        queries = [f"{domain} {t}" for t in sorted(titles)[:3]]
        if profile_sources.enqueue(session, job.user_id, domain, queries) is not None:
            queued.append(domain)
    return {"stale_domains": len(stale), "queued": queued}


# ---- проверка ссылок на оригинал ----


def check_url(url: str, client: httpx.Client) -> bool:
    """Жив ли адрес: HEAD, при отказе метода — GET. Любая сетевая ошибка считается недоступностью."""
    try:
        r = client.head(url, follow_redirects=True)
        if r.status_code in (403, 405, 501):
            r = client.get(url, follow_redirects=True)
        return r.status_code < 400
    except httpx.HTTPError:
        return False


def check_links(
    session: Session, *, transport: httpx.BaseTransport | None = None, now: datetime | None = None
) -> dict[str, int]:
    """Проверить адреса источников, которые давно не проверялись; мёртвые помечаются, фрагменты остаются."""
    now = now or datetime.now(timezone.utc)
    docs = (
        session.query(SourceDocument)
        .filter(SourceDocument.origin_url.isnot(None), SourceDocument.quarantined.is_(False))
        .limit(LINK_BATCH * 5)
        .all()
    )
    due = []
    for d in docs:
        checked = (d.meta or {}).get("linkCheckedAt")
        if checked:
            try:
                age = now - datetime.fromisoformat(checked)
                if age.days < LINK_RECHECK_DAYS:
                    continue
            except ValueError:
                pass
        due.append(d)
    report = {"checked": 0, "dead": 0, "alive": 0}
    with httpx.Client(timeout=LINK_TIMEOUT, transport=transport) as client:
        for d in due[:LINK_BATCH]:
            alive = check_url(d.origin_url or "", client)
            d.meta = {**(d.meta or {}), "linkCheckedAt": now.isoformat(), "linkDead": not alive}
            report["checked"] += 1
            report["alive" if alive else "dead"] += 1
    session.flush()
    return report


def enqueue_links(session: Session, user_id: uuid.UUID) -> Job:
    job = Job(user_id=user_id, type=LINKS_JOB, status="pending", input_ref={})
    session.add(job)
    session.flush()
    return job


def links_job(session: Session, job: Job, gateway: Any) -> dict[str, Any]:
    return check_links(session)


# ---- доверие к источнику по исходам споров, отзыв ----


def _documents_of_claim(session: Session, claim_id: uuid.UUID) -> list[SourceDocument]:
    return (
        session.query(SourceDocument)
        .join(SourceFragment, SourceFragment.document_id == SourceDocument.id)
        .join(ClaimSource, ClaimSource.fragment_id == SourceFragment.id)
        .filter(ClaimSource.claim_id == claim_id)
        .distinct()
        .all()
    )


def record_lost_dispute(session: Session, loser: Claim) -> list[str]:
    """Источники проигравшего утверждения получают отметку; много проигранных споров понижают доверие.

    Возвращает ключи документов, у которых уровень изменился. Решение человека не отменяется:
    это поправка на будущее, и руками уровень можно вернуть.
    """
    changed: list[str] = []
    for d in _documents_of_claim(session, loser.id):
        meta = dict(d.meta or {})
        lost = int(meta.get("lostDisputes", 0)) + 1
        meta["lostDisputes"] = lost
        d.meta = meta
        tier = d.trust_tier
        new = tier
        if lost >= LOST_TO_C and tier in ("A", "B"):
            new = "C"
        elif lost >= LOST_TO_B and tier == "A":
            new = "B"
        if new != tier:
            d.trust_tier = new
            d.trust_reasons = [*(d.trust_reasons or []), f"проиграно споров: {lost}"]
            changed.append(str(d.id))
        if lost >= LOST_TO_QUARANTINE and d.trust_tier == "C" and not d.quarantined:
            d.quarantined = True
            d.trust_reasons = [*(d.trust_reasons or []), "карантин: много проигранных споров"]
            changed.append(str(d.id))
    session.flush()
    return changed


def revoke_source(session: Session, doc_id: uuid.UUID, reason: str = "") -> dict[str, Any]:
    """Отозвать источник: удалить вместе с выведенным только из него; изученное отклоняется с записью в журнале."""
    doc = session.get(SourceDocument, doc_id)
    if doc is None:
        raise LookupError("Источник не найден")
    claims = (
        session.query(func.count(ClaimSource.id))
        .join(SourceFragment, SourceFragment.id == ClaimSource.fragment_id)
        .filter(SourceFragment.document_id == doc.id)
        .scalar()
    )
    rep = provenance.remove_document(session, doc)
    return {
        "documentId": str(doc_id),
        "claimSources": int(claims or 0),
        "reason": reason[:300],
        "conceptsDeleted": len(rep.concepts_deleted),
        "conceptsRejected": len(rep.concepts_rejected),
        "conceptsKept": len(rep.concepts_kept),
    }
