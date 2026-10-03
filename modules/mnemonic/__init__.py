"""Вторая техника запоминания: вспомнить по первым буквам (T-0063, R-0034).

От интервального повторения отличается устройством, а не названием: нет расписания и
карточки в очереди — формулировка узла показывается с пропущенными словами, человек
восстанавливает её по первым буквам и сам оценивает, как вышло. Результат уходит в граф
свидетельством об освоении (core.evidence), поэтому смена способа освоенность не стирает.

Модуль не знает графа: узел приходит готовым payload'ом, а ответ уходит общим форматом.
"""

from __future__ import annotations

import re
from typing import Any

from core.manifest import ModuleManifest
from core.methods import REMEMBER, StudyMethod
from core.modules import BackendModule

MODULE_ID = "mnemonic"
ACTIVITY_TYPE = "concept_mnemonic"

# Самооценка вспоминания → результат от 0 до 1 (те же ступени, что у оценки карточки).
SELF_RATING_SCORE = {"again": 0.0, "hard": 0.4, "good": 0.8, "easy": 1.0}

_WORD = re.compile(r"\w+", re.UNICODE)
_MAX_CHARS = 280


def _sentences_up_to(text: str, limit: int) -> str:
    """Начало текста по целым предложениям, но не короче первого."""
    parts = re.split(r"(?<=[.!?])\s+", text.strip())
    out = ""
    for part in parts:
        if out and len(out) + 1 + len(part) > limit:
            break
        out = f"{out} {part}".strip()
    return out


def mask_text(text: str) -> str:
    """Оставить у каждого слова первую букву, остальное скрыть; знаки препинания целы."""

    def hide(match: re.Match[str]) -> str:
        word = match.group(0)
        return word if len(word) < 3 else word[0] + "_" * (len(word) - 1)

    return _WORD.sub(hide, text)


class MnemonicModule(BackendModule):
    id = MODULE_ID
    first_party = True
    manifest = ModuleManifest(
        id=MODULE_ID,
        title="Вспомнить по первым буквам",
        version="1.0",
        provides=frozenset({"study_methods", "activity_payload"}),
    )

    def study_methods(self) -> list[StudyMethod]:
        return [
            StudyMethod(
                "first_letters",
                "Вспомнить по первым буквам",
                REMEMBER,
                ACTIVITY_TYPE,
                offline=True,
            )
        ]

    def activity_payload(self, activity_type: str, node: dict[str, Any]) -> dict[str, Any] | None:
        if activity_type != ACTIVITY_TYPE:
            return None
        summary = ((node.get("content") or {}).get("summary") or "").strip()
        if not summary:
            return None  # без формулировки вспоминать нечего — активность пропускается
        answer = _sentences_up_to(summary, _MAX_CHARS)
        base = {k: v for k, v in node.items() if k != "content"}
        return {**base, "cue": mask_text(answer), "answer": answer}


backend = MnemonicModule()

__all__ = ["ACTIVITY_TYPE", "MODULE_ID", "SELF_RATING_SCORE", "backend", "mask_text"]
