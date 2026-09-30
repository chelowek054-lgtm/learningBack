"""Кэш ответов LLM по хэшу входа (FR-AI-06, NFR-09).

Кэшируется только то, что детерминировано по входу: оценка одного и того же
ответа по той же рубрике. Вызов сам решает, можно ли ему кэш (`cache=True`):
построение графа, например, нельзя — «перестроить» должно давать новый
результат. Ключ включает модель и схему, поэтому смена модели или формата
ответа автоматически обесценивает прежние записи.

Сбой кэша не ломает вызов: промах хуже, чем ошибка пользователю.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from core.db import SessionLocal
from core.models import LlmCache

log = logging.getLogger(__name__)


def make_key(model: str, tool_name: str, schema: dict[str, Any], prompt: str) -> str:
    raw = json.dumps([model, tool_name, schema, prompt], sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get(key: str, session_factory: Callable[[], Session] | None = None) -> dict[str, Any] | None:
    try:
        with (session_factory or SessionLocal)() as session:
            row = session.get(LlmCache, key)
            return row.payload if row is not None else None
    except Exception:  # noqa: BLE001
        log.warning("Кэш LLM недоступен (чтение)", exc_info=True)
        return None


def put(
    key: str,
    payload: dict[str, Any],
    session_factory: Callable[[], Session] | None = None,
) -> None:
    try:
        with (session_factory or SessionLocal)() as session:
            if session.get(LlmCache, key) is None:
                session.add(LlmCache(key=key, payload=payload))
                session.commit()
    except Exception:  # noqa: BLE001
        log.warning("Кэш LLM недоступен (запись)", exc_info=True)
