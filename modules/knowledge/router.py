"""CRUD API графа знаний (KG1-03). Эффективный граф (COW), персональный слой,
канон-курирование, build-draft через AI-gateway. См. 05-knowledge-model."""

from fastapi import APIRouter, HTTPException, status

from core.deps import CurrentUser, SessionDep
from modules.knowledge.ai import build_graph, expand_node
from modules.knowledge.centrality import recompute_centrality
from modules.knowledge.cow import effective_graph
from modules.knowledge.models import Concept, ConceptEdge, UserConcept, UserEdge
from modules.knowledge.schemas import (
    ApproveNodeIn,
    BuildGraphIn,
    CanonEdgeIn,
    CanonNodeIn,
    CanonNodePatch,
    ExpandIn,
    OverrideIn,
    OwnNodeIn,
    RecomputeIn,
    UserEdgeIn,
    UserNodePatch,
)

router = APIRouter(prefix="/graph", tags=["graph"])


# ---- чтение эффективного графа (COW) ----
@router.get("/{domain}")
def get_graph(domain: str, user: CurrentUser, session: SessionDep) -> dict:
    return effective_graph(session, user.id, domain)


# ---- канон-курирование (в KG2 — через редактор/импорт) ----
@router.post("/canon/build")
def build_canon(body: BuildGraphIn, user: CurrentUser, session: SessionDep) -> dict:
    """LLM-черновик графа темы → персист как canonical draft (идемпотентно по domain+title)."""
    draft = build_graph(body.domain, body.topic)
    key_to_id: dict[str, object] = {}
    for n in draft.get("nodes", []):
        existing = (
            session.query(Concept).filter_by(domain=body.domain, title=n["title"]).first()
        )
        if existing:
            key_to_id[n["key"]] = existing.id
            continue
        c = Concept(
            domain=body.domain,
            title=n["title"],
            tier=n.get("tier", "derived"),
            content=n.get("content", {}),
            bloom_levels=n.get("bloomLevels", []),
            difficulty=n.get("difficulty", 1),
            source="llm",
            confidence=n.get("confidence", 0.0),
            status="draft",
        )
        session.add(c)
        session.flush()
        key_to_id[n["key"]] = c.id
    for e in draft.get("edges", []):
        f, t = key_to_id.get(e["from"]), key_to_id.get(e["to"])
        if f and t and not session.query(ConceptEdge).filter_by(
            from_id=f, to_id=t, type=e["type"]
        ).first():
            session.add(ConceptEdge(from_id=f, to_id=t, type=e["type"]))
    session.commit()
    return effective_graph(session, user.id, body.domain)


@router.post("/canon/nodes", status_code=status.HTTP_201_CREATED)
def create_canon_node(body: CanonNodeIn, _: CurrentUser, session: SessionDep) -> dict:
    c = Concept(
        domain=body.domain,
        title=body.title,
        tier=body.tier,
        content=body.content,
        bloom_levels=body.bloom_levels,
        difficulty=body.difficulty,
        source=body.source,
        centrality=body.centrality,
        status="approved",
    )
    session.add(c)
    session.commit()
    session.refresh(c)
    return {"id": str(c.id), "version": c.version}


@router.put("/canon/nodes/{concept_id}")
def update_canon_node(
    concept_id: str, body: CanonNodePatch, _: CurrentUser, session: SessionDep
) -> dict:
    c = session.get(Concept, concept_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "concept не найден")
    if body.title is not None:
        c.title = body.title
    if body.tier is not None:
        c.tier = body.tier
    if body.centrality is not None:
        c.centrality = body.centrality
    if body.status is not None:
        c.status = body.status
    if body.content is not None:
        c.content = body.content
        c.version += 1  # версионирование при смене контента (инвариант №5)
    session.commit()
    return {"id": str(c.id), "version": c.version}


@router.post("/canon/edges", status_code=status.HTTP_201_CREATED)
def create_canon_edge(body: CanonEdgeIn, _: CurrentUser, session: SessionDep) -> dict:
    e = ConceptEdge(from_id=body.from_id, to_id=body.to_id, type=body.type)
    session.add(e)
    session.commit()
    return {"id": str(e.id)}


@router.post("/canon/recompute-centrality")
def recompute(body: RecomputeIn, _: CurrentUser, session: SessionDep) -> list[dict]:
    """Пересчёт centrality + предложение узлов в ядро (гибрид метрика/курирование)."""
    return recompute_centrality(session, body.domain)


@router.post("/canon/nodes/{concept_id}/approve")
def approve_node(
    concept_id: str, body: ApproveNodeIn, _: CurrentUser, session: SessionDep
) -> dict:
    """Governance: подтвердить draft-узел (status='approved'), опц. закрепить tier."""
    c = session.get(Concept, concept_id)
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "concept не найден")
    c.status = "approved"
    if body.tier is not None:
        c.tier = body.tier
    session.commit()
    return {"id": str(c.id), "status": c.status, "tier": c.tier}


# ---- рост ветки под интересы (COW) ----
@router.post("/expand")
def expand(body: ExpandIn, user: CurrentUser, session: SessionDep) -> dict:
    """«Углубиться»: LLM дорастает ветку от узла → персональные own-узлы + рёбра (origin=grown_llm)."""
    c = session.get(Concept, str(body.concept_id))
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "concept не найден")
    result = expand_node(c.title, body.direction)
    for n in result.get("nodes", []):
        uc = UserConcept(
            user_id=user.id,
            base_concept_id=None,
            title=n["title"],
            content_override=n.get("content", {}),
            origin="grown_llm",
            status="learning",
        )
        session.add(uc)
        session.flush()
        session.add(UserEdge(user_id=user.id, from_id=body.concept_id, to_id=uc.id, type="related"))
    session.commit()
    return effective_graph(session, user.id, c.domain)


# ---- персональный слой (COW) ----
@router.post("/nodes", status_code=status.HTTP_201_CREATED)
def create_own_node(body: OwnNodeIn, user: CurrentUser, session: SessionDep) -> dict:
    uc = UserConcept(
        user_id=user.id,
        base_concept_id=None,
        title=body.title,
        content_override=body.content,
        origin="grown_llm",
        status="learning",
    )
    session.add(uc)
    session.commit()
    return {"userConceptId": str(uc.id)}


@router.post("/nodes/{base_concept_id}/override")
def override_canon_node(
    base_concept_id: str, body: OverrideIn, user: CurrentUser, session: SessionDep
) -> dict:
    """Правка унаследованного канон-узла → персональный оверрайд (COW)."""
    if session.get(Concept, base_concept_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "canonical concept не найден")
    uc = (
        session.query(UserConcept)
        .filter_by(user_id=user.id, base_concept_id=base_concept_id)
        .first()
    )
    if uc is None:
        uc = UserConcept(user_id=user.id, base_concept_id=base_concept_id, origin="edited")
        session.add(uc)
    uc.content_override = body.content
    uc.origin = "edited"
    session.commit()
    return {"userConceptId": str(uc.id)}


@router.put("/user-nodes/{user_concept_id}")
def patch_user_node(
    user_concept_id: str, body: UserNodePatch, user: CurrentUser, session: SessionDep
) -> dict:
    uc = session.get(UserConcept, user_concept_id)
    if uc is None or uc.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "user_concept не найден")
    if body.title is not None:
        uc.title = body.title
    if body.content is not None:
        uc.content_override = body.content
    if body.mastery is not None:
        uc.mastery = body.mastery
    if body.status is not None:
        uc.status = body.status
    session.commit()
    return {"userConceptId": str(uc.id)}


@router.delete("/user-nodes/{user_concept_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user_node(user_concept_id: str, user: CurrentUser, session: SessionDep) -> None:
    uc = session.get(UserConcept, user_concept_id)
    if uc is not None and uc.user_id == user.id:
        session.delete(uc)
        session.commit()


@router.post("/user-edges", status_code=status.HTTP_201_CREATED)
def create_user_edge(body: UserEdgeIn, user: CurrentUser, session: SessionDep) -> dict:
    e = UserEdge(user_id=user.id, from_id=body.from_id, to_id=body.to_id, type=body.type)
    session.add(e)
    session.commit()
    return {"id": str(e.id)}


@router.delete("/user-edges/{edge_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user_edge(edge_id: str, user: CurrentUser, session: SessionDep) -> None:
    e = session.get(UserEdge, edge_id)
    if e is not None and e.user_id == user.id:
        session.delete(e)
        session.commit()
