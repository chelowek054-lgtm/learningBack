"""Теория понятия по требованию (T-0121, R-0064).

Скелет графа хранит у понятия только короткое пояснение. Когда человек открывает шаг курса, а в понятии нет
разделов теории, здесь строятся разделы под уровень понятия и сохраняются в `content` (версия растёт, кэш заданий
по старой теории обесценивается, как при правке куратором). Для всего графа сразу теория не готовится.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from core.ai_gateway import get_ai_gateway, has_llm
from modules.knowledge import provenance
from modules.knowledge.content import NodeContent, coerce_content
from modules.knowledge.models import Concept

log = logging.getLogger(__name__)

AUTO_NOTE = "Составлено автоматически, не проверено специалистом"

THEORY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sections": {
            "type": "array",
            "description": "4–6 разделов теории",
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "body": {
                        "type": "string",
                        "description": "объяснение простыми словами, 3–6 предложений",
                    },
                    "examples": {"type": "array", "items": {"type": "string"}},
                    "counter_examples": {
                        "type": "array",
                        "description": "типичные ошибки и что в них неверно",
                        "items": {"type": "string"},
                    },
                },
                "required": ["heading", "body"],
            },
        }
    },
    "required": ["sections"],
}

_LEVEL = {"basic": "начальный", "middle": "средний", "advanced": "продвинутый"}


def needs_theory(content: dict[str, Any]) -> bool:
    return not NodeContent.model_validate(content).sections


def ensure_theory(session: Session, concept: Concept) -> bool:
    """Построить и сохранить теорию, если её нет; True — теория появилась. Сбой модели шаг не ломает."""
    content = coerce_content(concept.content)
    if not needs_theory(content) or not has_llm():
        return False
    sources = provenance.concept_sources(session, concept.id)
    try:
        raw = get_ai_gateway().structured(
            "submit_theory",
            "Вернуть разделы теории понятия.",
            THEORY_SCHEMA,
            (
                f"Понятие «{concept.title}» из области «{concept.domain}»"
                f"{f', этап «{concept.stage}»' if concept.stage else ''}. "
                f"Короткое описание: {content.get('summary', '')}. "
                f"Уровень понятия: {_LEVEL.get(concept.level or '', 'средний')}. "
                "Напиши теорию по разделам: определение простыми словами; зачем нужно и где встречается; "
                "как устроено; пример с разбором; типичные ошибки и что путают. Без воды, по делу."
            ),
        )
        sections = NodeContent.model_validate(
            {"sections": (raw or {}).get("sections") or []}
        ).sections
    except Exception:  # noqa: BLE001 — шаг открывается и с коротким описанием
        log.warning("Теория понятия не построена", exc_info=True)
        return False
    if not sections:
        return False
    note = "По источнику" if sources else AUTO_NOTE
    content["sections"] = [s.model_dump() for s in sections]
    content["references"] = [{"title": note}]
    concept.content = NodeContent.model_validate(content).model_dump()
    concept.version += 1  # версия растёт: кэш заданий по старой теории обесценивается
    session.flush()
    return True
