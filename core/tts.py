"""Порт «текст в речь» (T-0039, R-0022): озвучка материалов для дрилла аудирования.

Ядро знает только контракт: текст на входе, байты аудио на выходе. Провайдер выбирается
конфигурацией, как у LLM (ключ — из окружения, инвариант №2); без ключа работает
детерминированная заглушка, чтобы цепочка «материал → аудио → кэш на устройстве» проверялась
без сети и без расходов.
"""

from __future__ import annotations

import hashlib
from typing import Protocol

import httpx

from core.ai_gateway.base import ProviderError
from core.config import settings


class TextToSpeech(Protocol):
    mime: str

    def synthesize(self, text: str) -> bytes: ...


class MockTTS:
    """Заглушка: «аудио» — детерминированные байты от текста; воспроизвести их нельзя."""

    mime = "audio/x-mock"

    def synthesize(self, text: str) -> bytes:
        return b"MOCKAUDIO:" + hashlib.sha256(text.encode("utf-8")).digest()


class OpenAICompatibleTTS:
    """Любой сервис с совместимым `POST /audio/speech`."""

    mime = "audio/mpeg"

    def __init__(self) -> None:
        self._client = httpx.Client(
            base_url=settings.llm_base_url,
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            timeout=httpx.Timeout(settings.llm_timeout_seconds),
        )

    def synthesize(self, text: str) -> bytes:
        try:
            r = self._client.post(
                "/audio/speech",
                json={"model": settings.tts_model, "voice": settings.tts_voice, "input": text},
            )
            r.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"Озвучка недоступна: {exc}") from exc
        return r.content


def get_tts() -> TextToSpeech:
    return OpenAICompatibleTTS() if settings.llm_api_key and settings.tts_model else MockTTS()
