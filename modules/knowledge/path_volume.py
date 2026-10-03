"""Предпросмотр объёма пути перед построением (T-0067, R-0038).

Человек должен видеть, во что ввязывается, до построения: сколько базовых областей лежит под целью и
сколько в них понятий, и выбрать полный путь или короткий интуитивный. Расчёт — по графу областей и
межобластным связям; он ничего не строит и не тратит модель.

Интуитивный вариант — цель «понять своими словами»: связи, обязательные только для более высоких
ступеней, в него не входят (см. cross_links.required_ancestors).
"""

from __future__ import annotations

from sqlalchemy import func
from sqlalchemy.orm import Session

from modules.knowledge import cross_links, domains
from modules.knowledge.models import Concept, Domain

INTUITIVE_BLOOM = "understand"


def _built_counts(session: Session) -> dict[str, int]:
    """Сколько понятий построено в каждой области (по нормализованному ключу)."""
    counts: dict[str, int] = {}
    for name, n in session.query(Concept.domain, func.count(Concept.id)).group_by(Concept.domain):
        key = domains.normalize(name)
        counts[key] = counts.get(key, 0) + n
    return counts


def _variant(
    session: Session, domain: str, bloom: str, built: dict[str, int], chain_keys: list[str]
) -> dict:
    goal_ids = [c.id for c in session.query(Concept).filter(Concept.domain == domain).all()]
    levels = domains.levels(session)
    if goal_ids:
        # Цель уже построена: считаем точно — только нужные предки на этой ступени.
        needed = cross_links.required_ancestors(session, goal_ids, bloom)
        per: dict[str, int] = {}
        for c in session.query(Concept).filter(Concept.id.in_(list(needed) or [None])):
            k = domains.normalize(c.domain)
            per[k] = per.get(k, 0) + 1
        precise = True
    else:
        # Цели ещё нет: оценка по всем построенным понятиям областей под ней.
        per = {k: built.get(k, 0) for k in chain_keys}
        precise = False
    rows = [
        {
            "key": k,
            "level": levels.get(k, 0),
            "concepts": per.get(k, 0),
            "built": built.get(k, 0) > 0,
        }
        for k in chain_keys
        if precise is False or per.get(k, 0) > 0
    ]
    return {
        "bloom": bloom,
        "precise": precise,
        "domains": rows,
        "domainCount": len(rows),
        "conceptCount": sum(r["concepts"] for r in rows),
        "unbuilt": [r["key"] for r in rows if not r["built"]],
    }


def volume(session: Session, domain: str, target_bloom: str) -> dict:
    """Объём пути до цели в `domain`: полный и интуитивный варианты."""
    key = domains.normalize(domain)
    if session.get(Domain, key) is None:
        return {"goal": domain, "registered": False, "variants": {}}
    chain = domains.chain(session, key)
    chain_keys = [c["key"] for c in chain]
    built = _built_counts(session)
    full = _variant(session, domain, target_bloom, built, chain_keys)
    intuitive = _variant(session, domain, INTUITIVE_BLOOM, built, chain_keys)
    return {
        "goal": domain,
        "registered": True,
        "variants": {"full": full, "intuitive": intuitive},
        # Если цель и так «понять», выбирать не из чего.
        "differs": full["conceptCount"] != intuitive["conceptCount"],
    }
