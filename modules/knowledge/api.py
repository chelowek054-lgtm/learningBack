"""Публичный интерфейс модуля графа знаний (T-0052, R-0028).

Всё, что остальным модулям и ядру можно знать о графе, — здесь: прочитать узел и его
связи, найти границу знаний, получить и записать освоенность, подписаться на изменение
узла. Внутренности (таблицы, COW, формула освоенности) снаружи не видны и могут
меняться, пока этот интерфейс стоит. Граф не знает ни предметов, ни способов
запоминания: он хранит уровень освоения, а как к нему пришли — дело способа, который
сообщает «свидетельство об освоении» (`Evidence`) в этом общем формате.
"""

from __future__ import annotations

import uuid
from typing import Any, Callable

from sqlalchemy.orm import Session

from core.evidence import Evidence

from modules.knowledge.cow import effective_graph, resolve_node
from modules.knowledge.events import NodeChanged, subscribe, unsubscribe
from modules.knowledge.mastery import MasteryState, load_map
from modules.knowledge.models import Concept, UserConcept
from modules.knowledge.placement import placement_map, record_answer

__all__ = [
    "Evidence",
    "NodeChanged",
    "frontier",
    "get_edges",
    "get_mastery",
    "get_node",
    "record_evidence",
    "subscribe",
    "unsubscribe",
]


def get_node(session: Session, user_id: uuid.UUID, node_id: uuid.UUID) -> dict[str, Any] | None:
    """Узел с теорией: канонический (с правкой пользователя) или личный; чужой — None."""
    concept = session.get(Concept, node_id)
    if concept is not None:
        override = (
            session.query(UserConcept)
            .filter_by(user_id=user_id, base_concept_id=concept.id)
            .first()
        )
        return resolve_node(concept, override)
    own = session.get(UserConcept, node_id)
    if own is None or own.user_id != user_id or own.base_concept_id is not None:
        return None
    return resolve_node(None, own)


def get_edges(
    session: Session, user_id: uuid.UUID, domain: str, node_id: uuid.UUID
) -> list[dict[str, Any]]:
    """Связи узла в графе пользователя: входящие и исходящие, канонические и личные."""
    graph = effective_graph(session, user_id, domain)
    nid = str(node_id)
    return [e for e in graph["edges"] if e["from"] == nid or e["to"] == nid]


def frontier(session: Session, user_id: uuid.UUID, domain: str) -> list[dict[str, Any]]:
    """Граница знаний: узлы, которые человек готов осваивать сейчас (предпосылки освоены)."""
    nodes = placement_map(session, user_id, domain)["nodes"]
    return [n for n in nodes if n["status"] == "frontier"]


def get_mastery(session: Session, user_id: uuid.UUID, domain: str) -> dict[str, dict[str, Any]]:
    """Освоенность по узлам области: id узла → состояние (у ненаблюдавшихся — приор по предпосылкам)."""
    return {str(cid): state.dump() for cid, state in load_map(session, user_id, domain).items()}


def record_evidence(
    session: Session, user_id: uuid.UUID, domain: str, evidence: Evidence
) -> MasteryState:
    """Записать свидетельство об освоении и вернуть новое состояние узла.

    Единственный способ сообщить графу «человек ответил»: способы запоминания не пишут в
    его таблицы сами.
    """
    if not 0.0 <= evidence.score <= 1.0:
        raise ValueError("Результат свидетельства — число от 0 до 1")
    return record_answer(
        session, user_id, domain, evidence.concept_id, evidence.bloom, evidence.score
    )


Callback = Callable[[NodeChanged], None]
