"""Сопоставление профиля навыка с графом по близости векторов (T-0096, R-0050, A-0031).

Для каждой области профиля решается: такая область в графе уже есть, спорный случай или новая. Сначала
точное совпадение по названию и алиасам (реестр областей), затем косинусная близость вектора области к
векторам существующих областей: от `area_same_similarity` — уже есть, от `area_maybe_similarity` до неё —
спорно, и это решает модель (тем же «одно ли это», что при слиянии понятий); ниже — новая.
Для области, которая уже есть, так же сопоставляются понятия профиля с понятиями области (пороги слияния).

Каждое решение сохраняется в профиле и видно человеку: что и с чем сопоставлено, с какой близостью и кто
решил (название, вектор, модель). Ошибочное «уже есть» ничего не теряет: понятия остаются отдельными
записями, пока их не сольёт слияние.
"""

from __future__ import annotations

import hashlib
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from core.config import settings
from core.embeddings import Embedder, get_embedder
from modules.knowledge import domains, merge, provenance
from modules.knowledge.models import Concept, Domain, DomainAlias, DomainEmbedding

EXISTING, DISPUTED, NEW = "existing", "disputed", "new"
VIA_NAME, VIA_VECTOR, VIA_MODEL, VIA_NONE = "name", "vector", "model", "none"
MAX_DOMAIN_CONCEPTS = 8  # сколько названий понятий добавлять к тексту области

JUDGE_AREA_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["same", "different"]},
        "reason": {"type": "string"},
    },
    "required": ["verdict"],
}


def _hash(body: str, model: str) -> str:
    return hashlib.sha256(f"{model}\n{body}".encode()).hexdigest()


def domain_text(session: Session, domain: Domain) -> str:
    """Название, алиасы и несколько названий понятий: по одному названию синоним не отличить."""
    aliases = [a.alias for a in session.query(DomainAlias).filter_by(domain_key=domain.key)]
    titles = [
        c.title
        for c in session.query(Concept)
        .filter(
            Concept.domain.in_({domain.key, domain.title}), Concept.status != provenance.REJECTED
        )
        .order_by(Concept.centrality.desc())
        .limit(MAX_DOMAIN_CONCEPTS)
    ]
    parts = [domain.title, *aliases]
    return ". ".join(parts) + (f". Включает: {', '.join(titles)}" if titles else "")


def embed_domains(session: Session, embedder: Embedder | None = None) -> int:
    """Посчитать векторы областей; у кого текст не менялся, пропускается. Вернуть число пересчитанных."""
    embedder = embedder or get_embedder()
    todo: list[tuple[Domain, str, str]] = []
    for d in session.query(Domain).all():
        body = domain_text(session, d)
        digest = _hash(body, embedder.model)
        row = session.get(DomainEmbedding, d.key)
        if row is None or row.text_hash != digest or row.model != embedder.model:
            todo.append((d, body, digest))
    if not todo:
        return 0
    vectors = embedder.embed([body for _, body, _ in todo])
    for (d, _, digest), vec in zip(todo, vectors, strict=True):
        row = session.get(DomainEmbedding, d.key)
        if row is None:
            session.add(
                DomainEmbedding(
                    domain_key=d.key, model=embedder.model, text_hash=digest, embedding=vec
                )
            )
        else:
            row.model, row.text_hash, row.embedding = embedder.model, digest, vec
    session.flush()
    return len(todo)


def _nearest_domain(session: Session, vector: list[float]) -> tuple[Domain, float] | None:
    row = session.execute(
        text(
            "SELECT domain_key, 1 - (embedding <=> CAST(:v AS vector)) AS sim "
            "FROM domain_embedding ORDER BY embedding <=> CAST(:v AS vector) LIMIT 1"
        ),
        {"v": "[" + ",".join(f"{x:.8f}" for x in vector) + "]"},
    ).first()
    if row is None or row[1] is None:
        return None
    domain = session.get(Domain, row[0])
    return (domain, float(row[1])) if domain else None


def _judge_area(gateway: Any, area: dict[str, Any], domain: Domain, aliases: str) -> bool:
    """Спросить модель про спорную пару; нет внятного ответа — «разные»: лишняя область дешевле склейки."""
    raw = gateway.structured(
        "judge_area",
        "Решить, одна ли это область знаний.",
        JUDGE_AREA_SCHEMA,
        (
            "Две области знаний. Реши, это одна и та же область (синонимы, разные формулировки) "
            "или разные.\n"
            f"[нужная] {area['title']}. {area.get('summary', '')}\n"
            f"[в графе] {domain.title}. {aliases}\n"
            "Сомневаешься — выбирай different."
        ),
    )
    return (raw or {}).get("verdict") == "same"


def match_area(
    session: Session, area: dict[str, Any], gateway: Any = None, embedder: Embedder | None = None
) -> dict[str, Any]:
    """Решение по одной области профиля: {decision, domain, similarity, via}."""
    exact = domains.resolve(session, area["title"])
    if exact is not None:
        return {"decision": EXISTING, "domain": exact.key, "similarity": 1.0, "via": VIA_NAME}
    embedder = embedder or get_embedder()
    embed_domains(session, embedder)
    vector = embedder.embed([f"{area['title']}. {area.get('summary', '')}".strip()])[0]
    near = _nearest_domain(session, vector)
    if near is None or near[1] < settings.area_maybe_similarity:
        sim = round(near[1], 3) if near else 0.0
        return {"decision": NEW, "domain": None, "similarity": sim, "via": VIA_NONE}
    domain, sim = near
    sim = round(sim, 3)
    if sim >= settings.area_same_similarity:
        return {"decision": EXISTING, "domain": domain.key, "similarity": sim, "via": VIA_VECTOR}
    # Спорная полоса: решает модель; без модели остаётся «новая», но помечена спорной.
    if gateway is None:
        return {
            "decision": NEW,
            "domain": domain.key,
            "similarity": sim,
            "via": VIA_NONE,
            "disputed": True,
        }
    aliases = ", ".join(
        a.alias for a in session.query(DomainAlias).filter_by(domain_key=domain.key)
    )
    same = _judge_area(gateway, area, domain, aliases)
    return {
        "decision": EXISTING if same else NEW,
        "domain": domain.key,
        "similarity": sim,
        "via": VIA_MODEL,
        "disputed": True,
    }


def match_concepts(
    session: Session,
    domain: Domain,
    concepts: list[dict[str, Any]],
    embedder: Embedder | None = None,
) -> list[dict[str, Any]]:
    """Понятия профиля против понятий существующей области: {key, decision, conceptId, similarity}."""
    embedder = embedder or get_embedder()
    existing = (
        session.query(Concept)
        .filter(
            Concept.domain.in_({domain.key, domain.title}), Concept.status != provenance.REJECTED
        )
        .all()
    )
    if not existing or not concepts:
        return [
            {"key": c["key"], "decision": NEW, "conceptId": None, "similarity": 0.0}
            for c in concepts
        ]
    merge.embed_concepts(session, existing, embedder)
    vectors = embedder.embed([f"{c['title']}. {c.get('summary', '')}".strip() for c in concepts])
    out = []
    for c, vec in zip(concepts, vectors, strict=True):
        row = session.execute(
            text(
                "SELECT e.concept_id, 1 - (e.embedding <=> CAST(:v AS vector)) AS sim "
                "FROM concept_embedding e JOIN concept k ON k.id = e.concept_id "
                "WHERE k.domain IN (:d1, :d2) AND k.status <> 'rejected' "
                "ORDER BY e.embedding <=> CAST(:v AS vector) LIMIT 1"
            ),
            {
                "v": "[" + ",".join(f"{x:.8f}" for x in vec) + "]",
                "d1": domain.key,
                "d2": domain.title,
            },
        ).first()
        sim = float(row[1]) if row and row[1] is not None else 0.0
        same = sim >= settings.area_same_similarity
        out.append(
            {
                "key": c["key"],
                "decision": EXISTING if same else NEW,
                "conceptId": str(row[0]) if same and row else None,
                "similarity": round(sim, 3),
            }
        )
    return out


def match_profile(
    session: Session, profile: dict[str, Any], gateway: Any = None, embedder: Embedder | None = None
) -> dict[str, Any]:
    """Решения по всем областям и понятиям профиля; в базу ничего не пишет (кроме кэша векторов)."""
    embedder = embedder or get_embedder()
    areas = []
    for area in profile.get("areas", []):
        decision = match_area(session, area, gateway, embedder)
        concepts: list[dict[str, Any]] = []
        if decision["decision"] == EXISTING and decision["domain"]:
            domain = session.get(Domain, decision["domain"])
            concepts = match_concepts(session, domain, area.get("concepts", []), embedder)
        areas.append({"key": area["key"], **decision, "concepts": concepts})
    return {
        "areas": areas,
        "summary": {
            "existing": sum(1 for a in areas if a["decision"] == EXISTING),
            "new": sum(1 for a in areas if a["decision"] == NEW),
            "disputed": sum(1 for a in areas if a.get("disputed")),
        },
    }
