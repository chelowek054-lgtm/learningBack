"""Слияние понятий из разных источников: близость по векторам и решение модели (T-0078, R-0043).

Разбор нескольких книг (и нескольких окон одной) даёт одно и то же понятие под разными именами.
Слияние идёт в два шага. Дёшево: эмбеддинги отбирают кандидатов — ближайших соседей понятия в
той же области. Точно: модель по паре решает «то же / уточнение / другое / противоречит».
Совпадения сливаются в одно понятие с несколькими источниками, уточнения становятся связью
«специализирует», противоречия ложатся в очередь специалиста, а не решаются молча. После слияния
граф предпосылок проверяется на циклы: цикл разрывается по самой слабой черновой связи.

Решение по паре запоминается, чтобы не платить за один и тот же вопрос дважды.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from core.config import settings
from core.embeddings import Embedder, get_embedder
from core.models import Job
from modules.knowledge import provenance
from modules.knowledge.models import (
    Assessment,
    Concept,
    ConceptConflict,
    ConceptEdge,
    ConceptEmbedding,
    ConceptLink,
    ConceptSource,
    EdgeSource,
    MergeDecision,
    ReviewLog,
    UserConcept,
)

log = logging.getLogger("praxis.merge")

JOB_TYPE = "merge_concepts"
VERDICTS = ("same", "refines", "different", "contradicts")

JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": list(VERDICTS)},
        "general": {
            "type": "string",
            "enum": ["a", "b", ""],
            "description": "при refines: какое из понятий общее (a или b)",
        },
        "reason": {"type": "string", "description": "одно предложение"},
    },
    "required": ["verdict", "reason"],
}


# ---- эмбеддинги ----


def concept_text(c: Concept) -> str:
    summary = (c.content or {}).get("summary") or ""
    return f"{c.title}. {summary}".strip()


def _hash(text_: str, model: str) -> str:
    return hashlib.sha256(f"{model}\n{text_}".encode()).hexdigest()


def embed_concepts(
    session: Session, concepts: list[Concept], embedder: Embedder | None = None
) -> int:
    """Посчитать и сохранить векторы; у кого текст не менялся, пропускается. Вернуть число пересчитанных."""
    embedder = embedder or get_embedder()
    todo: list[tuple[Concept, str, str]] = []
    for c in concepts:
        body = concept_text(c)
        digest = _hash(body, embedder.model)
        row = session.get(ConceptEmbedding, c.id)
        if row is None or row.text_hash != digest or row.model != embedder.model:
            todo.append((c, body, digest))
    if not todo:
        return 0
    vectors = embedder.embed([body for _, body, _ in todo])
    for (c, _, digest), vec in zip(todo, vectors, strict=True):
        row = session.get(ConceptEmbedding, c.id)
        if row is None:
            session.add(
                ConceptEmbedding(
                    concept_id=c.id, model=embedder.model, text_hash=digest, embedding=vec
                )
            )
        else:
            row.model, row.text_hash, row.embedding = embedder.model, digest, vec
    session.flush()
    return len(todo)


def candidates(
    session: Session, concept: Concept, k: int | None = None, min_similarity: float | None = None
) -> list[tuple[Concept, float]]:
    """Ближайшие по смыслу понятия той же области: (понятие, косинусная близость), от ближайшего."""
    k = k or settings.merge_candidates
    floor = settings.merge_min_similarity if min_similarity is None else min_similarity
    rows = session.execute(
        text(
            """
            SELECT o.concept_id, 1 - (o.embedding <=> m.embedding) AS sim
            FROM concept_embedding m
            JOIN concept_embedding o ON o.concept_id <> m.concept_id
            JOIN concept oc ON oc.id = o.concept_id
            WHERE m.concept_id = :cid AND oc.domain = :domain AND oc.status <> 'rejected'
            ORDER BY o.embedding <=> m.embedding
            LIMIT :k
            """
        ),
        {"cid": concept.id, "domain": concept.domain, "k": k},
    ).all()
    out = []
    for cid, sim in rows:
        if sim is not None and float(sim) >= floor:
            other = session.get(Concept, cid)
            if other is not None:
                out.append((other, float(sim)))
    return out


# ---- решение по паре ----


def pair_key(a: uuid.UUID, b: uuid.UUID) -> str:
    x, y = sorted((str(a), str(b)))
    return f"{x}:{y}"


def judge(gateway, a: Concept, b: Concept) -> dict[str, str]:
    """Спросить модель про пару. Ответ не по схеме считается «другое»: слияние по догадке хуже пропуска."""
    prompt = (
        "Два понятия из графа знаний одной области. Реши, как они соотносятся.\n"
        f"[a] {a.title}\n{(a.content or {}).get('summary', '')}\n\n"
        f"[b] {b.title}\n{(b.content or {}).get('summary', '')}\n\n"
        "Варианты: same — это одно и то же понятие (разные названия или формулировки); "
        "refines — одно является частным случаем или уточнением другого (укажи, какое общее); "
        "different — разные понятия, даже если связаны; contradicts — утверждения противоречат "
        "друг другу. Сомневаешься — выбирай different."
    )
    raw = gateway.structured(
        "judge_pair", "Решить, как соотносятся два понятия.", JUDGE_SCHEMA, prompt
    )
    verdict = (raw or {}).get("verdict")
    if verdict not in VERDICTS:
        return {"verdict": "different", "general": "", "reason": "модель не дала внятного ответа"}
    general = raw.get("general") if raw.get("general") in ("a", "b") else ""
    return {"verdict": verdict, "general": general, "reason": str(raw.get("reason") or "")[:300]}


# ---- действия ----


def _source_count(session: Session, concept: Concept) -> int:
    return session.query(ConceptSource).filter_by(concept_id=concept.id).count()


def pick_keeper(session: Session, a: Concept, b: Concept) -> tuple[Concept, Concept]:
    """Кто остаётся: подтверждённое человеком, иначе с большим числом источников, иначе старшее."""

    def rank(c: Concept) -> tuple[bool, int, float]:
        age = c.created_at.timestamp() if c.created_at else 0.0
        return (c.status == provenance.APPROVED, _source_count(session, c), -age)

    keeper, loser = (a, b) if rank(a) >= rank(b) else (b, a)
    return keeper, loser


def merge_into(session: Session, keeper: Concept, loser: Concept, reason: str = "") -> None:
    """Влить `loser` в `keeper`: источники, связи, ссылки людей; запись в журнал."""
    kid, lid = keeper.id, loser.id
    # Этап и уровень: у понятия без меток берутся метки вливаемого; обязательное остаётся обязательным.
    if keeper.stage is None and loser.stage is not None:
        keeper.stage, keeper.stage_order = loser.stage, loser.stage_order
    if keeper.level is None:
        keeper.level = loser.level
    keeper.optional = keeper.optional and loser.optional
    # источники понятия
    for src in session.query(ConceptSource).filter_by(concept_id=lid).all():
        has = (
            session.query(ConceptSource)
            .filter_by(concept_id=kid, fragment_id=src.fragment_id, role=src.role)
            .first()
        )
        if has is None:
            session.add(ConceptSource(concept_id=kid, fragment_id=src.fragment_id, role=src.role))
    session.flush()
    # связи: перенаправить, не плодя дублей и петель; источники связей складываются
    for edge in (
        session.query(ConceptEdge)
        .filter((ConceptEdge.from_id == lid) | (ConceptEdge.to_id == lid))
        .all()
    ):
        new_from = kid if edge.from_id == lid else edge.from_id
        new_to = kid if edge.to_id == lid else edge.to_id
        if new_from == new_to:
            session.delete(edge)
            continue
        twin = (
            session.query(ConceptEdge)
            .filter(
                ConceptEdge.from_id == new_from,
                ConceptEdge.to_id == new_to,
                ConceptEdge.type == edge.type,
                ConceptEdge.id != edge.id,
            )
            .first()
        )
        if twin is None:
            edge.from_id, edge.to_id = new_from, new_to
        else:
            for es in session.query(EdgeSource).filter_by(edge_id=edge.id).all():
                if (
                    session.query(EdgeSource)
                    .filter_by(edge_id=twin.id, fragment_id=es.fragment_id)
                    .first()
                    is None
                ):
                    session.add(EdgeSource(edge_id=twin.id, fragment_id=es.fragment_id))
            if edge.status == provenance.APPROVED and twin.status != provenance.APPROVED:
                twin.status = provenance.APPROVED
            session.delete(edge)
    session.flush()
    # то, что ссылалось на вливаемое понятие
    session.query(UserConcept).filter_by(base_concept_id=lid).update(
        {"base_concept_id": kid}, synchronize_session=False
    )
    for link in (
        session.query(ConceptLink)
        .filter((ConceptLink.from_id == lid) | (ConceptLink.to_id == lid))
        .all()
    ):
        f = kid if link.from_id == lid else link.from_id
        t = kid if link.to_id == lid else link.to_id
        if f == t or session.query(ConceptLink).filter_by(from_id=f, to_id=t).first():
            session.delete(link)
        else:
            link.from_id, link.to_id = f, t
    session.query(Assessment).filter_by(concept_id=lid).delete(synchronize_session=False)
    session.query(ConceptConflict).filter(
        (ConceptConflict.a_id == lid) | (ConceptConflict.b_id == lid)
    ).update({"status": "resolved"}, synchronize_session=False)
    keeper.confidence = max(keeper.confidence or 0.0, loser.confidence or 0.0)
    session.add(
        ReviewLog(
            target_type="concept",
            target_id=kid,
            reviewer_id=None,
            action="edit",
            note=f"объединено с «{loser.title}»" + (f": {reason}" if reason else ""),
        )
    )
    session.query(ReviewLog).filter_by(target_type="concept", target_id=lid).delete(
        synchronize_session=False
    )
    session.query(ConceptEmbedding).filter_by(concept_id=lid).delete(synchronize_session=False)
    session.flush()
    session.delete(loser)
    session.flush()


def add_specialization(session: Session, general: Concept, special: Concept) -> bool:
    """Связь «частное специализирует общее» (черновик, источники — оба понятия). True, если создана."""
    edge = (
        session.query(ConceptEdge)
        .filter_by(from_id=special.id, to_id=general.id, type="specializes")
        .first()
    )
    created = edge is None
    if edge is None:
        edge = ConceptEdge(
            from_id=special.id, to_id=general.id, type="specializes", status=provenance.DRAFT
        )
        session.add(edge)
        session.flush()
    frag_ids = {
        row[0]
        for row in session.query(ConceptSource.fragment_id).filter(
            ConceptSource.concept_id.in_([general.id, special.id])
        )
    }
    for fid in frag_ids:
        if session.query(EdgeSource).filter_by(edge_id=edge.id, fragment_id=fid).first() is None:
            session.add(EdgeSource(edge_id=edge.id, fragment_id=fid))
    session.flush()
    return created


def record_conflict(session: Session, a: Concept, b: Concept, reason: str) -> ConceptConflict:
    x, y = sorted((a.id, b.id), key=str)
    found = session.query(ConceptConflict).filter_by(a_id=x, b_id=y).first()
    if found is not None:
        return found
    conflict = ConceptConflict(a_id=x, b_id=y, reason=reason, status="open")
    session.add(conflict)
    session.flush()
    return conflict


# ---- циклы предпосылок ----


def find_prereq_cycle(session: Session, domain: str) -> list[uuid.UUID] | None:
    """Любой цикл среди связей prereq области (список понятий по кругу) или None."""
    edges = (
        session.query(ConceptEdge)
        .join(Concept, Concept.id == ConceptEdge.from_id)
        .filter(Concept.domain == domain, ConceptEdge.type == "prereq")
        .all()
    )
    graph: dict[uuid.UUID, list[uuid.UUID]] = {}
    for e in edges:
        graph.setdefault(e.from_id, []).append(e.to_id)
    state: dict[uuid.UUID, int] = {}
    stack: list[uuid.UUID] = []

    def dfs(node: uuid.UUID) -> list[uuid.UUID] | None:
        state[node] = 1
        stack.append(node)
        for nxt in graph.get(node, []):
            if state.get(nxt) == 1:
                return stack[stack.index(nxt) :]
            if state.get(nxt) is None and (found := dfs(nxt)):
                return found
        stack.pop()
        state[node] = 2
        return None

    for start in list(graph):
        if state.get(start) is None and (cycle := dfs(start)):
            return cycle
    return None


def break_cycles(session: Session, domain: str, limit: int = 20) -> int:
    """Разорвать циклы предпосылок: удалить самую слабую черновую связь цикла.

    Слабая — с наименьшим числом источников. Подтверждённые человеком связи не трогаются: если
    весь цикл подтверждён, это решение человека, и оно уходит в очередь конфликтов.
    """
    removed = 0
    for _ in range(limit):
        cycle = find_prereq_cycle(session, domain)
        if not cycle:
            break
        pairs = list(zip(cycle, cycle[1:] + cycle[:1], strict=True))
        drafts = []
        for f, t in pairs:
            edge = session.query(ConceptEdge).filter_by(from_id=f, to_id=t, type="prereq").first()
            if edge is not None and edge.status != provenance.APPROVED:
                drafts.append(
                    (
                        session.query(EdgeSource).filter_by(edge_id=edge.id).count(),
                        str(edge.id),
                        edge,
                    )
                )
        if not drafts:
            a, b = session.get(Concept, pairs[0][0]), session.get(Concept, pairs[0][1])
            record_conflict(session, a, b, "цикл предпосылок из подтверждённых связей")
            break
        drafts.sort(key=lambda d: (d[0], d[1]))
        session.delete(drafts[0][2])
        session.flush()
        removed += 1
    return removed


# ---- слияние области ----


@dataclass
class MergeReport:
    embedded: int = 0
    judged: int = 0
    reused: int = 0
    merged: int = 0
    refined: int = 0
    conflicts: int = 0
    cycles_broken: int = 0
    skipped_budget: int = 0
    notes: list[str] = field(default_factory=list)


def merge_domain(
    session: Session,
    domain: str,
    gateway,
    embedder: Embedder | None = None,
    max_judgements: int | None = None,
) -> MergeReport:
    """Найти и слить дубли в области. Решения запоминаются; бюджет вопросов к модели ограничен."""
    report = MergeReport()
    budget = max_judgements if max_judgements is not None else settings.merge_max_judgements
    concepts = (
        session.query(Concept)
        .filter(Concept.domain == domain, Concept.status != provenance.REJECTED)
        .all()
    )
    report.embedded = embed_concepts(session, concepts, embedder)
    seen_pairs: set[str] = set()
    for concept in sorted(concepts, key=lambda c: c.title):
        if session.get(Concept, concept.id) is None:  # уже влит в другое
            continue
        for other, _sim in candidates(session, concept):
            if session.get(Concept, concept.id) is None:
                break
            if session.get(Concept, other.id) is None:
                continue
            key = pair_key(concept.id, other.id)
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            decision = session.query(MergeDecision).filter_by(pair_key=key).one_or_none()
            if decision is not None:
                report.reused += 1
                verdict = {
                    "verdict": decision.verdict,
                    "general": decision.general,
                    "reason": decision.reason,
                }
                if decision.verdict in ("different",):
                    continue
            else:
                if report.judged >= budget:
                    report.skipped_budget += 1
                    continue
                verdict = judge(gateway, concept, other)
                report.judged += 1
                session.add(
                    MergeDecision(
                        pair_key=key,
                        verdict=verdict["verdict"],
                        general=verdict["general"],
                        reason=verdict["reason"],
                    )
                )
                session.flush()
            _apply(session, concept, other, verdict, report)
    report.cycles_broken = break_cycles(session, domain)
    return report


def _apply(
    session: Session, a: Concept, b: Concept, verdict: dict[str, str], report: MergeReport
) -> None:
    kind = verdict["verdict"]
    if kind == "same":
        keeper, loser = pick_keeper(session, a, b)
        merge_into(session, keeper, loser, verdict["reason"])
        report.merged += 1
    elif kind == "refines" and verdict["general"] in ("a", "b"):
        general, special = (a, b) if verdict["general"] == "a" else (b, a)
        if add_specialization(session, general, special):
            report.refined += 1
    elif kind == "contradicts":
        record_conflict(session, a, b, verdict["reason"])
        report.conflicts += 1


def enqueue_merge(session: Session, domain: str, user_id: uuid.UUID) -> Job:
    job = Job(user_id=user_id, type=JOB_TYPE, status="pending", input_ref={"domain": domain})
    session.add(job)
    session.flush()
    return job


def merge_job(session: Session, job: Job, gateway) -> dict[str, Any]:
    domain = str((job.input_ref or {}).get("domain") or "").strip()
    if not domain:
        raise ValueError("Не указана область")
    r = merge_domain(session, domain, gateway)
    return {
        "domain": domain,
        "embedded": r.embedded,
        "judged": r.judged,
        "reused": r.reused,
        "merged": r.merged,
        "refined": r.refined,
        "conflicts": r.conflicts,
        "cyclesBroken": r.cycles_broken,
        "skippedBudget": r.skipped_budget,
    }
