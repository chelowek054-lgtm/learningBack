"""Способы изучения и приём свидетельств об освоении (T-0053, T-0062)."""

import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from core import modules
from core.deps import CurrentUser, SessionDep
from core.evidence import Evidence, dispatch

router = APIRouter(tags=["methods"])


@router.get("/methods")
def list_methods(_: CurrentUser) -> list[dict]:
    """Способы изучения включённых модулей: что человек может выбрать."""
    return [m.describe() for m in modules.study_methods()]


class EvidenceIn(BaseModel):
    domain: str = Field(min_length=1)
    concept_id: uuid.UUID = Field(alias="conceptId")
    bloom: str = Field(min_length=1)
    score: float = Field(ge=0.0, le=1.0)
    source: str = "unknown"

    model_config = {"populate_by_name": True}


@router.post("/evidence", status_code=status.HTTP_202_ACCEPTED)
def submit_evidence(body: EvidenceIn, user: CurrentUser, session: SessionDep) -> dict:
    """Свидетельство об освоении от любого способа: освоенность принадлежит человеку, не способу."""
    accepted = dispatch(
        session,
        user.id,
        body.domain,
        Evidence(body.concept_id, body.bloom, body.score, body.source),
        modules.enabled_modules(),
    )
    if accepted == 0:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Нет включённого модуля, который принимает свидетельства"
        )
    session.commit()
    return {"accepted": accepted}
