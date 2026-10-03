"""API графа областей (T-0064): читать может любой вошедший, менять — куратор (канон общий)."""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from core.deps import CurrentSuperuser, CurrentUser, SessionDep
from modules.knowledge import domains

router = APIRouter(tags=["domains"])


class DomainIn(BaseModel):
    title: str = Field(min_length=1)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    foundation: bool = False


class PrereqIn(BaseModel):
    prereq: str = Field(min_length=1)


def _fail(e: domains.DomainError) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e))


@router.get("/domains")
def list_domains(_: CurrentUser, session: SessionDep) -> list[dict]:
    """Области с вычисленным уровнем примитивности, от самых примитивных."""
    return domains.listing(session)


@router.post("/domains", status_code=status.HTTP_201_CREATED)
def register_domain(body: DomainIn, _: CurrentSuperuser, session: SessionDep) -> dict:
    """Завести область или вернуть существующую под тем же названием или алиасом."""
    try:
        domain, created = domains.register(
            session, body.title, aliases=body.aliases, foundation=body.foundation
        )
    except domains.DomainError as e:
        raise _fail(e) from e
    session.commit()
    return {"key": domain.key, "title": domain.title, "created": created}


@router.post("/domains/{key}/prereqs", status_code=status.HTTP_201_CREATED)
def add_domain_prereq(key: str, body: PrereqIn, _: CurrentSuperuser, session: SessionDep) -> dict:
    """«Нужно знать до»: имя можно давать как ключом, так и названием или алиасом."""
    domain, prereq = domains.resolve(session, key), domains.resolve(session, body.prereq)
    if domain is None or prereq is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Область не найдена")
    try:
        domains.add_prereq(session, domain.key, prereq.key)
    except domains.DomainError as e:
        raise _fail(e) from e
    session.commit()
    return {"domain": domain.key, "prereq": prereq.key}


@router.get("/domains/{key}/chain")
def domain_chain(key: str, _: CurrentUser, session: SessionDep) -> dict:
    """Что нужно знать до области: цепочка от самого примитивного к ближайшему."""
    domain = domains.resolve(session, key)
    if domain is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Область не найдена")
    return {
        "key": domain.key,
        "level": domains.levels(session)[domain.key],
        "chain": domains.chain(session, domain.key),
    }
