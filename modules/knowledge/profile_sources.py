"""Автопоиск и разбор нескольких источников для новых областей профиля (T-0097, R-0051, A-0032).

Скелет профиля — понятия «со слов модели». Чтобы они опирались на источники, для каждой новой области в
фоне ищутся минимум `profile_min_sources` источников от разных поставщиков (каталоги, затем веб-поиск),
они скачиваются и ставятся в разбор. Разобранные понятия сливаются со скелетом готовым слиянием по близости:
у понятия с источником больше свидетельств, поэтому оно остаётся, а этап, уровень и необязательность
переходят к нему от скелета. Противоречия между источниками уходят в конфликты слияния.

Ручного запуска нет. Повторно искать по области, пока идёт предыдущий поиск или разбор её документов, нельзя.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from core.config import settings
from core.models import Job
from modules.knowledge import gap_search, source_search
from modules.knowledge.models import SourceDocument

JOB_TYPE = "profile_sources"
SEARCH_LIMIT = 5  # сколько кандидатов просить у поставщиков на один запрос
MAX_QUERIES = 3


def queries_for(area_title: str, stage_titles: list[str], concept_titles: list[str]) -> list[str]:
    """Несколько формулировок: по названию, по названию и ключевым понятиям, по этапам."""
    candidates = [
        area_title,
        " ".join([area_title, *concept_titles[:2]]),
        " ".join([area_title, *stage_titles[:2]]),
    ]
    out: list[str] = []
    for q in candidates:
        q = " ".join(q.split())
        if len(q) >= 3 and q not in out:
            out.append(q)
    return out[:MAX_QUERIES]


def pick_diverse(
    candidates: list[source_search.Candidate], n: int
) -> list[source_search.Candidate]:
    """Не больше n источников, по кругу от разных поставщиков: три страницы одного каталога — не три источника."""
    by_provider: dict[str, list[source_search.Candidate]] = {}
    for c in candidates:
        by_provider.setdefault(c.provider, []).append(c)
    picked: list[source_search.Candidate] = []
    while len(picked) < n and any(by_provider.values()):
        for provider in list(by_provider):
            if by_provider[provider] and len(picked) < n:
                picked.append(by_provider[provider].pop(0))
    return picked


def in_progress(session: Session, domain: str) -> bool:
    """Идёт ли по области поиск (задача в очереди) или разбор её документов."""
    for job in session.query(Job).filter(
        Job.type == JOB_TYPE, Job.status.in_(("pending", "running"))
    ):
        if (job.input_ref or {}).get("domain") == domain:
            return True
    docs = session.query(SourceDocument).filter(SourceDocument.domain == domain).all()
    from modules.knowledge import ingest

    return any(ingest.progress(session, d)["status"] in gap_search.ACTIVE_STATUSES for d in docs)


def enqueue(
    session: Session,
    user_id: uuid.UUID,
    domain: str,
    queries: list[str],
) -> Job | None:
    """Поставить поиск источников по области в очередь; None — выключено, нечего искать или уже идёт."""
    if not settings.profile_auto_sources or not queries or in_progress(session, domain):
        return None
    job = Job(
        user_id=user_id,
        type=JOB_TYPE,
        status="pending",
        input_ref={"domain": domain, "queries": queries},
    )
    session.add(job)
    session.flush()
    return job


def sources_job(session: Session, job: Job, gateway: Any) -> dict[str, Any]:
    """Обработчик: ValueError — навсегда (нет области), недоступный каталог не роняет задачу."""
    domain = str((job.input_ref or {}).get("domain") or "").strip()
    queries = [str(q) for q in (job.input_ref or {}).get("queries") or []]
    if not domain or not queries:
        raise ValueError("Не указана область или запросы")
    found: list[source_search.Candidate] = []
    problems: list[str] = []
    seen: set[str] = set()
    for query in queries:
        try:
            cands, issues = source_search.search(query, SEARCH_LIMIT)
        except source_search.SearchError as exc:
            problems.append(str(exc))
            continue
        problems.extend(issues)
        for c in cands:
            if c.url not in seen:
                seen.add(c.url)
                found.append(c)
    chosen = pick_diverse(found, max(1, settings.profile_min_sources))
    if not chosen:
        return {"domain": domain, "status": "nothing_found", "problems": problems[:5]}
    out = source_search.fetch_and_queue(session, job.user_id, domain, chosen, max_docs=len(chosen))
    return {
        "domain": domain,
        "status": "queued" if out["queued"] else "nothing_queued",
        "providers": sorted({c.provider for c in chosen}),
        "queued": len(out["queued"]),
        "skipped": out["skipped"][:5],
        "problems": problems[:5],
    }
