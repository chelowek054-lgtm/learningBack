"""Язык текста: определение без модели и список поддерживаемых языков (R-0079, R-0080, T-0176).

Основной язык графа — английский (A-0034), второй — русский. Язык документа нужен при приёме: поиск и
разбор идут на обоих, текст на неподдерживаемом языке отклоняется с причиной, а не уходит в модель.
"""

from __future__ import annotations

import re

from core.config import settings

CANONICAL = "en"

# Частые служебные слова английского: их доля в тексте отличает английский от других латиничных языков.
_EN_STOP = frozenset(
    "the of and to in is that for it as with are this by on be at from or an which can has have not "
    "was were we if then these their its".split()
)
_WORD = re.compile(r"[A-Za-zА-Яа-яЁё]+")
_CYR = re.compile(r"[А-Яа-яЁё]")
_LAT = re.compile(r"[A-Za-z]")


def supported() -> list[str]:
    """Поддерживаемые языки: основной первым."""
    langs = [x.strip() for x in settings.graph_languages.split(",") if x.strip()]
    return langs or [CANONICAL]


def detect_lang(text: str) -> str | None:
    """`en`, `ru` или None, если язык не опознан (мало текста, другой алфавит или другой язык)."""
    sample = text[:20000]
    cyr = len(_CYR.findall(sample))
    lat = len(_LAT.findall(sample))
    letters = cyr + lat
    if letters < 200:
        return None
    if cyr / letters >= 0.6:
        return "ru"
    if lat / letters >= 0.6:
        words = [w.lower() for w in _WORD.findall(sample)]
        if not words:
            return None
        share = sum(1 for w in words if w in _EN_STOP) / len(words)
        return "en" if share >= 0.08 else None
    return None


def language_name(code: str) -> str:
    return {"en": "английский", "ru": "русский"}.get(code, code)
