"""API связей между понятиями разных областей (T-0065): менять может куратор, читать — любой."""

import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from core.deps import CurrentSuperuser, CurrentUser, SessionDep
from modules.knowledge import chain_placement, cross_links, path_volume
from modules.knowledge.placement import NoProbeAvailable

router = APIRouter(tags=["cross-links"])


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
