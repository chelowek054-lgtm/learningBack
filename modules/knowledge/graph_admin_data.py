"""Данные и правки схемы графа канона для админки (T-0107…T-0110, R-0058).

Чтение собирает то, что раньше лежало в трёх таблицах: понятия области с этапами, связи внутри области,
мосты между областями и граф самих областей. Правки идут теми же правилами, что и в остальном модуле:
подтверждение и отклонение — через `provenance.review_*` (с журналом и причиной), версия растёт при смене
содержимого, предпосылки не замыкаются в цикл и не дублируются. Ничего предметного здесь нет (R-0028).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from modules.knowledge import domains, provenance, stages
from modules.knowledge.content import NodeContent, coerce_content
from modules.knowledge.models import Concept, ConceptEdge, ConceptLink, ConceptSource

EDGE_TYPES = (
    "prereq",
    "specializes",
    "part_of",
    "related",
    "contrasts",
    "misconception",
    "example",
)
TIERS = ("core", "derived")
STATUSES = ("draft", "approved", "rejected")


class GraphEditError(ValueError):
    """Правка отклонена: `code` — для интерфейса, текст — человеку."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _uid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError as e:
        raise GraphEditError("bad_id", "Некорректный идентификатор") from e


def _source_counts(session: Session, ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not ids:
        return {}
    rows = (
        session.query(ConceptSource.concept_id, func.count())
        .filter(ConceptSource.concept_id.in_(ids))
        .group_by(ConceptSource.concept_id)
        .all()
    )
    return {cid: n for cid, n in rows}


def _node(c: Concept, sources: int) -> dict[str, Any]:
    content = coerce_content(c.content)
    return {
        "id": str(c.id),
        "title": c.title,
        "domain": c.domain,
        "tier": c.tier,
        "status": c.status,
        "stage": c.stage,
        "stageOrder": c.stage_order,
        "level": c.level,
        "optional": bool(c.optional),
        "centrality": round(float(c.centrality or 0), 3),
        "version": c.version,
        "summary": content.get("summary", ""),
        "hasTheory": bool(content.get("sections")),
        "sources": sources,
    }


def _edge(e: ConceptEdge) -> dict[str, Any]:
    return {
        "id": str(e.id),
        "from": str(e.from_id),
        "to": str(e.to_id),
        "type": e.type,
        "status": e.status,
    }


# ---- чтение ----


def overview(session: Session) -> list[dict[str, Any]]:
    """Области, в которых есть понятия: сколько понятий и сколько ещё черновиков."""
    rows = (
        session.query(
            Concept.domain,
            func.count(),
            func.count().filter(Concept.status == "draft"),
        )
        .group_by(Concept.domain)
        .order_by(Concept.domain)
        .all()
    )
    return [{"domain": d, "concepts": n, "drafts": dr} for d, n, dr in rows]


def area_graph(session: Session, domain: str) -> dict[str, Any]:
    """Понятия одной области и связи между ними; этапы — по возрастанию порядка."""
    concepts = session.query(Concept).filter(Concept.domain == domain).all()
    ids = [c.id for c in concepts]
    counts = _source_counts(session, ids)
    edges = (
        session.query(ConceptEdge)
        .filter(ConceptEdge.from_id.in_(ids), ConceptEdge.to_id.in_(ids))
        .all()
        if ids
        else []
    )
    stage_list = sorted(
        {(c.stage_order or 0, c.stage) for c in concepts if c.stage},
        key=lambda s: s[0],
    )
    return {
        "domain": domain,
        "nodes": [_node(c, counts.get(c.id, 0)) for c in concepts],
        "edges": [_edge(e) for e in edges],
        "stages": [{"order": o, "title": t} for o, t in stage_list],
    }


def domains_graph(session: Session) -> dict[str, Any]:
    """Сами области: узлы с числом понятий и предпосылки между ними."""
    counts = {d["domain"]: d for d in overview(session)}
    rows = domains.listing(session)
    nodes = []
    known: set[str] = set()
    for r in rows:
        known.add(r["key"])
        stat = counts.get(r["title"]) or counts.get(r["key"]) or {"concepts": 0, "drafts": 0}
        nodes.append(
            {
                "id": r["key"],
                "title": r["title"],
                "foundation": r["foundation"],
                "level": r["level"],
                "concepts": stat["concepts"],
                "drafts": stat["drafts"],
            }
        )
    edges = [{"from": p, "to": r["key"]} for r in rows for p in r["prereqs"] if p in known]
    return {"nodes": nodes, "edges": edges}


def bridges_graph(session: Session, domain: str) -> dict[str, Any]:
    """Мосты `concept_link` с выбранной областью: её понятия и понятия других областей по ту сторону."""
    mine = {c.id for c in session.query(Concept.id).filter(Concept.domain == domain)}
    links = (
        session.query(ConceptLink)
        .filter((ConceptLink.from_id.in_(mine)) | (ConceptLink.to_id.in_(mine)))
        .all()
        if mine
        else []
    )
    ids = {x for link in links for x in (link.from_id, link.to_id)}
    concepts = session.query(Concept).filter(Concept.id.in_(ids)).all() if ids else []
    counts = _source_counts(session, [c.id for c in concepts])
    return {
        "domain": domain,
        "nodes": [_node(c, counts.get(c.id, 0)) for c in concepts],
        "edges": [
            {
                "id": str(link.id),
                "from": str(link.from_id),
                "to": str(link.to_id),
                "type": "bridge",
                "bloom": link.bloom,
            }
            for link in links
        ],
    }


def node_card(session: Session, node_id: str) -> dict[str, Any]:
    """Карточка понятия: поля, источники и соседи «нужно раньше / открывает»."""
    concept = session.get(Concept, _uid(node_id))
    if concept is None:
        raise GraphEditError("not_found", "Понятие не найдено")
    data = _node(concept, _source_counts(session, [concept.id]).get(concept.id, 0))
    edges = (
        session.query(ConceptEdge)
        .filter((ConceptEdge.from_id == concept.id) | (ConceptEdge.to_id == concept.id))
        .all()
    )
    other_ids = {e.to_id if e.from_id == concept.id else e.from_id for e in edges}
    titles = {c.id: c.title for c in session.query(Concept).filter(Concept.id.in_(other_ids)).all()}
    data["neighbors"] = [
        {
            "edgeId": str(e.id),
            "direction": "out" if e.from_id == concept.id else "in",
            "id": str(e.to_id if e.from_id == concept.id else e.from_id),
            "title": titles.get(e.to_id if e.from_id == concept.id else e.from_id, "—"),
            "type": e.type,
            "status": e.status,
        }
        for e in edges
    ]
    data["sourceList"] = provenance.concept_sources(session, concept.id)
    return data


# ---- правка ----


def update_node(
    session: Session, node_id: str, fields: dict[str, Any], reviewer_id: uuid.UUID | None
) -> dict[str, Any]:
    """Поправить понятие; версия растёт, если поменялись название или пояснение."""
    concept = session.get(Concept, _uid(node_id))
    if concept is None:
        raise GraphEditError("not_found", "Понятие не найдено")
    changed_content = False
    if "title" in fields:
        title = str(fields["title"]).strip()
        if not title:
            raise GraphEditError("empty_title", "Название не может быть пустым")
        changed_content |= title != concept.title
        concept.title = title
    if "summary" in fields:
        content = coerce_content(concept.content)
        summary = str(fields["summary"]).strip()
        changed_content |= summary != content.get("summary", "")
        concept.content = NodeContent.model_validate({**content, "summary": summary}).model_dump()
    if "tier" in fields:
        if fields["tier"] not in TIERS:
            raise GraphEditError("bad_tier", "Тир: core или derived")
        concept.tier = fields["tier"]
    if "stage" in fields:
        concept.stage = (str(fields["stage"]).strip() or None) if fields["stage"] else None
    if "stageOrder" in fields:
        concept.stage_order = (
            int(fields["stageOrder"]) if fields["stageOrder"] not in (None, "") else None
        )
    if "level" in fields:
        concept.level = stages.clean_level(fields["level"])
    if "optional" in fields:
        concept.optional = bool(fields["optional"])
    if changed_content:
        concept.version += 1  # версия растёт: кэш заданий по старой теории обесценивается
    if "status" in fields:
        _set_status(session, concept, fields["status"], fields.get("note"), reviewer_id)
    session.flush()
    return node_card(session, str(concept.id))


def _set_status(
    session: Session, concept: Concept, status: str, note: Any, reviewer_id: uuid.UUID | None
) -> None:
    if status not in STATUSES:
        raise GraphEditError("bad_status", "Статус: draft, approved или rejected")
    if status == concept.status:
        return
    try:
        if status == "approved":
            provenance.review_concept(session, concept, reviewer_id, "approve", note)
        elif status == "rejected":
            provenance.review_concept(session, concept, reviewer_id, "reject", note)
        else:
            concept.status = "draft"  # вернуть на проверку; журнал не пишем
    except provenance.ProvenanceError as e:
        raise GraphEditError(e.code, str(e)) from e


def _reaches(session: Session, start: uuid.UUID, target: uuid.UUID) -> bool:
    """Достижим ли `target` из `start` по предпосылкам. Новое ребро A→B замкнёт цикл, если A достижимо из B."""
    seen: set[uuid.UUID] = set()
    stack = [start]
    while stack:
        node = stack.pop()
        if node == target:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(
            to
            for (to,) in session.query(ConceptEdge.to_id).filter(
                ConceptEdge.from_id == node, ConceptEdge.type == "prereq"
            )
        )
    return False


def add_edge(session: Session, from_id: str, to_id: str, type_: str) -> dict[str, Any]:
    if type_ not in EDGE_TYPES:
        raise GraphEditError("bad_type", "Неизвестный тип связи")
    a, b = _uid(from_id), _uid(to_id)
    if a == b:
        raise GraphEditError("self_loop", "Понятие нельзя связать с самим собой")
    if session.get(Concept, a) is None or session.get(Concept, b) is None:
        raise GraphEditError("not_found", "Понятие не найдено")
    if session.query(ConceptEdge).filter_by(from_id=a, to_id=b, type=type_).first():
        raise GraphEditError("duplicate", "Такая связь уже есть")
    if type_ == "prereq" and _reaches(session, b, a):
        raise GraphEditError("cycle", "Связь замкнула бы цикл предпосылок")
    edge = ConceptEdge(from_id=a, to_id=b, type=type_, status="approved")
    session.add(edge)
    session.flush()
    return _edge(edge)


def change_edge(
    session: Session,
    edge_id: str,
    fields: dict[str, Any],
    reviewer_id: uuid.UUID | None,
) -> dict[str, Any]:
    edge = session.get(ConceptEdge, _uid(edge_id))
    if edge is None:
        raise GraphEditError("not_found", "Связь не найдена")
    if "type" in fields and fields["type"] != edge.type:
        if fields["type"] not in EDGE_TYPES:
            raise GraphEditError("bad_type", "Неизвестный тип связи")
        if (
            session.query(ConceptEdge)
            .filter_by(from_id=edge.from_id, to_id=edge.to_id, type=fields["type"])
            .first()
        ):
            raise GraphEditError("duplicate", "Такая связь уже есть")
        if fields["type"] == "prereq" and _reaches(session, edge.to_id, edge.from_id):
            raise GraphEditError("cycle", "Связь замкнула бы цикл предпосылок")
        edge.type = fields["type"]
    if "status" in fields and fields["status"] != edge.status:
        action = {"approved": "approve", "rejected": "reject"}.get(fields["status"])
        try:
            if action:
                provenance.review_edge(session, edge, reviewer_id, action, fields.get("note"))
            elif fields["status"] == "draft":
                edge.status = "draft"
            else:
                raise GraphEditError("bad_status", "Статус: draft, approved или rejected")
        except provenance.ProvenanceError as e:
            raise GraphEditError(e.code, str(e)) from e
    session.flush()
    return _edge(edge)


def delete_edge(session: Session, edge_id: str) -> None:
    edge = session.get(ConceptEdge, _uid(edge_id))
    if edge is None:
        raise GraphEditError("not_found", "Связь не найдена")
    session.delete(edge)
    session.flush()
