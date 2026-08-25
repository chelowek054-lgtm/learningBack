"""Прохождение шага курса (KG5-05): граф ↔ движок Activity ↔ FSRS ↔ event log.

До этого модуля курс был планом на бумаге: шаг закрывался кнопкой. Здесь шаг
разворачивается в настоящие `activity` движка, ответ пишется в единый
`response`-лог (инвариант №4) и возвращается в модель освоенности, а слабый
узел уходит в повторение через FSRS.

Замыкается петля, ради которой строился весь слой:

    узел графа → задание из его теории → ответ → освоенность → граница
              ↘ слабый ответ → карточка FSRS, привязанная к узлу ↗

Шаг курса закрывается не нажатием, а достигнутой освоенностью.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from core.models import Activity, Response, SrsCard
from core.srs import initial_fsrs_state
from modules.knowledge.answer import score_answer
from modules.knowledge.assessment import AssessmentItem, NotGroundable
from modules.knowledge.assessment_store import get_or_generate
from modules.knowledge.content import NodeContent, coerce_content
from modules.knowledge.course import mark_completed
from modules.knowledge.mastery import KNOWN_THRESHOLD, MasteryState, load_map
from modules.knowledge.models import Concept, Course
from modules.knowledge.placement import record_answer

MODULE_ID = "knowledge"

# Ниже этого результата узел считается непонятым и возвращается через FSRS.
WEAK_SCORE = 0.6

# Какой вид задания стоит за активностью; study и srs заданий не требуют.
_ACTIVITY_KIND = {
    "concept_recall": "test",
    "concept_contrast": "test",
    "concept_apply": "practice",
}


def _connectivity(activity_type: str) -> str:
    """Читать теорию и повторять можно офлайн; проверка ответа требует сети."""
    return "offline" if activity_type in ("concept_study", "srs") else "online"


def _step_of(course: Course, concept_id: str) -> dict[str, Any] | None:
    return next((s for s in (course.path or []) if s["conceptId"] == concept_id), None)


def start_step(
    session: Session, user_id: uuid.UUID, course: Course, concept_id: str
) -> list[Activity]:
    """Развернуть шаг курса в активности движка (идемпотентно)."""
    step = _step_of(course, concept_id)
    if step is None:
        raise LookupError("шаг не найден в текущем курсе")
    concept = session.get(Concept, concept_id)
    if concept is None:
        raise LookupError("concept не найден")

    existing = {
        a.type: a
        for a in session.query(Activity)
        .filter(
            Activity.user_id == user_id,
            Activity.module == MODULE_ID,
            Activity.payload["conceptId"].astext == concept_id,
        )
        .all()
    }

    content = NodeContent.model_validate(coerce_content(concept.content))
    created: list[Activity] = []
    for planned in step["activities"]:
        activity_type = planned["type"]
        if activity_type in existing:
            created.append(existing[activity_type])
            continue
        payload = _payload(session, concept, content, activity_type, planned["bloom"])
        if payload is None:
            continue  # заданий по этому узлу не сгенерировать — активность пропускаем
        activity = Activity(
            user_id=user_id,
            module=MODULE_ID,
            type=activity_type,
            connectivity=_connectivity(activity_type),
            payload=payload,
        )
        session.add(activity)
        created.append(activity)

    _ensure_card(session, user_id, concept, content, source="generated")
    session.flush()
    return created


def _payload(
    session: Session,
    concept: Concept,
    content: NodeContent,
    activity_type: str,
    bloom: str,
) -> dict[str, Any] | None:
    base = {
        # domain кладём в payload, чтобы рендерер был самодостаточен: он получает
        # только activity и должен уметь отправить ответ.
        "domain": concept.domain,
        "conceptId": str(concept.id),
        "conceptVersion": concept.version,
        "title": concept.title,
        "bloom": bloom,
    }
    if activity_type == "concept_study":
        return {**base, "content": content.model_dump()}
    if activity_type == "srs":
        return {**base, "prompt": f"Вспомните: {concept.title}"}

    kind = _ACTIVITY_KIND.get(activity_type)
    if kind is None:
        return None
    try:
        payload, _cached = get_or_generate(session, concept, bloom, kind)
    except (NotGroundable, ValueError):
        return None
    return {**base, "item": payload.items[0].model_dump()}


def _ensure_card(
    session: Session,
    user_id: uuid.UUID,
    concept: Concept,
    content: NodeContent,
    *,
    source: str,
) -> SrsCard:
    """Карточка удержания под узел. Одна на узел — повторное падение её не дублирует."""
    now = datetime.now(timezone.utc)
    card = (
        session.query(SrsCard)
        .filter(SrsCard.user_id == user_id, SrsCard.concept_id == concept.id)
        .first()
    )
    if card is not None:
        if source == "error_log":
            # Узел не понят — вернуть его в очередь на сегодня, не сбрасывая историю.
            card.source = source
            card.due_at = now
        return card

    card = SrsCard(
        user_id=user_id,
        module=MODULE_ID,
        concept_id=concept.id,
        front={"prompt": f"Вспомните: {concept.title}"},
        back={"summary": content.summary},
        source=source,
        fsrs_state=initial_fsrs_state(now),
        due_at=now,
    )
    session.add(card)
    return card


def submit_answer(
    session: Session,
    user_id: uuid.UUID,
    course: Course,
    concept_id: str,
    activity: Activity,
    answer: Any,
) -> dict[str, Any]:
    """Оценить ответ, записать в event log, обновить освоенность и продвинуть курс."""
    concept = session.get(Concept, concept_id)
    if concept is None:
        raise LookupError("concept не найден")

    item = AssessmentItem.model_validate(activity.payload.get("item") or {"prompt": ""})
    bloom = activity.payload.get("bloom", "understand")
    score, explanation = score_answer(item, answer)

    # Единый event log: FSRS и адаптация читают только его (инвариант №4).
    session.add(
        Response(
            activity_id=activity.id,
            user_id=user_id,
            user_answer={"answer": answer},
            grade={"score": score, "explanation": explanation, "conceptId": concept_id},
            local_created_at=datetime.now(timezone.utc),
            synced=True,
        )
    )

    state = record_answer(session, user_id, course.domain, concept.id, bloom, score)

    content = NodeContent.model_validate(coerce_content(concept.content))
    if score < WEAK_SCORE:
        # Слабый узел возвращается через удержание, а не остаётся позади.
        _ensure_card(session, user_id, concept, content, source="error_log")

    # Шаг закрывается достигнутой освоенностью, а не нажатием кнопки.
    advanced = state.estimate >= KNOWN_THRESHOLD and state.confidence >= 0.6
    if advanced:
        mark_completed(session, course, concept_id)

    session.flush()
    return {
        "score": score,
        "explanation": explanation,
        "mastery": state.dump(),
        "stepCompleted": advanced,
    }


def weak_nodes(session: Session, user_id: uuid.UUID, domain: str) -> list[dict[str, Any]]:
    """Узлы, которые ретест или ошибки вернули в работу."""
    states = load_map(session, user_id, domain)
    concepts = {c.id: c for c in session.query(Concept).filter(Concept.domain == domain).all()}
    out = []
    for concept_id, state in states.items():
        concept = concepts.get(concept_id)
        if concept is None or state.observations == 0:
            continue
        if state.estimate < KNOWN_THRESHOLD:
            out.append(
                {
                    "conceptId": str(concept_id),
                    "title": concept.title,
                    **MasteryState.model_validate(state.model_dump()).dump(),
                }
            )
    out.sort(key=lambda n: n["estimate"])
    return out
