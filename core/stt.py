"""Порт «речь в текст» (T-0041, R-0023): расшифровка записи ответа с таймингами слов.

Контракт: байты записи на входе, текст со словами и их временем на выходе. Провайдер выбирается
конфигурацией (ключ — из окружения); без ключа работает заглушка, чтобы цепочку «запись →
расшифровка → оценка» проверять без сети. Понятная ошибка вместо пустой оценки — часть
контракта: длинная, тихая или пустая запись — `SpeechError`, а не расшифровка из нуля слов.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx

from core.ai_base import ProviderError
from core.config import settings

MAX_SECONDS = 180.0
MIN_WORDS = 3
NO_SPEECH = "В записи почти нет речи: проверьте микрофон и запишите ответ ещё раз"


class SpeechError(ValueError):
    """С записью что-то не так, повтор не поможет: `str(e)` можно показать человеку."""


@dataclass(frozen=True)
class Word:
    word: str
    start: float
    end: float


@dataclass(frozen=True)
class Transcript:
    text: str
    words: list[Word]
    duration: float


class SpeechToText(Protocol):
    def transcribe(self, audio: bytes, mime: str) -> Transcript: ...


def check_transcript(t: Transcript) -> Transcript:
    """Годится ли расшифровка для оценки; иначе — понятная причина."""
    if t.duration > MAX_SECONDS:
        raise SpeechError(f"Запись длиннее {int(MAX_SECONDS)} секунд: запишите ответ короче")
    if len(t.words) < MIN_WORDS:
        raise SpeechError(NO_SPEECH)
    return t


class MockSTT:
    """Заглушка: запись вида `MOCKSPEECH:<текст>` «расшифровывается» в этот текст, слово = 0.4 с."""

    PREFIX = b"MOCKSPEECH:"

    def transcribe(self, audio: bytes, mime: str) -> Transcript:
        if not audio.startswith(self.PREFIX):
            raise SpeechError(NO_SPEECH)
        text = audio[len(self.PREFIX) :].decode("utf-8", errors="replace").strip()
        words = [
            Word(w, round(i * 0.4, 2), round(i * 0.4 + 0.35, 2)) for i, w in enumerate(text.split())
        ]
        return check_transcript(Transcript(text, words, words[-1].end if words else 0.0))


class OpenAICompatibleSTT:
    """Сервис с совместимым `POST /audio/transcriptions` и таймингами слов (verbose_json)."""

    def __init__(self) -> None:
        self._client = httpx.Client(
            base_url=settings.llm_base_url,
            headers={"Authorization": f"Bearer {settings.llm_api_key}"},
            timeout=httpx.Timeout(settings.llm_timeout_seconds),
        )

    def transcribe(self, audio: bytes, mime: str) -> Transcript:
        try:
            r = self._client.post(
                "/audio/transcriptions",
                data={
                    "model": settings.stt_model,
                    "response_format": "verbose_json",
                    "timestamp_granularities[]": "word",
                },
                files={"file": ("answer", audio, mime or "application/octet-stream")},
            )
            r.raise_for_status()
            data = r.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ProviderError(f"Расшифровка недоступна: {exc}") from exc
        words = [
            Word(str(w["word"]).strip(), float(w["start"]), float(w["end"]))
            for w in data.get("words", [])
        ]
        return check_transcript(
            Transcript(
                str(data.get("text", "")).strip(),
                words,
                float(data.get("duration") or (words[-1].end if words else 0.0)),
            )
        )


def get_stt() -> SpeechToText:
    return OpenAICompatibleSTT() if settings.llm_api_key and settings.stt_model else MockSTT()
