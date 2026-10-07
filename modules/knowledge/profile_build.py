"""Скелет графа из профиля навыка (T-0090, R-0048, A-0032).

Понятия профиля заводятся черновиками с этапами, уровнями и связями; области получают места в реестре
областей и связи между собой, а понятия последнего этапа области-предпосылки привязываются к первым понятиям
следующей. Что уже есть в графе (решения сопоставления, T-0096), не дублируется. Источники потом
подтверждают и наполняют скелет (T-0097); без них понятия остаются черновиками «со слов модели».
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from modules.knowledge import cross_links, domains, profile_match, stages
from modules.knowledge.centrality import recompute_centrality
from modules.knowledge.models import Concept, ConceptEdge, Domain

BRIDGE_FANOUT = 2  # сколько корней следующей области цепляем к одному «хвосту» предыдущей
LEVEL_BLOOMS = {
    "basic": ["remember", "understand"],
    "middle": ["remember", "understand", "apply"],
    "advanced": ["remember", "understand", "apply"],
}
LEVEL_DIFFICULTY = {"basic": 1, "middle": 3, "advanced": 5}


def _bloom(level: str | None) -> list[str]:
    return list(LEVEL_BLOOMS.get(level or "", LEVEL_BLOOMS["middle"]))


def _target_domain(
    session: Session, area: dict[str, Any], decision: dict[str, Any], goal_domain: str
) -> Domain:
    """Область графа для области профиля: найденная по сопоставлению либо заведённая; цель — под именем человека."""
    if decision.get("decision") == profile_match.EXISTING and decision.get("domain"):
        found = session.get(Domain, decision["domain"])
        if found is not None:
            return found
    title = goal_domain if area["role"] == "goal" else area["title"]
    foundation = area["role"] == "foundation" and not area["prereqs"]
    domain, _ = domains.register(session, title, foundation=foundation)
    return domain


def _concept_rows(area: dict[str, Any]) -> list[dict[str, Any]]:
    """Понятия области с этапом и порядком этапа: метки из профиля, как есть."""
    by_stage = {s["key"]: s for s in area["stages"]}
    rows = []
    for c in area["concepts"]:
        stage = by_stage.get(c.get("stage"))
        rows.append(
            {
                **c,
                "stageTitle": stage["title"] if stage else None,
                "stageOrder": stage["order"] if stage else None,
            }
        )
    return rows


def _create_concepts(
    session: Session, domain: Domain, rows: list[dict[str, Any]], reuse: dict[str, uuid.UUID]
) -> tuple[dict[str, uuid.UUID], int]:
    """Создать недостающие понятия; повторный вызов находит уже созданные по ключу."""
    ids = dict(reuse)
    created = 0
    for row in rows:
        if row["key"] in ids:
            continue
        found = session.query(Concept).filter_by(domain=domain.title, key=row["key"]).first()
        if found is not None:
            ids[row["key"]] = found.id
            continue
        fields = stages.stage_fields(
            {
                "stage": row["stageTitle"],
                "stageOrder": row["stageOrder"],
                "level": row.get("level"),
                "optional": row.get("optional"),
            }
        )
        concept = Concept(
            domain=domain.title,
            key=row["key"],
            title=row["title"],
            tier="core" if row.get("level") == "basic" and not row.get("optional") else "derived",
            content={"summary": row.get("summary") or row["title"], "sections": []},
            bloom_levels=_bloom(row.get("level")),
            difficulty=LEVEL_DIFFICULTY.get(row.get("level") or "", 2),
            source="llm",
            confidence=0.0,
            status="draft",
            **fields,
        )
        session.add(concept)
        session.flush()
        ids[row["key"]] = concept.id
        created += 1
    return ids, created


def _edge(session: Session, from_id: uuid.UUID, to_id: uuid.UUID) -> bool:
    if from_id == to_id:
        return False
    exists = (
        session.query(ConceptEdge).filter_by(from_id=from_id, to_id=to_id, type="prereq").first()
    )
    if exists is not None:
        return False
    session.add(ConceptEdge(from_id=from_id, to_id=to_id, type="prereq", status="draft"))
    return True


def _link_inside(
    session: Session, rows: list[dict[str, Any]], ids: dict[str, uuid.UUID], only_new: set[str]
) -> int:
    """Предпосылки модели; понятие без предпосылок на втором и дальнейших этапах получает последнее понятие
    предыдущего этапа, чтобы этапы шли цепочкой, а не россыпью."""
    made = 0
    stage_last: dict[int, str] = {}
    for row in rows:
        if row["stageOrder"] is not None:
            stage_last[row["stageOrder"]] = row["key"]
    for row in rows:
        if row["key"] not in only_new:
            continue
        for pre in row["prereqs"]:
            if pre in ids and _edge(session, ids[pre], ids[row["key"]]):
                made += 1
        order = row["stageOrder"]
        if not row["prereqs"] and order and order > 1:
            earlier = [o for o in stage_last if o < order]
            if earlier:
                tail = stage_last[max(earlier)]
                if tail != row["key"] and _edge(session, ids[tail], ids[row["key"]]):
                    made += 1
    session.flush()
    return made


def _tails_and_roots(rows: list[dict[str, Any]], ids: dict[str, uuid.UUID]):
    has_out = {p for r in rows for p in r["prereqs"]}
    has_in = {r["key"] for r in rows if r["prereqs"]}
    tails = [ids[r["key"]] for r in rows if r["key"] not in has_out and r["key"] in ids]
    roots = [ids[r["key"]] for r in rows if r["key"] not in has_in and r["key"] in ids]
    return tails, roots


def build_skeleton(
    session: Session, goal_domain: str, profile: dict[str, Any], match: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Завести области, понятия и связи профиля. `match` — решения T-0096; без них всё считается новым."""
    decisions = {a["key"]: a for a in (match or {}).get("areas", [])}
    areas = profile.get("areas", [])
    mapped: dict[str, dict[str, Any]] = {}
    report: dict[str, Any] = {
        "domains": [],
        "areas": [],
        "created": 0,
        "reused": 0,
        "edges": 0,
        "links": 0,
    }

    for area in areas:
        decision = decisions.get(area["key"], {})
        domain = _target_domain(session, area, decision, goal_domain)
        reuse = {
            c["key"]: uuid.UUID(c["conceptId"])
            for c in decision.get("concepts", [])
            if c.get("decision") == profile_match.EXISTING and c.get("conceptId")
        }
        rows = _concept_rows(area)
        ids, created = _create_concepts(session, domain, rows, reuse)
        new_keys = {r["key"] for r in rows if r["key"] not in reuse}
        report["created"] += created
        report["reused"] += len(reuse)
        report["edges"] += _link_inside(session, rows, ids, new_keys)
        mapped[area["key"]] = {"domain": domain, "ids": ids, "rows": rows}
        report["domains"].append(domain.title)
        report["areas"].append(
            {
                "key": area["key"],
                "domain": domain.title,
                "created": created,
                "new": decision.get("decision") != profile_match.EXISTING,
            }
        )

    # области: предпосылки между ними и привязка понятий через границу
    for area in areas:
        upper = mapped[area["key"]]
        for pre_key in area["prereqs"]:
            lower = mapped.get(pre_key)
            if lower is None or lower["domain"].key == upper["domain"].key:
                continue
            try:
                domains.add_prereq(session, upper["domain"].key, lower["domain"].key)
            except domains.DomainError:
                continue  # у опоры предпосылок быть не может, а повтор уже учтён
            tails, _ = _tails_and_roots(lower["rows"], lower["ids"])
            _, roots = _tails_and_roots(upper["rows"], upper["ids"])
            for tail in tails:
                for root in roots[:BRIDGE_FANOUT]:
                    try:
                        cross_links.add_link(session, tail, root, "understand")
                        report["links"] += 1
                    except cross_links.LinkError:
                        continue
    for item in mapped.values():
        recompute_centrality(session, item["domain"].title, commit=False)
    session.flush()
    return report
