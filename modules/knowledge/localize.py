"""Язык изучения человека: теория, названия и задания узла на нём (R-0079, T-0174).

Язык берётся из запроса (`lang`), затем из профиля (`studyLang`), иначе ничего не подменяется и узел
отдаётся на языке по умолчанию. Тексты на других языках лежат в `content.i18n`, названия — в
`concept_term`; модель при чтении не участвует.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import Depends, Query
from sqlalchemy.orm import Session

from core.deps import CurrentUser
from modules.knowledge import langs
from modules.knowledge.models import Concept, ConceptTerm

PROFILE_KEY = "studyLang"


def study_lang(profile: dict[str, Any] | None, requested: str | None = None) -> str | None:
    """Язык ответа: запрошенный, иначе из профиля; неподдерживаемый отбрасывается."""
    ok = langs.supported()
    for candidate in (requested, (profile or {}).get(PROFILE_KEY)):
        if isinstance(candidate, str) and candidate in ok:
            return candidate
    return None


def get_lang(user: CurrentUser, lang: str | None = Query(None, max_length=8)) -> str | None:
    return study_lang(user.profile, lang)


StudyLang = Annotated[str | None, Depends(get_lang)]


def localize_content(content: dict[str, Any] | None, lang: str | None) -> dict[str, Any]:
    """Теория узла на языке `lang`; нет перевода — как есть."""
    data = dict(content or {})
    if not lang:
        return data
    block = (data.get("i18n") or {}).get(lang)
    if isinstance(block, dict) and block.get("sections"):
        data["summary"] = block.get("summary") or data.get("summary", "")
        data["sections"] = block["sections"]
    return data


def term_titles(session: Session, concept_ids: list[uuid.UUID], lang: str | None) -> dict[str, str]:
    """concept_id → название на языке `lang` (основное название языка, если оно есть)."""
    if not lang or not concept_ids:
        return {}
    rows = (
        session.query(ConceptTerm)
        .filter(ConceptTerm.concept_id.in_(concept_ids), ConceptTerm.lang == lang)
        .order_by(ConceptTerm.primary.desc())
        .all()
    )
    out: dict[str, str] = {}
    for r in rows:
        out.setdefault(str(r.concept_id), r.term)
    return out


def title_of(session: Session, concept: Concept, lang: str | None) -> str:
    return term_titles(session, [concept.id], lang).get(str(concept.id), concept.title)
