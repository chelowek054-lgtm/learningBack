"""CRUD API графа знаний (KG1-03). Эффективный граф (COW), персональный слой,
канон-курирование, build-draft через AI-gateway. См. 05-knowledge-model."""

import uuid

from fastapi import APIRouter, HTTPException, Query, status

from core.deps import CurrentSuperuser, CurrentUser, SessionDep
from modules.knowledge.ai import build_graph, expand_node
from modules.knowledge.answer import score_answer
from modules.knowledge.assessment import NotGroundable
from modules.knowledge.assessment_store import get_or_generate
from modules.knowledge.centrality import recompute_centrality
from modules.knowledge.content import coerce_content
from modules.knowledge.cow import effective_graph
from modules.knowledge.placement import (
    NoProbeAvailable,
    next_probe,
    placement_map,
    record_answer,
)
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
    PlacementAnswerIn,
    RecomputeIn,
    UserEdgeIn,
    UserNodePatch,
)

router = APIRouter(prefix="/graph", tags=["graph"])


# ---- чтение эффективного графа (COW) ----
@router.get("/{domain}")
def get_graph(domain: str, user: CurrentUser, session: SessionDep) -> dict:
    return effective_graph(session, user.id, domain)


# ---- канон-курирование: ТОЛЬКО администратор ----
# Канон общий для всех пользователей, поэтому правит его только is_superuser.
# Обычный пользователь работает со своим слоем (COW) — секция ниже.
@router.post("/canon/build")
def build_canon(body: BuildGraphIn, user: CurrentSuperuser, session: SessionDep) -> dict:
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
            content=coerce_content(n.get("content")),
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
def create_canon_node(body: CanonNodeIn, _: CurrentSuperuser, session: SessionDep) -> dict:
    c = Concept(
        domain=body.domain,
        title=body.title,
        tier=body.tier,
        content=body.content.model_dump(),
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
    concept_id: str, body: CanonNodePatch, _: CurrentSuperuser, session: SessionDep
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
        c.content = body.content.model_dump()
        c.version += 1  # версионирование при смене контента (инвариант №5)
    session.commit()
    return {"id": str(c.id), "version": c.version}


@router.post("/canon/edges", status_code=status.HTTP_201_CREATED)
def create_canon_edge(body: CanonEdgeIn, _: CurrentSuperuser, session: SessionDep) -> dict:
    e = ConceptEdge(from_id=body.from_id, to_id=body.to_id, type=body.type)
    session.add(e)
    session.commit()
    return {"id": str(e.id)}


@router.post("/canon/recompute-centrality")
def recompute(body: RecomputeIn, _: CurrentSuperuser, session: SessionDep) -> list[dict]:
    """Пересчёт centrality + предложение узлов в ядро (гибрид метрика/курирование)."""
    return recompute_centrality(session, body.domain)


@router.post("/canon/nodes/{concept_id}/approve")
def approve_node(
    concept_id: str, body: ApproveNodeIn, _: CurrentSuperuser, session: SessionDep
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


# ---- задания по теории узла (KG3) ----
@router.get("/nodes/{concept_id}/assessment")
def node_assessment(
    concept_id: str,
    user: CurrentUser,
    session: SessionDep,
    bloom: str = Query(description="ступень Блума: remember|understand|apply|..."),
    kind: str = Query("test", description="test | practice | probe"),
) -> dict:
    """Задания по узлу — из кэша, иначе генерируются и кэшируются.

    Ключ кэша включает версию узла, поэтому правка теории сама обесценивает
    прежние задания. Персональные узлы пока не поддержаны: у них нет версии,
    а без неё кэш не обесценить.
    """
    concept = session.get(Concept, concept_id)
    if concept is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "concept не найден")
    try:
        payload, cached = get_or_generate(session, concept, bloom, kind)
    except NotGroundable as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
    return {
        "conceptId": str(concept.id),
        "conceptVersion": concept.version,
        "cached": cached,
        **payload.model_dump(),
    }


@router.post("/nodes/{concept_id}/assessment/regenerate")
def regenerate_assessment(
    concept_id: str,
    _: CurrentSuperuser,
    session: SessionDep,
    bloom: str = Query(description="ступень Блума"),
    kind: str = Query("test", description="test | practice | probe"),
) -> dict:
    """Пересобрать задания, не меняя теорию узла (курирование)."""
    concept = session.get(Concept, concept_id)
    if concept is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "concept не найден")
    try:
        payload, _cached = get_or_generate(session, concept, bloom, kind, force=True)
    except NotGroundable as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
    return {"conceptId": str(concept.id), "conceptVersion": concept.version, **payload.model_dump()}


# ---- адаптивный плейсмент (KG4) ----
@router.get("/placement/{domain}/probe")
def placement_probe(
    domain: str,
    user: CurrentUser,
    session: SessionDep,
    target: str = Query("understand", description="целевая ступень Блума — задаёт пользователь"),
) -> dict:
    """Следующий зонд на границе знаний: где ответ даст больше всего информации."""
    try:
        return next_probe(session, user.id, domain, target)
    except NoProbeAvailable as e:
        return {"done": True, "reason": str(e), "map": placement_map(session, user.id, domain)}
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e


@router.post("/placement/answer")
def placement_answer(body: PlacementAnswerIn, user: CurrentUser, session: SessionDep) -> dict:
    """Оценить ответ, обновить освоенность и выдать следующий зонд."""
    concept = session.get(Concept, str(body.concept_id))
    if concept is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "concept не найден")
    try:
        payload, _ = get_or_generate(session, concept, body.bloom, "probe")
    except NotGroundable as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e

    score, explanation = score_answer(payload.items[0], body.answer)
    state = record_answer(session, user.id, body.domain, concept.id, body.bloom, score)
    session.commit()

    result = {
        "conceptId": str(concept.id),
        "score": score,
        "explanation": explanation,
        "mastery": state.dump(),
    }
    try:
        result["next"] = next_probe(session, user.id, body.domain, body.bloom)
    except NoProbeAvailable as e:
        result["next"] = None
        result["done"] = True
        result["reason"] = str(e)
    return result


@router.get("/placement/{domain}/map")
def placement_state(domain: str, user: CurrentUser, session: SessionDep) -> dict:
    """Карта освоенности: что известно, что на границе, что закрыто предпосылками."""
    return placement_map(session, user.id, domain)


# ---- рост ветки под интересы (COW) ----
@router.post("/expand")
def expand(body: ExpandIn, user: CurrentUser, session: SessionDep) -> dict:
    """«Углубиться»: LLM дорастает ветку от узла → персональные own-узлы + рёбра (origin=grown_llm)."""
    c = session.get(Concept, str(body.concept_id))
    if c is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "concept не найден")
    result = expand_node(c.title, body.direction)

    # key из ответа модели → id созданного узла: по ним восстанавливаются связи
    # между новыми узлами, а не только «от исходного».
    by_key: dict[str, uuid.UUID] = {}
    created: list[uuid.UUID] = []
    for n in result.get("nodes", []):
        uc = UserConcept(
            user_id=user.id,
            domain=c.domain,  # ветка растёт в домене узла, от которого её тянут
            base_concept_id=None,
            title=n["title"],
            content_override=coerce_content(n.get("content")),
            origin="grown_llm",
            status="learning",
        )
        session.add(uc)
        session.flush()
        created.append(uc.id)
        for alias in (n.get("key"), n.get("title")):
            if alias:
                by_key[str(alias)] = uc.id

    def _resolve(ref: str) -> uuid.UUID | None:
        """Конец ребра — либо новый узел по ключу, либо сам исходный узел."""
        if ref in by_key:
            return by_key[ref]
        if ref in (c.title, str(c.id)):
            return c.id
        return None

    linked: set[uuid.UUID] = set()
    for e in result.get("edges", []):
        src, dst = _resolve(str(e.get("from", ""))), _resolve(str(e.get("to", "")))
        if src is None or dst is None or src == dst:
            continue
        session.add(
            UserEdge(
                user_id=user.id,
                domain=c.domain,
                from_id=src,
                to_id=dst,
                type=e.get("type", "related"),
            )
        )
        linked.add(dst)

    # Узел, который модель не связала ни с чем, всё равно должен висеть на ветке,
    # иначе он потеряется в графе.
    for node_id in created:
        if node_id not in linked:
            session.add(
                UserEdge(
                    user_id=user.id,
                    domain=c.domain,
                    from_id=c.id,
                    to_id=node_id,
                    type="related",
                )
            )
    session.commit()
    return effective_graph(session, user.id, c.domain)


# ---- персональный слой (COW) ----
@router.post("/nodes", status_code=status.HTTP_201_CREATED)
def create_own_node(body: OwnNodeIn, user: CurrentUser, session: SessionDep) -> dict:
    uc = UserConcept(
        user_id=user.id,
        domain=body.domain,
        base_concept_id=None,
        title=body.title,
        content_override=body.content.model_dump(),
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
    base = session.get(Concept, base_concept_id)
    if base is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "canonical concept не найден")
    uc = (
        session.query(UserConcept)
        .filter_by(user_id=user.id, base_concept_id=base_concept_id)
        .first()
    )
    if uc is None:
        uc = UserConcept(
            user_id=user.id,
            domain=base.domain,  # оверрайд живёт в домене перекрываемого узла
            base_concept_id=base_concept_id,
            origin="edited",
        )
        session.add(uc)
    uc.content_override = body.content.model_dump()
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
        uc.content_override = body.content.model_dump()
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
    e = UserEdge(
        user_id=user.id,
        domain=body.domain,
        from_id=body.from_id,
        to_id=body.to_id,
        type=body.type,
    )
    session.add(e)
    session.commit()
    return {"id": str(e.id)}


@router.delete("/user-edges/{edge_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user_edge(edge_id: str, user: CurrentUser, session: SessionDep) -> None:
    e = session.get(UserEdge, edge_id)
    if e is not None and e.user_id == user.id:
        session.delete(e)
        session.commit()
