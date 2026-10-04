"""Устный ответ: запись, расшифровка, метрики темпа и пауз (T-0040…T-0043, R-0023).

Запись приходит с устройства файлом и живёт ровно до расшифровки: голос — биометрия, поэтому
после успешной расшифровки файл удаляется, а дальше работает только текст с таймингами
(политика хранения голоса — T-0044). Оценка идёт по тексту и таймингам; произношение по ним
не проверить, и рубрика честно говорит об этом пометкой (`caveat`).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from core.config import settings
from core.models import Job, Response
from core.stt import Transcript, get_stt

TRANSCRIBE_JOB = "transcribe"
GRADE_JOB = "grade_speaking"
RUBRIC_ID = "ielts_speaking"
LONG_PAUSE_SEC = 1.0


def voice_path(user_id: uuid.UUID, audio_id: str) -> Path:
    # audio_id приходит от клиента: только UUID, иначе путь можно увести за каталог.
    return Path(settings.voice_dir) / str(user_id) / f"{uuid.UUID(audio_id)}.bin"


def store_voice(user_id: uuid.UUID, data: bytes) -> str:
    audio_id = str(uuid.uuid4())
    path = voice_path(user_id, audio_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return audio_id


def metrics(t: Transcript) -> dict[str, Any]:
    """Темп и паузы по таймингам слов: единственное, что видно в речи без звука."""
    gaps = [b.start - a.end for a, b in zip(t.words, t.words[1:], strict=False)]
    long_pauses = [g for g in gaps if g >= LONG_PAUSE_SEC]
    speaking = max(t.duration, 0.1)
    return {
        "words": len(t.words),
        "durationSec": round(t.duration, 1),
        "wordsPerMinute": round(len(t.words) / speaking * 60),
        "longPauses": len(long_pauses),
        "longestPauseSec": round(max(gaps, default=0.0), 1),
    }


def as_text(t: Transcript, m: dict[str, Any]) -> str:
    return (
        f"{t.text}\n\n[Тайминги: {m['wordsPerMinute']} слов/мин, пауз длиннее "
        f"{LONG_PAUSE_SEC:g} с: {m['longPauses']}, самая длинная пауза {m['longestPauseSec']} с]"
    )


def transcribe_job(session, job: Job, gateway) -> dict[str, Any]:
    """Расшифровать запись → записать текст в ответ → поставить оценку. ValueError — навсегда."""
    ref = job.input_ref or {}
    try:
        path = voice_path(job.user_id, str(ref.get("audioId")))
    except ValueError as e:
        raise ValueError("audioId некорректен") from e
    response = session.get(Response, ref.get("responseId")) if ref.get("responseId") else None
    if response is None or response.user_id != job.user_id:
        raise ValueError("responseId не найден")
    if not path.is_file():
        raise ValueError("Запись не найдена: возможно, она уже обработана или удалена")

    t = get_stt().transcribe(path.read_bytes(), str(ref.get("mime", "")))
    m = metrics(t)
    response.user_answer = {
        "transcript": t.text,
        "words": [{"w": w.word, "s": w.start, "e": w.end} for w in t.words],
        "metrics": m,
        "asText": as_text(t, m),
    }
    path.unlink(missing_ok=True)  # голос не хранится дольше нужного
    session.add(
        Job(
            user_id=job.user_id,
            type=GRADE_JOB,
            status="pending",
            input_ref={"responseId": str(response.id), "rubricId": RUBRIC_ID},
        )
    )
    return {"responseId": str(response.id), "metrics": m, "voiceDeleted": True}
