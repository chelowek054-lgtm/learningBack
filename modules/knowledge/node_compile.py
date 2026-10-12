"""Сборка узла заранее: теория из утверждений и банк заданий на каждый язык (R-0075, R-0079, T-0159, T-0160, T-0173).

Один запрос к модели на понятие, когда меняется набор его принятых утверждений (хэш набора). Модель только
раскладывает утверждения по разделам и сочиняет задания; у каждого раздела и задания — номера утверждений,
а предложение без ссылки на утверждение отбрасывается: новых фактов вне утверждений модель добавить не может.
Результат хранится в узле: `content.i18n[язык]` — разделы на языке, `content.claims_hash`, версия растёт; задания —
в `learning_item` с ключами ответа. При обучении модель не вызывается.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from core.models import Job
from modules.knowledge import funnel, langs
from modules.knowledge.models import (
    Claim,
    ClaimSource,
    Concept,
    LearningItem,
    SourceDocument,
    SourceFragment,
)

log = logging.getLogger("praxis.compile")

JOB_TYPE = "compile_domain"
ITEM_KINDS = ("card", "mcq", "cloze")
MAX_CLAIMS = 60


def schema_for(languages: list[str]) -> dict[str, Any]:
    """Схема ответа с текстами на каждом поддерживаемом языке."""

    def per_lang(props: dict[str, Any], required: list[str]) -> dict[str, Any]:
        one = {"type": "object", "properties": props, "required": required}
        return {
            "type": "object",
            "properties": {lang: one for lang in languages},
            "required": languages,
        }

    section_text = per_lang(
        {
            "heading": {"type": "string", "description": "короткий заголовок, до 60 знаков"},
            "body": {
                "type": "string",
                "description": "объяснение простыми словами, 2-5 предложений",
            },
            "examples": {"type": "array", "items": {"type": "string"}},
            "counter_examples": {"type": "array", "items": {"type": "string"}},
        },
        ["heading", "body"],
    )
    card = per_lang({"front": {"type": "string"}, "back": {"type": "string"}}, ["front", "back"])
    mcq = per_lang(
        {
            "question": {"type": "string"},
            "options": {
                "type": "array",
                "items": {"type": "string"},
                "description": "ровно 4 варианта",
            },
        },
        ["question", "options"],
    )
    cloze = per_lang(
        {
            "text": {"type": "string", "description": "предложение с пропуском ____"},
            "answer": {"type": "string"},
        },
        ["text", "answer"],
    )
    claim_refs = {
        "type": "array",
        "items": {"type": "integer"},
        "description": "номера утверждений",
    }
    return {
        "type": "object",
        "properties": {
            "summary": per_lang(
                {"text": {"type": "string", "description": "1-2 предложения"}}, ["text"]
            ),
            "sections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"claims": claim_refs, "i18n": section_text},
                    "required": ["claims", "i18n"],
                },
            },
            "cards": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"claims": claim_refs, "i18n": card},
                    "required": ["claims", "i18n"],
                },
            },
            "tests": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "claims": claim_refs,
                        "answer": {
                            "type": "integer",
                            "description": "номер верного варианта с нуля",
                        },
                        "i18n": mcq,
                    },
                    "required": ["claims", "answer", "i18n"],
                },
            },
            "clozes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"claims": claim_refs, "i18n": cloze},
                    "required": ["claims", "i18n"],
                },
            },
        },
        "required": ["summary", "sections", "cards", "tests", "clozes"],
    }


def claims_hash(claims: list[Claim], languages: list[str]) -> str:
    h = hashlib.sha1()
    h.update(",".join(languages).encode())
    for c in sorted(claims, key=lambda c: str(c.id)):
        h.update(str(c.id).encode())
        h.update(c.text.encode())
    return h.hexdigest()


def live_claims(session: Session, concept: Concept) -> list[Claim]:
    """Принятые утверждения, у которых нет открытой проблемы и которые не держатся только на источниках уровня C."""
    rows = (
        session.query(Claim)
        .filter_by(concept_id=concept.id, status="accepted")
        .order_by(Claim.created_at)
        .all()
    )
    return [c for c in rows if not funnel.sole_support_is_weak(session, c.id)][:MAX_CLAIMS]


def _prompt(concept: Concept, claims: list[Claim], languages: list[str]) -> str:
    listing = "\n".join(
        f"{i}. [{c.kind}] {c.text_i18n.get('en') or c.text}" for i, c in enumerate(claims, start=1)
    )
    names = ", ".join(f"{code} ({langs.language_name(code)})" for code in languages)
    level = {"basic": "начальный", "middle": "средний", "advanced": "продвинутый"}.get(
        concept.level or "", "средний"
    )
    return (
        f"Понятие «{concept.title}» из области «{concept.domain}», уровень: {level}.\n"
        "Ниже пронумерованные утверждения о нём (английский канонический текст). Собери учебный узел.\n"
        "Правила:\n"
        "- используй ТОЛЬКО эти утверждения; ничего не добавляй от себя, не выдумывай цифры и примеры;\n"
        "- sections: 3–6 разделов (определение, зачем нужно, как устроено, пример, типичные ошибки — "
        "по тому, что есть); у каждого в claims — номера утверждений, на которых он стоит;\n"
        "- cards: 3–6 карточек «вопрос — ответ»; tests: 3–5 вопросов с 4 вариантами, неверные варианты — "
        "правдоподобные ошибки (берутся из типичных ошибок, если они есть); clozes: 2–4 предложения с "
        "пропуском ____ и ответом; у каждого задания в claims — номера утверждений;\n"
        f"- тексты на каждом языке: {names}; смысл на языках одинаковый, ответ в tests — один и тот же "
        "номер варианта на всех языках, порядок вариантов одинаковый.\n\n"
        f"Утверждения:\n{listing}"
    )


@dataclass
class CompileReport:
    concepts: int = 0
    skipped: int = 0
    calls: int = 0
    sections: int = 0
    items: int = 0
    dropped: int = 0


def _valid_refs(raw: Any, n: int) -> list[int]:
    return [i for i in (raw or []) if isinstance(i, int) and 1 <= i <= n]


def _references(session: Session, claims: list[Claim]) -> list[dict[str, Any]]:
    """Источники понятия для теории: название, страница и фрагменты (без текста: его отдаёт API с учётом лицензии)."""
    ids = [c.id for c in claims]
    if not ids:
        return []
    rows = (
        session.query(SourceDocument, SourceFragment)
        .join(SourceFragment, SourceFragment.document_id == SourceDocument.id)
        .join(ClaimSource, ClaimSource.fragment_id == SourceFragment.id)
        .filter(ClaimSource.claim_id.in_(ids))
        .all()
    )
    by_doc: dict[uuid.UUID, dict[str, Any]] = {}
    for doc, frag in rows:
        ref = by_doc.setdefault(
            doc.id, {"title": doc.title, "url": doc.origin_url, "fragment_ids": []}
        )
        if str(frag.id) not in ref["fragment_ids"]:
            ref["fragment_ids"].append(str(frag.id))
    return list(by_doc.values())


def compile_concept(session: Session, concept: Concept, gateway, report: CompileReport) -> bool:
    languages = langs.supported()
    claims = live_claims(session, concept)
    if not claims:
        report.skipped += 1
        return False
    digest = claims_hash(claims, languages)
    content = dict(concept.content or {})
    if content.get("claims_hash") == digest:
        report.skipped += 1
        return False

    report.calls += 1
    raw = gateway.structured(
        "submit_node",
        "Вернуть теорию и задания узла по утверждениям.",
        schema_for(languages),
        _prompt(concept, claims, languages),
    )
    raw = raw if isinstance(raw, dict) else {}
    n = len(claims)

    # Разделы: без ссылки на утверждение раздел отбрасывается.
    sections_by_lang: dict[str, list[dict[str, Any]]] = {lang: [] for lang in languages}
    for sec in raw.get("sections") or []:
        refs = _valid_refs(sec.get("claims"), n)
        i18n = sec.get("i18n") or {}
        if not refs or not all(isinstance(i18n.get(lang), dict) for lang in languages):
            report.dropped += 1
            continue
        report.sections += 1
        for lang in languages:
            t = i18n[lang]
            sections_by_lang[lang].append(
                {
                    "heading": str(t.get("heading") or concept.title)[:80],
                    "body": str(t.get("body") or ""),
                    "examples": [str(x) for x in t.get("examples") or []][:3],
                    "counter_examples": [str(x) for x in t.get("counter_examples") or []][:3],
                    "claims": [str(claims[i - 1].id) for i in refs],
                }
            )
    if not sections_by_lang[languages[0]]:
        return False  # нечего собирать: узел остаётся прежним, повторим при следующем запуске

    summary = (raw.get("summary") or {}) if isinstance(raw.get("summary"), dict) else {}
    i18n_content: dict[str, Any] = {}
    for lang in languages:
        text = str((summary.get(lang) or {}).get("text") or "")
        i18n_content[lang] = {"summary": text, "sections": sections_by_lang[lang]}

    default = "ru" if "ru" in languages else languages[0]
    concept.content = {
        "summary": i18n_content[default]["summary"] or content.get("summary", ""),
        "sections": sections_by_lang[default],
        "references": _references(session, claims),
        "i18n": i18n_content,
        "claims_hash": digest,
    }
    concept.version = (concept.version or 1) + 1  # старые задания обесцениваются (кэш по версии)
    session.flush()

    # Банк заданий: старые строки узла заменяются.
    session.query(LearningItem).filter_by(concept_id=concept.id).delete(synchronize_session=False)
    for kind, key in (("card", "cards"), ("mcq", "tests"), ("cloze", "clozes")):
        for item in raw.get(key) or []:
            refs = _valid_refs(item.get("claims"), n)
            i18n = item.get("i18n") or {}
            if not refs or not all(isinstance(i18n.get(lang), dict) for lang in languages):
                report.dropped += 1
                continue
            claim_ids = [str(claims[i - 1].id) for i in refs]
            if kind == "mcq":
                answer = item.get("answer")
                options_ok = all(
                    isinstance(i18n[lang].get("options"), list) and len(i18n[lang]["options"]) >= 2
                    for lang in languages
                )
                if not isinstance(answer, int) or not options_ok:
                    report.dropped += 1
                    continue
                if any(answer >= len(i18n[lang]["options"]) for lang in languages):
                    report.dropped += 1
                    continue
            for lang in languages:
                t = i18n[lang]
                if kind == "card":
                    payload, key_ = {"front": t.get("front", ""), "back": t.get("back", "")}, {}
                elif kind == "mcq":
                    payload = {"question": t.get("question", ""), "options": t["options"]}
                    key_ = {"index": item["answer"]}
                else:
                    payload, key_ = {"text": t.get("text", "")}, {"answer": t.get("answer", "")}
                session.add(
                    LearningItem(
                        concept_id=concept.id,
                        concept_version=concept.version,
                        lang=lang,
                        kind=kind,
                        payload=payload,
                        answer_key=key_,
                        claim_ids=claim_ids,
                    )
                )
                report.items += 1
    session.flush()
    report.concepts += 1
    return True


def compile_domain(session: Session, domain: str, gateway) -> CompileReport:
    report = CompileReport()
    concepts = (
        session.query(Concept)
        .filter(Concept.domain == domain, Concept.status != "rejected")
        .order_by(Concept.title)
        .all()
    )
    for concept in concepts:
        try:
            compile_concept(session, concept, gateway, report)
        except Exception:  # noqa: BLE001 — один узел не должен ронять область: повтор соберёт его
            log.warning("Сборка узла %s не удалась", concept.title, exc_info=True)
    return report


def enqueue(session: Session, domain: str, user_id: uuid.UUID) -> Job:
    job = Job(user_id=user_id, type=JOB_TYPE, status="pending", input_ref={"domain": domain})
    session.add(job)
    session.flush()
    return job


def compile_job(session: Session, job: Job, gateway) -> dict[str, Any]:
    domain = str((job.input_ref or {}).get("domain") or "").strip()
    if not domain:
        raise ValueError("Не указана область")
    r = compile_domain(session, domain, gateway)
    return {
        "domain": domain,
        "concepts": r.concepts,
        "skipped": r.skipped,
        "calls": r.calls,
        "sections": r.sections,
        "items": r.items,
        "dropped": r.dropped,
    }
