"""Генерация курса как симуляции развития (KG5).

Курс — не топосорт графа, а проигранная траектория углубления в область
(05-knowledge-model §7). Отсюда четыре стадии, и порядок между ними важнее
любой оптимизации длины пути:

  1. УКОРЕНЕНИЕ — непокрытое фундаментальное ядро идёт первым, независимо от
     цели: вести к вершине в обход ядра бессмысленно (инвариант №4 слоя).
  2. ДИФФЕРЕНЦИАЦИЯ — от границы знаний вглубь, общее→частное, и только в ЗБР:
     узел не вводится, пока его предпосылки не пройдены или не освоены.
  3. ВЕТВЛЕНИЕ — `derived`-ветки в сторону заявленного интереса.
  4. СПИРАЛЬ — узлы ядра пере-поднимаются на более высокую ступень Блума,
     когда цель выше той, что уже достигнута.

Под каждый узел разворачивается цепочка активностей с нарастанием Блума;
движок Activity про граф по-прежнему не знает — порядок задаёт этот модуль.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from modules.knowledge.assessment import BLOOM_LEVELS
from modules.knowledge.mastery import (
    KNOWN_THRESHOLD,
    MasteryState,
    load_map,
    prerequisite_map,
)
from modules.knowledge.models import Concept, ConceptEdge, Course

# Почему узел попал в путь — это объяснение курса, а не отладочная метка.
ROOTING = "rooting"
DIFFERENTIATION = "differentiation"
BRANCH = "branch"
SPIRAL = "spiral"

# Ступень, ниже которой изучать узел заново не нужно — достаточно повторения.
_STUDY_BLOOM = "understand"
# Через сколько шагов вставлять повторение ранее пройденного (interleaving).
_INTERLEAVE_EVERY = 3


def _chain(concept: Concept, bloom: str, *, spiral: bool, has_misconception: bool) -> list[dict]:
    """Цепочка активностей под узел: изучить → вспомнить → применить → удержать."""
    target = BLOOM_LEVELS.index(bloom)
    chain: list[dict[str, Any]] = []

    # На спирали теорию заново не читают — возвращаются сразу к работе с ней.
    if not spiral:
        chain.append({"type": "concept_study", "bloom": "remember"})
    chain.append({"type": "concept_recall", "bloom": "understand"})
    if has_misconception:
        # Заблуждение чинится противопоставлением, а не повторением (§7.1).
        chain.append({"type": "concept_contrast", "bloom": "understand"})
    if target >= BLOOM_LEVELS.index("apply"):
        chain.append({"type": "concept_apply", "bloom": "apply"})
    chain.append({"type": "srs", "bloom": "remember"})
    return chain


def _step(
    concept: Concept, bloom: str, reason: str, *, has_misconception: bool = False
) -> dict[str, Any]:
    return {
        "conceptId": str(concept.id),
        "title": concept.title,
        "tier": concept.tier,
        "centrality": concept.centrality,
        "bloom": bloom,
        "reason": reason,
        "activities": _chain(
            concept, bloom, spiral=reason == SPIRAL, has_misconception=has_misconception
        ),
    }


def _capped_bloom(concept: Concept, target: str) -> str:
    """Не выше цели и не выше того, что узел поддерживает."""
    cap = BLOOM_LEVELS.index(target)
    available = [
        b
        for b in (concept.bloom_levels or [])
        if b in BLOOM_LEVELS and BLOOM_LEVELS.index(b) <= cap
    ]
    return max(available, key=BLOOM_LEVELS.index) if available else BLOOM_LEVELS[0]


def _misconception_nodes(session: Session, ids: set[uuid.UUID]) -> set[uuid.UUID]:
    edges = session.query(ConceptEdge).filter(ConceptEdge.type == "misconception").all()
    return {e.to_id for e in edges if e.to_id in ids} | {
        e.from_id for e in edges if e.from_id in ids
    }


def _ready(
    concept_id: uuid.UUID,
    prereqs: dict[uuid.UUID, list[uuid.UUID]],
    states: dict[uuid.UUID, MasteryState],
    planned: set[uuid.UUID],
) -> bool:
    """ЗБР: узел вводится, только если предпосылки освоены либо уже в пути."""
    return all(
        parent in planned or states.get(parent, MasteryState()).estimate >= KNOWN_THRESHOLD
        for parent in prereqs.get(concept_id, [])
    )


def build_path(
    session: Session,
    user_id: uuid.UUID,
    domain: str,
    target_bloom: str,
    interests: list[uuid.UUID] | None = None,
) -> list[dict[str, Any]]:
    """Путь по графу от текущей границы до цели."""
    if target_bloom not in BLOOM_LEVELS:
        raise ValueError(f"неизвестная ступень Блума: {target_bloom!r}")

    concepts = {c.id: c for c in session.query(Concept).filter(Concept.domain == domain).all()}
    if not concepts:
        return []
    prereqs = prerequisite_map(session, domain)
    states = load_map(session, user_id, domain)
    broken = _misconception_nodes(session, set(concepts))
    wanted = set(interests or [])

    def known(cid: uuid.UUID) -> bool:
        return states.get(cid, MasteryState()).estimate >= KNOWN_THRESHOLD

    path: list[dict[str, Any]] = []
    planned: set[uuid.UUID] = set()

    def emit(concept: Concept, reason: str, bloom: str | None = None) -> None:
        path.append(
            _step(
                concept,
                bloom or _capped_bloom(concept, target_bloom),
                reason,
                has_misconception=concept.id in broken,
            )
        )
        planned.add(concept.id)

    def drain(candidates: list[Concept], reason: str) -> None:
        """Добавлять готовые узлы, пока путь растёт: порядок диктуют предпосылки."""
        remaining = list(candidates)
        while remaining:
            ready = [c for c in remaining if _ready(c.id, prereqs, states, planned)]
            if not ready:
                break  # остаток заблокирован предпосылками вне выборки
            ready.sort(key=lambda c: (-c.centrality, c.title))
            for concept in ready:
                emit(concept, reason)
                remaining.remove(concept)

    # 1. Укоренение: сначала непокрытое ядро, что бы ни было целью.
    drain(
        [c for c in concepts.values() if c.tier == "core" and not known(c.id)],
        ROOTING,
    )

    # 2. Дифференциация: остальные неосвоенные узлы от границы вглубь.
    drain(
        [
            c
            for c in concepts.values()
            if c.id not in planned and not known(c.id) and c.id not in wanted
        ],
        DIFFERENTIATION,
    )

    # 3. Ветвление: то, ради чего пришли, — после того как под это есть опора.
    drain([concepts[i] for i in wanted if i in concepts and i not in planned], BRANCH)

    # 4. Спираль: ядро, освоенное ниже цели, поднимаем на ступень выше.
    for concept in sorted(concepts.values(), key=lambda c: (-c.centrality, c.title)):
        if concept.tier != "core" or concept.id in planned or not known(concept.id):
            continue
        reached = states[concept.id].bloom_reached
        bloom = _capped_bloom(concept, target_bloom)
        if reached is None or BLOOM_LEVELS.index(reached) < BLOOM_LEVELS.index(bloom):
            emit(concept, SPIRAL, bloom)

    return _interleave(path)


def _interleave(path: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Вкраплять повторение ранее пройденного узла — против зубрёжки подряд.

    Порядок узлов не меняется: добавляется только srs-активность на более
    ранний узел, чтобы материал возвращался, а не оставался позади.
    """
    for index, step in enumerate(path):
        if index and index % _INTERLEAVE_EVERY == 0:
            earlier = path[index - _INTERLEAVE_EVERY]
            step["activities"].append(
                {
                    "type": "srs",
                    "bloom": "remember",
                    "conceptId": earlier["conceptId"],
                    "note": "повторение ранее пройденного",
                }
            )
    return path


def generate_course(
    session: Session,
    user_id: uuid.UUID,
    domain: str,
    target_bloom: str,
    interests: list[uuid.UUID] | None = None,
) -> Course:
    """Построить курс и сохранить его, заменив прежний по этому домену."""
    path = build_path(session, user_id, domain, target_bloom, interests)

    course = session.query(Course).filter_by(user_id=user_id, domain=domain).one_or_none()
    target = {"bloom": target_bloom, "concepts": [str(i) for i in (interests or [])]}
    if course is None:
        course = Course(user_id=user_id, domain=domain, target=target, path=path, progress={})
        session.add(course)
    else:
        course.target = target
        course.path = path
        # Прогресс переносим: пройденные узлы остаются пройденными, даже если
        # путь пересобран после ретеста.
        course.progress = {"completed": _kept_progress(course.progress, path)}
    session.flush()
    return course


def _kept_progress(progress: Any, path: list[dict[str, Any]]) -> list[str]:
    done = set((progress or {}).get("completed") or [])
    return [step["conceptId"] for step in path if step["conceptId"] in done]


def course_view(course: Course) -> dict[str, Any]:
    """Курс для клиента: путь, прогресс и что делать сейчас."""
    completed = set((course.progress or {}).get("completed") or [])
    steps = [{**step, "done": step["conceptId"] in completed} for step in (course.path or [])]
    current = next((s for s in steps if not s["done"]), None)
    return {
        "domain": course.domain,
        "target": course.target,
        "steps": steps,
        "completed": len(completed),
        "total": len(steps),
        "current": current,
    }


def mark_completed(session: Session, course: Course, concept_id: str) -> Course:
    done = set((course.progress or {}).get("completed") or [])
    done.add(concept_id)
    order = [s["conceptId"] for s in (course.path or [])]
    course.progress = {"completed": [c for c in order if c in done]}
    session.flush()
    return course
