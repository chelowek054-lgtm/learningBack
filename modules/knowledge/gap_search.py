"""Поиск источников по областям, которых нет в графе (T-0084, R-0043).

Отчёт по предварительным знаниям (prior_report) называет базовые области цели, по которым в графе нет
ни одного понятия. Здесь администратор получает по каждой из них готовый запрос и одним действием
запускает поиск по белому списку, скачивание и разбор. Повторный запуск по области, пока идёт разбор
её документов, отклоняется. Всё это — только для администратора: учащийся источников не видит.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
from sqlalchemy.orm import Session

from modules.knowledge import ingest, prior_report, source_search
from modules.knowledge.models import SourceDocument
from modules.knowledge.source_search import Finder

IDLE, RUNNING = "idle", "running"
ACTIVE_STATUSES = ("queued", "running")


class GapError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def suggested_query(title: str) -> str:
    """Готовый запрос по умолчанию: название области; администратор может его поправить."""
    return title.strip()


def _documents(session: Session, key: str) -> list[dict[str, Any]]:
    docs = (
        session.query(SourceDocument)
        .filter(SourceDocument.domain == key)
        .order_by(SourceDocument.created_at.desc())
        .all()
    )
    return [ingest.progress(session, d) for d in docs]


def gaps(session: Session, user_id: uuid.UUID, goal: str, target: str) -> dict[str, Any]:
    """Области под целью без понятий в графе: запрос, идёт ли уже разбор и что с документами."""
    rep = prior_report.report(session, user_id, goal, target)
    items = []
    for area in rep["areas"]:
        if area["verdict"] != prior_report.NO_GRAPH:
            continue
        documents = _documents(session, area["key"])
        running = any(d["status"] in ACTIVE_STATUSES for d in documents)
        items.append(
            {
                "key": area["key"],
                "title": area["title"],
                "query": suggested_query(area["title"]),
                "state": RUNNING if running else IDLE,
                "documents": documents,
            }
        )
    return {"goal": goal, "target": target, "registered": rep["registered"], "gaps": items}


def fill(
    session: Session,
    user_id: uuid.UUID,
    goal: str,
    target: str,
    requests: list[dict[str, str]],
    *,
    finders: list[Finder] | None = None,
    transport: httpx.BaseTransport | None = None,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Найти и поставить в разбор источники по выбранным областям.

    `requests` — элементы `{"area": ключ, "query": запрос}`; запрос необязателен. Область должна быть
    в списке пробелов цели: искать «куда придётся» через эту ручку нельзя.
    """
    if not requests:
        raise GapError("empty", "Не выбрано ни одной области")
    current = {g["key"]: g for g in gaps(session, user_id, goal, target)["gaps"]}
    results: list[dict[str, Any]] = []
    for req in requests:
        key = req["area"]
        gap = current.get(key)
        if gap is None:
            raise GapError("not_a_gap", f"Область «{key}» не отсутствует в графе этой цели")
        if gap["state"] == RUNNING:
            results.append({"area": key, "status": "already_running", "queued": [], "skipped": []})
            continue
        query = (req.get("query") or "").strip() or gap["query"]
        try:
            candidates, problems = source_search.search(query, limit, finders)
        except source_search.SearchError as exc:
            results.append(
                {
                    "area": key,
                    "status": "bad_query",
                    "queued": [],
                    "skipped": [],
                    "problems": [str(exc)],
                }
            )
            continue
        if not candidates:
            results.append(
                {
                    "area": key,
                    "status": "nothing_found",
                    "queued": [],
                    "skipped": [],
                    "problems": problems,
                }
            )
            continue
        out = source_search.fetch_and_queue(session, user_id, key, candidates, transport=transport)
        results.append(
            {
                "area": key,
                "status": "queued" if out["queued"] else "nothing_queued",
                "queued": out["queued"],
                "skipped": out["skipped"],
                "problems": problems,
            }
        )
    return results
