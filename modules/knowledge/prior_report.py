"""Отчёт по предварительным знаниям: хватает, мало, нет (T-0075, R-0042).

Тест по цепочке базовых областей (chain_placement) даёт освоенность понятий. Здесь она сверяется с
графом: по каждой базовой области цели — хватает ли человеку знаний для цели, мало или нет, и есть ли
эта область в графе вообще. Тест можно пропустить: тогда области остаются «не проверено», а курс
строится так, будто человек ничего не знает. Модель не вызывается — отчёт считается по данным.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from modules.knowledge import chain_placement, domains, provenance
from modules.knowledge.chain_placement import IMPLIED, KNOWN, UNKNOWN
from modules.knowledge.models import Concept, Domain

ENOUGH = "enough"  # хватает: всё нужное из области освоено или снято освоенным вышестоящим
PARTIAL = "partial"  # мало: часть освоена, часть нет
NONE = "none"  # нет: человека спрашивали, и ничего из нужного он не показал
UNCHECKED = "unchecked"  # не проверено (тест пропущен или до области не дошёл)
NO_GRAPH = "no_graph"  # области в графе нет вообще: сверять не с чем, нужны источники

VERDICTS = (ENOUGH, PARTIAL, NONE, UNCHECKED, NO_GRAPH)


def _built(session: Session) -> dict[str, tuple[int, int]]:
    """По нормализованному ключу области: сколько понятий построено и сколько из них черновых."""
    out: dict[str, tuple[int, int]] = {}
    rows = (
        session.query(Concept.domain, Concept.status, func.count(Concept.id))
        .group_by(Concept.domain, Concept.status)
        .all()
    )
    for name, status, n in rows:
        key = domains.normalize(name)
        total, drafts = out.get(key, (0, 0))
        out[key] = (total + n, drafts + (n if status == provenance.DRAFT else 0))
    return out


def _verdict(known: int, implied: int, unknown: int, answered: int) -> str:
    if unknown == 0:
        return ENOUGH
    if known + implied > 0:
        return PARTIAL
    return NONE if answered > 0 else UNCHECKED


def report(session: Session, user_id: uuid.UUID, domain: str, target_bloom: str) -> dict[str, Any]:
    """Области под целью сверху вниз, по каждой — вердикт; плюс что в графе отсутствует."""
    key = domains.normalize(domain)
    if session.get(Domain, key) is None:
        return {
            "goal": domain,
            "registered": False,
            "target": target_bloom,
            "areas": [],
            "summary": {},
        }

    chain = domains.chain(session, key)
    built = _built(session)
    needed = chain_placement.needed_nodes(session, domain, target_bloom)
    concepts, status, states = chain_placement.classify(session, user_id, needed)

    per: dict[str, dict[str, int]] = {}
    for cid, concept in concepts.items():
        row = per.setdefault(
            domains.normalize(concept.domain),
            {"needed": 0, KNOWN: 0, IMPLIED: 0, UNKNOWN: 0, "answered": 0},
        )
        row["needed"] += 1
        row[status[cid]] += 1
        if states[cid].observations > 0:
            row["answered"] += 1

    areas = []
    for item in chain:
        total, drafts = built.get(item["key"], (0, 0))
        row = per.get(item["key"])
        if total == 0:
            verdict = NO_GRAPH
        elif row is None:
            # Область построена, но цель на этой ступени от неё ничего не требует (или цели ещё нет).
            if needed:
                continue
            verdict = UNCHECKED
        else:
            verdict = _verdict(row[KNOWN], row[IMPLIED], row[UNKNOWN], row["answered"])
        row = row or {"needed": 0, KNOWN: 0, IMPLIED: 0, UNKNOWN: 0, "answered": 0}
        areas.append(
            {
                "key": item["key"],
                "title": item["title"],
                "level": item["level"],
                "verdict": verdict,
                "needed": row["needed"],
                "known": row[KNOWN] + row[IMPLIED],
                "answered": row["answered"],
                "concepts": total,
                "drafts": drafts,
            }
        )
    # Сверху вниз: самая сложная область первой, как и идёт проверка.
    areas.sort(key=lambda a: (-a["level"], a["key"]))

    summary = {v: sum(1 for a in areas if a["verdict"] == v) for v in VERDICTS}
    return {
        "goal": domain,
        "registered": True,
        "target": target_bloom,
        "areas": areas,
        "summary": summary,
        # Что нужно добавить в граф, прежде чем тест по этим областям что-то значит.
        "missing": [a["key"] for a in areas if a["verdict"] == NO_GRAPH],
        "checked": any(a["answered"] for a in areas),
    }
