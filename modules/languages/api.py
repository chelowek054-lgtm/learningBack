"""API материалов аудирования (T-0039): куратор генерирует и подтверждает, учащийся читает approved."""

import uuid

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from core.ai_gateway import get_ai_gateway
from core.ai_gateway.base import ProviderError
from core.deps import CurrentSuperuser, CurrentUser, SessionDep
from core.tts import get_tts
from modules.languages import listening
from modules.languages.speaking_api import router as speaking_router

listening_router = APIRouter(prefix="/languages/listening", tags=["languages"])


class GenerateIn(BaseModel):
    topic: str = Field(min_length=3, max_length=200)
    level: str = Field(default="B2", max_length=10)


def _id(raw: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Материал не найден") from e


@listening_router.post("/generate", status_code=status.HTTP_201_CREATED)
def generate(body: GenerateIn, _: CurrentSuperuser, session: SessionDep) -> dict:
    try:
        material = listening.generate(session, get_ai_gateway(), get_tts(), body.topic, body.level)
    except listening.GenerationFailed as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
    except ProviderError as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(e)) from e
    return listening.describe(material, passage=True)


@listening_router.get("")
def list_approved(_: CurrentUser, session: SessionDep) -> list[dict]:
    """Учащимся — только подтверждённое и без текста."""
    return [
        listening.describe(m, passage=False) for m in listening.listing(session, listening.APPROVED)
    ]


@listening_router.get("/drafts")
def list_drafts(_: CurrentSuperuser, session: SessionDep) -> list[dict]:
    return [
        listening.describe(m, passage=True) for m in listening.listing(session, listening.DRAFT)
    ]


def _set(session, material_id: str, new_status: str) -> dict:
    m = listening.set_status(session, _id(material_id), new_status)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Материал не найден")
    return listening.describe(m, passage=True)


@listening_router.post("/{material_id}/approve")
def approve(material_id: str, _: CurrentSuperuser, session: SessionDep) -> dict:
    return _set(session, material_id, listening.APPROVED)


@listening_router.post("/{material_id}/reject")
def reject(material_id: str, _: CurrentSuperuser, session: SessionDep) -> dict:
    return _set(session, material_id, listening.DRAFT)


@listening_router.get("/{material_id}/audio")
def audio(material_id: str, _: CurrentUser, session: SessionDep):
    m = listening.get(session, _id(material_id))
    path = listening.audio_file(m) if m is not None else None
    if m is None or m.content.get("status") != listening.APPROVED or path is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Аудио не найдено")
    return FileResponse(path, media_type=m.content["audio"]["mime"])


router = APIRouter()
router.include_router(listening_router)
router.include_router(speaking_router)
