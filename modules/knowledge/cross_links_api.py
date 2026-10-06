"""API связей между понятиями разных областей (T-0065): менять может куратор, читать — любой."""

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from core.deps import CurrentSuperuser, CurrentUser, SessionDep
from modules.knowledge import chain_placement, cross_links, path_volume, prior_report
from modules.knowledge.answer import score_answer
from modules.knowledge.assessment import NotGroundable
from modules.knowledge.assessment_store import get_or_generate
from modules.knowledge.models import Concept
from modules.knowledge.placement import PROBE_KIND, NoProbeAvailable, record_answer

router = APIRouter(tags=["cross-links"])


class ChainAnswerIn(BaseModel):
    concept_id: uuid.UUID
    bloom: str = Field(min_length=1)
    answer: Any = None


class LinkIn(BaseModel):
    from_id: uuid.UUID
    to_id: uuid.UUID
    bloom: str = Field(min_length=1)


@router.post("/concept-links", status_code=status.HTTP_201_CREATED)
def add_concept_link(body: LinkIn, _: CurrentSuperuser, session: SessionDep) -> dict:
    """Предпосылка из другой области: понятие `from_id` нужно знать для `to_id` с ступени `bloom`."""
    try:
        link = cross_links.add_link(session, body.from_id, body.to_id, body.bloom)
    except cross_links.LinkError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
    session.commit()
    return {
        "id": str(link.id),
        "fromId": str(link.from_id),
        "toId": str(link.to_id),
        "bloom": link.bloom,
    }


@router.get("/concept-links/{concept_id}")
def concept_links(concept_id: uuid.UUID, _: CurrentUser, session: SessionDep) -> list[dict]:
    """Что нужно знать из других областей, чтобы освоить понятие."""
    return [
        {"fromId": str(link.from_id), "bloom": link.bloom}
        for link in cross_links.links_into(session, concept_id)
    ]


@router.get("/placement/{domain}/chain")
def placement_chain(
    domain: str, user: CurrentUser, session: SessionDep, target: str = "understand"
) -> list[dict]:
    """Базовые области под целью: что освоено, что снято освоенным вышестоящим, что не проверено."""
    try:
        return chain_placement.chain_plan(session, user.id, domain, target)
    except cross_links.LinkError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e


@router.get("/placement/{domain}/chain-probe")
def placement_chain_probe(
    domain: str, user: CurrentUser, session: SessionDep, target: str = "understand"
) -> dict:
    """Следующий зонд по цепочке сверху вниз; `done` — спрашивать больше не о чем."""
    try:
        return chain_placement.next_chain_probe(session, user.id, domain, target)
    except NoProbeAvailable as e:
        return {"done": True, "reason": str(e), "code": e.code}
    except cross_links.LinkError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e


@router.get("/goal/{domain}/volume")
def goal_volume(domain: str, _: CurrentUser, session: SessionDep, target: str = "apply") -> dict:
    """Сколько базовых областей и понятий лежит под целью: полный и интуитивный варианты."""
    return path_volume.volume(session, domain, target)


@router.post("/placement/{domain}/chain-answer")
def placement_chain_answer(
    domain: str,
    body: ChainAnswerIn,
    user: CurrentUser,
    session: SessionDep,
    target: str = "understand",
) -> dict:
    """Оценить ответ на зонд цепочки, записать освоенность в профиль и выдать следующий зонд и отчёт."""
    concept = session.get(Concept, str(body.concept_id))
    if concept is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "concept не найден")
    try:
        payload, _ = get_or_generate(session, concept, body.bloom, PROBE_KIND)
    except NotGroundable as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e

    score, explanation = score_answer(payload.items[0], body.answer)
    state = record_answer(session, user.id, concept.domain, concept.id, body.bloom, score)
    session.commit()

    result: dict = {
        "conceptId": str(concept.id),
        "score": score,
        "explanation": explanation,
        "mastery": state.dump(),
    }
    try:
        result["next"] = chain_placement.next_chain_probe(session, user.id, domain, target)
    except NoProbeAvailable as e:
        result.update(next=None, done=True, reason=str(e), code=e.code)
    except cross_links.LinkError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
    result["report"] = prior_report.report(session, user.id, domain, target)
    return result


@router.get("/placement/{domain}/report")
def placement_report(
    domain: str, user: CurrentUser, session: SessionDep, target: str = "understand"
) -> dict:
    """Базовые области под целью: хватает / мало / нет / не проверено / нет в графе."""
    try:
        return prior_report.report(session, user.id, domain, target)
    except cross_links.LinkError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
