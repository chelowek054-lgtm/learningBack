"""Модель освоенности узла (KG4-01).

Оценка держится на бета-распределении: `alpha` — свидетельства «знает»,
`beta` — «не знает». Это **не IRT**: сложность заданий не калибруется, задача
скромнее — сузить неопределённость по каждому узлу за разумное число зондов.

Приор берётся из предпосылок: если предки по `prereq`/`specializes` освоены,
шанс, что узел тоже освоен, заметно выше — это то же допущение, на котором
стоит порядок обучения в графе.

Освоенность **всегда персональная** (05-knowledge-model §2), поэтому живёт в
`user_concept.mastery`; зондирование канонического узла заводит персональную
строку — это штатный COW, а не «копия канона».
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from modules.knowledge.assessment import BLOOM_LEVELS
from modules.knowledge.models import Concept, ConceptEdge, UserConcept

# Ребра, по которым узел считается предпосылкой другого.
PREREQ_EDGE_TYPES = ("prereq", "specializes")

# Сила приора в «виртуальных ответах»: 2 — слабый, легко перебивается данными.
PRIOR_STRENGTH = 2.0
# Приор узла без предпосылок: про нового пользователя мы почти ничего не знаем.
PRIOR_ROOT = 0.20
# Шанс знать узел ПРИ УСЛОВИИ освоенных предпосылок.
PRIOR_CONDITIONAL = 0.70
# Границы, чтобы приор оставался вероятностью и не вырождался.
PRIOR_MIN = 0.02
PRIOR_MAX = 0.95

KNOWN_THRESHOLD = 0.75
CONFIDENT_ENOUGH = 0.6

# Сколько наблюдений даёт уверенность 0.5 (мягкая шкала «сколько мы видели»).
CONFIDENCE_HALFLIFE = 3.0


class MasteryState(BaseModel):
    alpha: float = Field(default=1.0, gt=0)
    beta: float = Field(default=1.0, gt=0)
    bloom_reached: str | None = None
    observations: int = 0

    @property
    def estimate(self) -> float:
        return self.alpha / (self.alpha + self.beta)

    @property
    def confidence(self) -> float:
        """Насколько мы верим оценке — по ОБЪЁМУ свидетельств, а не по остроте приора.

        Через разброс бета-распределения считать нельзя: перекошенный приор
        (например 0.3/1.7 у узла без освоенных предпосылок) выглядит «узким», и
        первый же ответ, противоречащий догадке, увеличивал бы неопределённость
        вместо того, чтобы её снижать.
        """
        n = self.observations
        return round(n / (n + CONFIDENCE_HALFLIFE), 3)

    @property
    def uncertainty(self) -> float:
        return 1 - self.confidence

    def dump(self) -> dict[str, Any]:
        """Плоская форма для jsonb: производные поля тоже пишем — их читает клиент."""
        return {
            "alpha": round(self.alpha, 4),
            "beta": round(self.beta, 4),
            "estimate": round(self.estimate, 3),
            "confidence": self.confidence,
            "bloom_reached": self.bloom_reached,
            "observations": self.observations,
        }


def load_state(raw: Any) -> MasteryState:
    if not isinstance(raw, dict) or "alpha" not in raw:
        return MasteryState()
    try:
        return MasteryState.model_validate(raw)
    except ValueError:
        return MasteryState()


def prior_from_prerequisites(prereq_estimates: list[float]) -> MasteryState:
    """Приор узла по освоенности его предпосылок.

    Берётся МИНИМУМ, а не среднее: пропущенной одной предпосылки достаточно,
    чтобы узел был недоступен. И связь условная (умножение), а не аддитивная —
    иначе получалась инверсия: узел, предпосылка которого не освоена, получал
    приор ВЫШЕ, чем корневой, то есть чем глубже в графе, тем «вероятнее знает».
    """
    if not prereq_estimates:
        center = PRIOR_ROOT
    else:
        center = min(prereq_estimates) * PRIOR_CONDITIONAL
    center = max(PRIOR_MIN, min(PRIOR_MAX, center))
    return MasteryState(
        alpha=center * PRIOR_STRENGTH,
        beta=(1 - center) * PRIOR_STRENGTH,
    )


def update(state: MasteryState, score: float, bloom: str, weight: float = 1.0) -> MasteryState:
    """Байесовское обновление одним наблюдением.

    `score` — доля правильного (1 за верный ответ, 0 за неверный, между —
    частично верный ответ по оценке AI).
    """
    score = max(0.0, min(1.0, score))
    reached = state.bloom_reached
    if score >= 0.6 and bloom in BLOOM_LEVELS:
        current = BLOOM_LEVELS.index(reached) if reached in BLOOM_LEVELS else -1
        reached = bloom if BLOOM_LEVELS.index(bloom) > current else reached
    return MasteryState(
        alpha=state.alpha + weight * score,
        beta=state.beta + weight * (1 - score),
        bloom_reached=reached,
        observations=state.observations + 1,
    )


def status_for(state: MasteryState, prerequisites_known: bool) -> str:
    """locked → frontier → learning → known (05-knowledge-model §4)."""
    if state.estimate >= KNOWN_THRESHOLD and state.confidence >= CONFIDENT_ENOUGH:
        return "known"
    if not prerequisites_known:
        return "locked"
    return "learning" if state.observations else "frontier"


# ---- работа с графом ----


def prerequisite_map(session: Session, domain: str) -> dict[uuid.UUID, list[uuid.UUID]]:
    """concept_id → список его предпосылок внутри домена."""
    ids = {c.id for c in session.query(Concept).filter(Concept.domain == domain).all()}
    result: dict[uuid.UUID, list[uuid.UUID]] = {i: [] for i in ids}
    edges = session.query(ConceptEdge).filter(ConceptEdge.type.in_(PREREQ_EDGE_TYPES)).all()
    for e in edges:
        if e.from_id in ids and e.to_id in ids:
            result[e.to_id].append(e.from_id)
    return result


def load_map(session: Session, user_id: uuid.UUID, domain: str) -> dict[uuid.UUID, MasteryState]:
    """Освоенность по каждому узлу домена: записанная либо приор от предпосылок."""
    prereqs = prerequisite_map(session, domain)
    rows = {
        uc.base_concept_id: uc
        for uc in session.query(UserConcept)
        .filter(
            UserConcept.user_id == user_id,
            UserConcept.domain == domain,
            UserConcept.base_concept_id.isnot(None),
        )
        .all()
    }

    states: dict[uuid.UUID, MasteryState] = {}
    for concept_id in _topological_order(prereqs):
        row = rows.get(concept_id)
        if row is not None and isinstance(row.mastery, dict) and "alpha" in row.mastery:
            states[concept_id] = load_state(row.mastery)
            continue
        parents = [states[p].estimate for p in prereqs.get(concept_id, []) if p in states]
        states[concept_id] = prior_from_prerequisites(parents)
    return states


def prerequisites_known(
    concept_id: uuid.UUID,
    prereqs: dict[uuid.UUID, list[uuid.UUID]],
    states: dict[uuid.UUID, MasteryState],
) -> bool:
    parents = prereqs.get(concept_id, [])
    return all(states[p].estimate >= KNOWN_THRESHOLD for p in parents if p in states)


def save_state(
    session: Session,
    user_id: uuid.UUID,
    domain: str,
    concept_id: uuid.UUID,
    state: MasteryState,
) -> UserConcept:
    """Записать освоенность, заведя персональную строку при необходимости (COW)."""
    row = session.query(UserConcept).filter_by(user_id=user_id, base_concept_id=concept_id).first()
    if row is None:
        row = UserConcept(
            user_id=user_id,
            domain=domain,
            base_concept_id=concept_id,
            origin="inherited",
        )
        session.add(row)
    row.mastery = state.dump()
    session.flush()
    return row


def _topological_order(prereqs: dict[uuid.UUID, list[uuid.UUID]]) -> list[uuid.UUID]:
    """Предпосылки раньше зависимых, чтобы приор строился на уже готовых оценках.

    Граф от LLM может содержать цикл — тогда оставшиеся узлы отдаются как есть,
    порядок внутри цикла произволен, но обход завершается.
    """
    pending = {node: set(parents) for node, parents in prereqs.items()}
    ordered: list[uuid.UUID] = []
    done: set[uuid.UUID] = set()
    while pending:
        ready = [n for n, parents in pending.items() if not (parents - done)]
        if not ready:
            ordered.extend(pending)
            break
        for node in ready:
            ordered.append(node)
            done.add(node)
            del pending[node]
    return ordered
