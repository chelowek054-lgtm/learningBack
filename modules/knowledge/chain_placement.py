"""Проверка уровня по цепочке базовых областей сверху вниз (T-0066, R-0037, A-0022).

По умолчанию путь строится в предположении, что человек ничего не знает. Чтобы взрослый и
второклассник не получали одинаково длинный путь, нужные понятия базовых областей проверяются
от сложных областей к простым, и освоенное верхнее понятие снимает проверку тех, что лежат под
ним: кто знает вышестоящее, нижестоящее проверять незачем. Задания и оценка ответов — те же, что
у плейсмента одной области; здесь только выбор, о чём спрашивать и что считать освоенным.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from modules.knowledge import cross_links, domains
from modules.knowledge.assessment_store import get_or_generate
from modules.knowledge.assessment import NotGroundable
from modules.knowledge.mastery import KNOWN_THRESHOLD, MasteryState, load_map
from modules.knowledge.models import Concept
from modules.knowledge.placement import (
    CENTRALITY_WEIGHT,
    PROBE_KIND,
    NoProbeAvailable,
    probe_bloom,
)

# Уверенность оценки, с которой «не освоено» считается установленным.
SETTLED_CONFIDENCE = 0.5

KNOWN = "known"
IMPLIED = "implied"
UNKNOWN = "unknown"


def needed_nodes(session: Session, domain: str, target_bloom: str) -> dict[uuid.UUID, str]:
    """Понятия других областей, нужные для цели в `domain` на ступени `target_bloom`."""
    goal = [c.id for c in session.query(Concept).filter(Concept.domain == domain).all()]
    return cross_links.required_ancestors(session, goal, target_bloom)


def _states(session: Session, user_id: uuid.UUID, concepts: dict[uuid.UUID, Concept]):
    cache: dict[str, dict[uuid.UUID, MasteryState]] = {}
    out: dict[uuid.UUID, MasteryState] = {}
    for cid, concept in concepts.items():
        domain_states = cache.setdefault(concept.domain, load_map(session, user_id, concept.domain))
        out[cid] = domain_states.get(cid, MasteryState())
    return out


def _is_known(state: MasteryState) -> bool:
    # Приор от предпосылок — не знание: освоенным считается то, о чём человек уже отвечал.
    return state.observations > 0 and state.estimate >= KNOWN_THRESHOLD


def classify(
    session: Session, user_id: uuid.UUID, needed: dict[uuid.UUID, str]
) -> tuple[dict[uuid.UUID, Concept], dict[uuid.UUID, str], dict[uuid.UUID, MasteryState]]:
    """Состояние каждого нужного понятия: освоено, снято освоенным вышестоящим, не освоено."""
    concepts = {
        c.id: c for c in session.query(Concept).filter(Concept.id.in_(list(needed) or [None])).all()
    }
    states = _states(session, user_id, concepts)
    status = {cid: (KNOWN if _is_known(states[cid]) else UNKNOWN) for cid in concepts}
    for cid in [c for c, s in status.items() if s == KNOWN]:
        # Всё, что нужно для освоенного понятия, считается освоенным без проверки.
        for below in cross_links.required_ancestors(session, [cid], needed[cid]):
            if status.get(below) == UNKNOWN:
                status[below] = IMPLIED
    return concepts, status, states


def implied_known(
    session: Session, user_id: uuid.UUID, needed: dict[uuid.UUID, str]
) -> set[uuid.UUID]:
    """Понятия, которые проверять не нужно: нижестоящие под освоенным (для курса)."""
    _, status, _ = classify(session, user_id, needed)
    return {cid for cid, s in status.items() if s in (KNOWN, IMPLIED)}


def chain_plan(
    session: Session, user_id: uuid.UUID, domain: str, target_bloom: str
) -> list[dict[str, Any]]:
    """Области цепочки сверху вниз и состояние их нужных понятий."""
    needed = needed_nodes(session, domain, target_bloom)
    concepts, status, _ = classify(session, user_id, needed)
    level = domains.levels(session)
    by_domain: dict[str, list[dict[str, Any]]] = {}
    for cid, concept in concepts.items():
        by_domain.setdefault(concept.domain, []).append(
            {
                "conceptId": str(cid),
                "title": concept.title,
                "bloom": needed[cid],
                "state": status[cid],
            }
        )
    plan = []
    for dom, nodes in by_domain.items():
        nodes.sort(key=lambda n: n["title"])
        done = all(n["state"] != UNKNOWN for n in nodes)
        plan.append(
            {
                "domain": dom,
                "level": level.get(domains.normalize(dom), 0),
                "state": KNOWN if done else "pending",
                "nodes": nodes,
            }
        )
    return sorted(plan, key=lambda p: (-p["level"], p["domain"]))


def next_chain_probe(
    session: Session, user_id: uuid.UUID, domain: str, target_bloom: str
) -> dict[str, Any]:
    """Следующий зонд по цепочке: самая сложная область с непроверенным понятием."""
    needed = needed_nodes(session, domain, target_bloom)
    if not needed:
        raise NoProbeAvailable("для этой цели базовых областей не нужно", "no_foundations")
    concepts, status, states = classify(session, user_id, needed)
    level = domains.levels(session)

    # Понятие, по которому уже набралось достаточно ответов и оно не освоено, повторно не
    # спрашивают: проверка идёт вниз, к тому, чего ему не хватает.
    unknown = [
        c
        for c, s in status.items()
        if s == UNKNOWN
        and not (states[c].observations > 0 and states[c].confidence >= SETTLED_CONFIDENCE)
    ]
    if not unknown:
        raise NoProbeAvailable("вся цепочка освоена или снята освоенным вышестоящим", "settled")

    def order(cid: uuid.UUID) -> tuple:
        concept, state = concepts[cid], states[cid]
        weight = state.uncertainty * (1 + CENTRALITY_WEIGHT * concept.centrality)
        return (-level.get(domains.normalize(concept.domain), 0), -weight, concept.title)

    for cid in sorted(unknown, key=order):
        concept = concepts[cid]
        bloom = probe_bloom(concept, needed[cid])
        try:
            payload, cached = get_or_generate(session, concept, bloom, PROBE_KIND)
        except NotGroundable:
            continue
        return {
            "conceptId": str(cid),
            "conceptTitle": concept.title,
            "conceptVersion": concept.version,
            "domain": concept.domain,
            "bloom": bloom,
            "cached": cached,
            "item": payload.items[0].model_dump(),
        }
    raise NoProbeAvailable(
        "у непроверенных понятий нет теории, по которой можно спросить", "no_theory"
    )
