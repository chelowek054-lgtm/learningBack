"""Адаптивный плейсмент (KG4-02, KG4-03).

Первый шаг задаёт пользователь — целевую ступень Блума. Дальше зонд подбирается
там, где система знает меньше всего, но спрашивать уже осмысленно: на
**границе знаний** (предпосылки освоены), а не в наугад выбранном узле.

Останов — по бюджету зондов либо когда на границе не осталось узлов с заметной
неопределённостью. Итог — карта освоенности, с которой KG5 строит курс.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from modules.knowledge.assessment import BLOOM_LEVELS, NotGroundable
from modules.knowledge.assessment_store import get_or_generate
from modules.knowledge.mastery import (
    CONFIDENT_ENOUGH,
    MasteryState,
    load_map,
    prerequisite_map,
    prerequisites_known,
    save_state,
    status_for,
    update,
)
from modules.knowledge.models import Concept

PROBE_KIND = "probe"
# Ниже этой неопределённости узел не стоит зондировать — уже понятно.
MIN_UNCERTAINTY = 1 - CONFIDENT_ENOUGH
# Насколько фундаментальность узла повышает приоритет зондирования.
CENTRALITY_WEIGHT = 0.5


class NoProbeAvailable(RuntimeError):
    """Зондировать больше нечего: граница исчерпана или узлы без теории."""


def probe_bloom(concept: Concept, target: str) -> str:
    """Не выше цели и не выше того, что узел вообще поддерживает."""
    if target not in BLOOM_LEVELS:
        raise ValueError(f"неизвестная ступень Блума: {target!r}")
    cap = BLOOM_LEVELS.index(target)
    available = [
        b for b in (concept.bloom_levels or []) if b in BLOOM_LEVELS and BLOOM_LEVELS.index(b) <= cap
    ]
    if not available:
        return BLOOM_LEVELS[0]
    return max(available, key=BLOOM_LEVELS.index)


def rank_candidates(
    session: Session, user_id: uuid.UUID, domain: str
) -> list[tuple[Concept, MasteryState, float]]:
    """Узлы границы, отсортированные по «сколько даст ответ»."""
    concepts = {c.id: c for c in session.query(Concept).filter(Concept.domain == domain).all()}
    prereqs = prerequisite_map(session, domain)
    states = load_map(session, user_id, domain)

    ranked: list[tuple[Concept, MasteryState, float]] = []
    for concept_id, concept in concepts.items():
        state = states.get(concept_id, MasteryState())
        if not prerequisites_known(concept_id, prereqs, states):
            continue  # спрашивать про узел, предпосылки которого не освоены, бессмысленно
        if state.uncertainty < MIN_UNCERTAINTY:
            continue
        score = state.uncertainty * (1 + CENTRALITY_WEIGHT * concept.centrality)
        ranked.append((concept, state, round(score, 4)))

    ranked.sort(key=lambda row: (-row[2], row[0].title))
    return ranked


def next_probe(
    session: Session, user_id: uuid.UUID, domain: str, target_bloom: str
) -> dict[str, Any]:
    """Следующий зонд: узел границы + задание из кэша (или сгенерированное)."""
    for concept, state, score in rank_candidates(session, user_id, domain):
        bloom = probe_bloom(concept, target_bloom)
        try:
            payload, cached = get_or_generate(session, concept, bloom, PROBE_KIND)
        except NotGroundable:
            continue  # узел без теории зондировать нечем — берём следующий
        item = payload.items[0]
        return {
            "conceptId": str(concept.id),
            "conceptTitle": concept.title,
            "conceptVersion": concept.version,
            "bloom": bloom,
            "cached": cached,
            "priority": score,
            "uncertainty": round(state.uncertainty, 3),
            "item": item.model_dump(),
        }
    raise NoProbeAvailable("на границе знаний не осталось узлов, которые стоит зондировать")


def record_answer(
    session: Session,
    user_id: uuid.UUID,
    domain: str,
    concept_id: uuid.UUID,
    bloom: str,
    score: float,
) -> MasteryState:
    """Обновить освоенность узла одним наблюдением."""
    states = load_map(session, user_id, domain)
    before = states.get(concept_id, MasteryState())
    after = update(before, score, bloom)
    save_state(session, user_id, domain, concept_id, after)
    return after


def placement_map(session: Session, user_id: uuid.UUID, domain: str) -> dict[str, Any]:
    """Карта освоенности: где пользователь, где граница, что закрыто."""
    concepts = {c.id: c for c in session.query(Concept).filter(Concept.domain == domain).all()}
    prereqs = prerequisite_map(session, domain)
    states = load_map(session, user_id, domain)

    nodes = []
    for concept_id, concept in concepts.items():
        state = states.get(concept_id, MasteryState())
        known = prerequisites_known(concept_id, prereqs, states)
        nodes.append(
            {
                "conceptId": str(concept_id),
                "title": concept.title,
                "tier": concept.tier,
                "centrality": concept.centrality,
                "status": status_for(state, known),
                **state.dump(),
            }
        )
    nodes.sort(key=lambda n: (-n["centrality"], n["title"]))

    counts: dict[str, int] = {}
    for node in nodes:
        counts[node["status"]] = counts.get(node["status"], 0) + 1
    return {
        "domain": domain,
        "nodes": nodes,
        "summary": counts,
        "coreCovered": all(
            n["status"] == "known" for n in nodes if n["tier"] == "core"
        ),
    }
