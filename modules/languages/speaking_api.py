"""Загрузка записи устного ответа (T-0040): файл с устройства, затем job transcribe в очередь."""

from fastapi import APIRouter, HTTPException, UploadFile, status

from core.config import settings
from core.deps import CurrentUser
from modules.languages import speech

router = APIRouter(prefix="/languages/speaking", tags=["languages"])


@router.post("/audio", status_code=status.HTTP_201_CREATED)
async def upload(file: UploadFile, user: CurrentUser) -> dict:
    """Принять запись; вернуть audioId для job `transcribe`. Файл виден только владельцу."""
    data = await file.read(settings.max_voice_bytes + 1)
    if not data:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Запись пустая")
    if len(data) > settings.max_voice_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Запись слишком большая")
    return {"audioId": speech.store_voice(user.id, data), "mime": file.content_type or ""}
