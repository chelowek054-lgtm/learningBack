"""Способы изучения и приём свидетельств об освоении (T-0053, T-0062)."""

import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from core import modules
from core.deps import CurrentUser, SessionDep
from core.evidence import Evidence, dispatch
from core.methods import PREFERENCE_KEY, PURPOSES, preferences

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


class PreferenceIn(BaseModel):
    purpose: str
    method: str | None = None  # None — вернуться к способу по умолчанию


def _preferences_view(user) -> dict:
    return {
        "preferred": preferences(user.profile),
        "options": [m.describe() for m in modules.study_methods() if m.in_course],
    }


@router.get("/me/study-methods")
def get_study_methods(user: CurrentUser) -> dict:
    """Способы, из которых можно выбрать, и что выбрано сейчас."""
    return _preferences_view(user)


@router.put("/me/study-methods")
def set_study_method(body: PreferenceIn, user: CurrentUser, session: SessionDep) -> dict:
    """Выбрать способ для шага изучения и пересобрать под него курс.

    Освоенность, ошибки и карточки не трогаются: они принадлежат человеку, не способу.
    """
    if body.purpose not in PURPOSES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Неизвестный шаг изучения")
    if body.method is not None:
        known = {m.id for m in modules.study_methods() if m.in_course and m.purpose == body.purpose}
        if body.method not in known:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "Этот способ недоступен для шага"
            )
    chosen = preferences(user.profile)
    if body.method is None:
        chosen.pop(body.purpose, None)
    else:
        chosen[body.purpose] = body.method
    user.profile = {**(user.profile or {}), PREFERENCE_KEY: chosen}
    session.flush()
    modules.notify_methods_changed(session, user.id)
    session.commit()
    return _preferences_view(user)
