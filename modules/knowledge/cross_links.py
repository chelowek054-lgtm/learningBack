"""Предпосылки между понятиями разных областей (T-0065, R-0036, A-0022).

Курс подтягивает из базовых областей не всю область, а предков конкретных понятий и только тех
ступеней, которые требует цель: чтобы «понять своими словами», хватает меньшего, чем чтобы
«решить задачу». Порядок областей берётся из графа областей: предпосылка должна лежать в
области, которая в нём ниже.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from modules.knowledge import domains
from modules.knowledge.assessment import BLOOM_LEVELS
from modules.knowledge.mastery import prerequisite_map
from modules.knowledge.models import Concept, ConceptLink, Domain


class LinkError(ValueError):
    """Связь отклонена; `code` — для тестов и клиента."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _bloom_index(bloom: str) -> int:
    if bloom not in BLOOM_LEVELS:
        raise LinkError("unknown_bloom", f"Неизвестная ступень освоения: {bloom!r}")
    return BLOOM_LEVELS.index(bloom)


def _registered(session: Session, concept: Concept) -> Domain:
    domain = session.get(Domain, domains.normalize(concept.domain))
    if domain is None:
        raise LinkError(
            "domain_unregistered",
            f"Область «{concept.domain}» не внесена в граф областей: сначала заведите её там",
        )
    return domain


def _link_reaches(session: Session, start: uuid.UUID, target: uuid.UUID) -> bool:
    """Достижимо ли `target` из `start`, идя от предпосылки к зависимому понятию."""
    forward: dict[uuid.UUID, list[uuid.UUID]] = {}
    for link in session.query(ConceptLink).all():
        forward.setdefault(link.from_id, []).append(link.to_id)
    stack, seen = [start], set()
    while stack:
        cur = stack.pop()
        if cur == target:
            return True
        if cur not in seen:
            seen.add(cur)
            stack.extend(forward.get(cur, ()))
    return False


def add_link(session: Session, from_id: uuid.UUID, to_id: uuid.UUID, bloom: str) -> ConceptLink:
    """Связь «`from_id` нужно знать, чтобы освоить `to_id` до ступени `bloom`»."""
    _bloom_index(bloom)
    if from_id == to_id:
        raise LinkError("self_link", "Понятие не может требовать само себя")
    source, target = session.get(Concept, from_id), session.get(Concept, to_id)
    if source is None or target is None:
        raise LinkError("unknown_concept", "Неизвестное понятие")
    if source.domain == target.domain:
        raise LinkError("same_domain", "Внутри области предпосылки задаёт обычное ребро графа")
    lower, upper = _registered(session, source), _registered(session, target)
    if lower.key not in {c["key"] for c in domains.chain(session, upper.key)}:
        raise LinkError(
            "domain_order",
            f"«{source.domain}» не лежит ниже «{target.domain}» в графе областей: "
            "сначала внесите связь между областями",
        )
    existing = session.query(ConceptLink).filter_by(from_id=from_id, to_id=to_id).one_or_none()
    if existing is not None:
        existing.bloom = bloom
        session.flush()
        return existing
    if _link_reaches(session, to_id, from_id):
        raise LinkError("cycle", "Связь замкнула бы цикл между понятиями")
    link = ConceptLink(from_id=from_id, to_id=to_id, bloom=bloom)
    session.add(link)
    session.flush()
    return link


def links_into(session: Session, concept_id: uuid.UUID) -> list[ConceptLink]:
    return session.query(ConceptLink).filter_by(to_id=concept_id).all()


def required_ancestors(
    session: Session, concept_ids: list[uuid.UUID], target_bloom: str
) -> dict[uuid.UUID, str]:
    """Предки из других областей, нужные для `concept_ids` на ступени `target_bloom`.

    Результат: понятие → ступень, до которой его нужно освоить. Идём по связям вглубь, пока они
    обязательны на ступени зависимого понятия, и берём внутриобластные предпосылки найденных
    понятий. Если понятие нужно с нескольких сторон, берётся более высокая ступень.
    """
    cap = _bloom_index(target_bloom)
    result: dict[uuid.UUID, int] = {}
    frontier: list[tuple[uuid.UUID, int]] = [(cid, cap) for cid in concept_ids]
    start = set(concept_ids)
    domain_prereqs: dict[str, dict[uuid.UUID, list[uuid.UUID]]] = {}
    while frontier:
        node, stage = frontier.pop()
        parents: list[tuple[uuid.UUID, int]] = [
            (link.from_id, _bloom_index(link.bloom))
            for link in links_into(session, node)
            if _bloom_index(link.bloom) <= stage
        ]
        if node not in start:
            concept = session.get(Concept, node)
            if concept is not None:
                pm = domain_prereqs.setdefault(
                    concept.domain, prerequisite_map(session, concept.domain)
                )
                parents.extend((p, stage) for p in pm.get(node, ()))
        for parent, parent_stage in parents:
            if parent in start or result.get(parent, -1) >= parent_stage:
                continue
            result[parent] = parent_stage
            frontier.append((parent, parent_stage))
    return {cid: BLOOM_LEVELS[i] for cid, i in result.items()}
