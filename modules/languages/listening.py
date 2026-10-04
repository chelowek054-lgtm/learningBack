"""Материалы для аудирования: генерация, озвучка, курирование (T-0039, R-0022).

Passage и вопросы с дистракторами строит модель (`structured`), озвучку — порт TTS. Материал
рождается черновиком с оценкой уверенности и до подтверждения куратором учащимся не выдаётся:
сгенерированный вопрос с неверным «верным» ответом хуже его отсутствия.
Состояние живёт в `Material.content`, отдельной таблицы нет: материал общий (user_id = null).
"""

from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from core.config import settings
from core.models import Material
from core.tts import TextToSpeech

MODULE_ID = "languages"
SOURCE = "generated"
KIND = "listening"
DRAFT, APPROVED = "draft", "approved"

MIN_OPTIONS = 3
MIN_QUESTIONS = 2
MIN_PASSAGE_WORDS = 30

_TOOL = "make_listening_material"
_DESC = "Составь короткий монолог для аудирования и вопросы к нему с вариантами ответа."
SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "passage": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string"},
                    "options": {"type": "array", "items": {"type": "string"}},
                    "answer": {"type": "string"},
                    "explanation": {"type": "string"},
                },
                "required": ["prompt", "options", "answer"],
            },
        },
    },
    "required": ["passage", "questions"],
}


class GenerationFailed(Exception):
    """Модель не дала годного материала; `str(e)` — причина для куратора."""


def _prompt(topic: str, level: str) -> str:
    return (
        f"Write a spoken monologue (80-150 words) for an English listening drill, level {level}, "
        f"topic: {topic}. Then write 3-4 multiple-choice questions about facts stated in it. "
        "Each question has 4 options: exactly one correct answer taken from the text, "
        "and plausible wrong options built from common misunderstandings. "
        "Give 'answer' as the exact text of the correct option. Rate your confidence 0..1."
    )


def clean_questions(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Оставить только годные вопросы: верный ответ — один из вариантов, варианты не повторяются."""
    out: list[dict[str, Any]] = []
    for q in raw or []:
        options = [str(o).strip() for o in q.get("options", []) if str(o).strip()]
        answer = str(q.get("answer", "")).strip()
        if (
            not str(q.get("prompt", "")).strip()
            or len(options) < MIN_OPTIONS
            or len(set(options)) != len(options)
            or answer not in options
        ):
            continue
        out.append(
            {
                "id": f"q{len(out) + 1}",
                "type": "mcq",
                "prompt": str(q["prompt"]).strip(),
                "options": options,
                "answer": answer,
                "explanation": str(q.get("explanation", "")).strip(),
            }
        )
    return out


def confidence(raw: dict[str, Any], kept: int) -> float:
    """Уверенность: заявленная моделью, сниженная долей отброшенных вопросов."""
    asked = max(len(raw.get("questions") or []), 1)
    declared = raw.get("confidence")
    declared = float(declared) if isinstance(declared, (int, float)) else 0.5
    return round(max(0.0, min(1.0, declared)) * kept / asked, 2)


def _audio_path(text: str) -> Path:
    return Path(settings.audio_dir) / (hashlib.sha256(text.encode("utf-8")).hexdigest() + ".bin")


def generate(
    session: Session, gateway, tts: TextToSpeech, topic: str, level: str = "B2"
) -> Material:
    raw = gateway.structured(_TOOL, _DESC, SCHEMA, _prompt(topic, level)) or {}
    passage = str(raw.get("passage", "")).strip()
    if len(re.findall(r"\w+", passage)) < MIN_PASSAGE_WORDS:
        raise GenerationFailed("Модель не вернула текст для аудирования (или он слишком короткий)")
    questions = clean_questions(raw.get("questions") or [])
    if len(questions) < MIN_QUESTIONS:
        raise GenerationFailed("Годных вопросов меньше двух: ответ не входит в варианты")
    path = _audio_path(passage)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(tts.synthesize(passage))
    material = Material(
        user_id=None,
        module=MODULE_ID,
        source=SOURCE,
        title=str(raw.get("title") or topic)[:200],
        content={
            "kind": KIND,
            "status": DRAFT,
            "confidence": confidence(raw, len(questions)),
            "topic": topic,
            "level": level,
            "passage": passage,
            "questions": questions,
            "audio": {"file": path.name, "mime": tts.mime},
        },
    )
    session.add(material)
    session.flush()
    return material


def listing(session: Session, status: str | None = None) -> list[Material]:
    rows = (
        session.query(Material)
        .filter_by(module=MODULE_ID, source=SOURCE)
        .order_by(Material.created_at.desc())
        .all()
    )
    return [
        m
        for m in rows
        if (m.content or {}).get("kind") == KIND
        and (status is None or m.content.get("status") == status)
    ]


def get(session: Session, material_id: uuid.UUID) -> Material | None:
    m = session.get(Material, material_id)
    return m if m is not None and (m.content or {}).get("kind") == KIND else None


def set_status(session: Session, material_id: uuid.UUID, status: str) -> Material | None:
    m = get(session, material_id)
    if m is None:
        return None
    m.content = {**m.content, "status": status}
    session.flush()
    return m


def audio_file(material: Material) -> Path | None:
    name = ((material.content or {}).get("audio") or {}).get("file")
    path = Path(settings.audio_dir) / name if name else None
    return path if path and path.is_file() else None


def describe(m: Material, *, passage: bool) -> dict[str, Any]:
    """Учащемуся текст не отдаётся: это аудирование, текст видит только куратор."""
    c = m.content
    out = {
        "id": str(m.id),
        "title": m.title,
        "status": c["status"],
        "confidence": c["confidence"],
        "level": c.get("level"),
        "audioMime": (c.get("audio") or {}).get("mime"),
        "questions": c["questions"],
    }
    if passage:
        out["passage"] = c["passage"]
    return out
