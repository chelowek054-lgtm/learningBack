"""Разбор документа в понятия со ссылками на фрагмент (T-0077, R-0043).

Документ из хранилища режется на фрагменты (страница, заголовок главы из оглавления PDF), модель
выделяет по окнам фрагментов понятия и связи, и КАЖДОЕ утверждение обязано сослаться на фрагмент
и привести цитату из него. Цитата сверяется с текстом фрагмента: понятие, чью цитату в источнике
не нашли, отбрасывается как выдуманное. Всё принятое получает статус «черновик» (T-0076) и ждёт
проверки человеком.

Один разбор на книгу: прогресс по окнам хранится в `meta` документа, поэтому повтор после сбоя
продолжает с места остановки, а уже разобранный документ не тратит ни токена.
"""

from __future__ import annotations

import io
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from pypdf import PdfReader
from sqlalchemy.orm import Session

from core import materials
from core.config import settings
from core.models import Job
from core.objects import ObjectStore, get_store
from modules.knowledge import provenance
from modules.knowledge.models import (
    Concept,
    ConceptEdge,
    SourceDocument,
    SourceFragment,
)

log = logging.getLogger("praxis.ingest")

JOB_TYPE = "ingest_document"
EDGE_TYPES = (
    "prereq",
    "specializes",
    "part_of",
    "related",
    "contrasts",
    "misconception",
    "example",
)
MAX_QUOTE = 300
MIN_QUOTE = 12
# Уверенность понятий из источника не выше этого: проверка человеком ещё впереди.
MAX_CONFIDENCE = 0.8

EXTRACT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "concepts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "snake_case латиницей"},
                    "title": {"type": "string"},
                    "tier": {"type": "string", "enum": ["core", "derived"]},
                    "summary": {"type": "string", "description": "2-4 предложения своими словами"},
                    "examples": {"type": "array", "items": {"type": "string"}},
                    "misconceptions": {"type": "array", "items": {"type": "string"}},
                    "sources": {
                        "type": "array",
                        "description": "номера фрагментов окна, из которых взято понятие",
                        "items": {"type": "integer"},
                    },
                    "quote": {
                        "type": "string",
                        "description": "дословная цитата из одного из этих фрагментов, 1-2 предложения",
                    },
                    "confidence": {"type": "number"},
                },
                "required": ["key", "title", "summary", "sources", "quote"],
            },
        },
        "edges": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "from": {"type": "string"},
                    "to": {"type": "string"},
                    "type": {"type": "string", "enum": list(EDGE_TYPES)},
                    "sources": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["from", "to", "type", "sources"],
            },
        },
    },
    "required": ["concepts", "edges"],
}


# ---- чтение документа ----


def pdf_headings(data: bytes) -> dict[int, str]:
    """Страница → заголовок главы по закладкам PDF; пусто, если оглавления нет."""
    try:
        reader = PdfReader(io.BytesIO(data))
        outline = reader.outline
    except Exception:  # noqa: BLE001 — оглавление необязательно, текст разберётся и без него
        return {}
    found: dict[int, str] = {}

    def walk(items: list, depth: int = 0) -> None:
        for item in items:
            if isinstance(item, list):
                walk(item, depth + 1)
                continue
            try:
                page = reader.get_destination_page_number(item) + 1
                title = str(item.title).strip()
            except Exception:  # noqa: BLE001
                continue
            if title and page not in found:
                found[page] = title

    walk(outline)
    return found


def heading_for(page: int | None, headings: dict[int, str]) -> str | None:
    """Ближайший заголовок не позже страницы: глава действует до следующей закладки."""
    if page is None or not headings:
        return None
    earlier = [p for p in headings if p <= page]
    return headings[max(earlier)] if earlier else None


def parse_document(
    session: Session, doc: SourceDocument, filename: str, data: bytes
) -> list[SourceFragment]:
    """Разобрать файл в фрагменты и сохранить. Повторный вызов фрагментов не дублирует."""
    existing = session.query(SourceFragment).filter_by(document_id=doc.id).count()
    if existing:
        return (
            session.query(SourceFragment)
            .filter_by(document_id=doc.id)
            .order_by(SourceFragment.ordinal)
            .all()
        )
    extracted = materials.extract(filename, data)
    headings = pdf_headings(data) if extracted.source == "pdf" else {}
    items = [
        {
            "text": f.text,
            "page": f.page,
            "heading": f.heading or heading_for(f.page, headings),
        }
        for f in extracted.fragments
    ]
    frags = provenance.add_fragments(session, doc, items)
    doc.meta = {**(doc.meta or {}), "filename": filename, "pages": extracted.pages}
    session.flush()
    return frags


# ---- окна и промпт ----


def windows(fragments: list[SourceFragment], size: int) -> list[list[SourceFragment]]:
    """Окна подряд идущих фрагментов; граница окна не режет главу, если можно сдвинуть к заголовку."""
    out: list[list[SourceFragment]] = []
    current: list[SourceFragment] = []
    for frag in fragments:
        new_chapter = bool(current) and frag.heading and frag.heading != current[-1].heading
        if current and (len(current) >= size or (new_chapter and len(current) >= size // 2)):
            out.append(current)
            current = []
        current.append(frag)
    if current:
        out.append(current)
    return out


def prompt_for(window: list[SourceFragment], domain: str, doc_title: str) -> str:
    body = "\n\n".join(
        f"[Фрагмент {i}] (стр. {f.page or '?'}"
        + (f", {f.heading}" if f.heading else "")
        + f")\n{f.text}"
        for i, f in enumerate(window, start=1)
    )
    return (
        f"Источник: «{doc_title}», область «{domain}». Ниже фрагменты учебника.\n"
        "Выдели понятия, которые ЯВНО объясняются в этих фрагментах, и связи между ними.\n"
        "Правила:\n"
        "- берёшь только то, что написано во фрагментах; ничего не добавляй от себя;\n"
        "- у каждого понятия: sources — номера фрагментов, из которых оно взято, и quote — "
        "ДОСЛОВНАЯ цитата (1-2 предложения) из одного из них;\n"
        "- summary — 2-4 предложения своими словами по этому тексту; examples и misconceptions — "
        "только если они есть в тексте;\n"
        "- понятие — это идея, которую можно объяснить и проверить, а не заголовок раздела и не термин "
        "без объяснения;\n"
        "- связи: prereq (без A не понять B), specializes, part_of, related, contrasts, misconception, "
        "example; у связи sources — фрагменты, где это видно; в from и to — key понятий этого же ответа;\n"
        "- если во фрагментах нет объяснений понятий, верни пустые списки.\n\n" + body
    )


# ---- проверка ответа модели ----


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[“”«»\"'`]", "", text)).strip().lower()


def quote_found(quote: str, texts: list[str]) -> bool:
    """Цитата есть в одном из фрагментов (без учёта регистра, кавычек и переносов)."""
    q = _norm(quote)
    if len(q) < MIN_QUOTE:
        return False
    q = q[:MAX_QUOTE]
    return any(q in _norm(t) for t in texts)


def _key(raw: Any, title: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", str(raw or "").lower()).strip("_")
    if base:
        return base[:60]
    return "c_" + uuid.uuid5(uuid.NAMESPACE_URL, title.lower()).hex[:10]


@dataclass
class Cleaned:
    concepts: list[dict[str, Any]] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)


def clean_extraction(raw: Any, window: list[SourceFragment]) -> Cleaned:
    """Оставить только обоснованное: источник из окна, цитата найдена, понятия не повторяются."""
    out = Cleaned()
    raw = raw if isinstance(raw, dict) else {}
    seen: dict[str, dict[str, Any]] = {}
    for item in raw.get("concepts") or []:
        title = str(item.get("title") or "").strip()
        if not title:
            out.dropped.append("без названия")
            continue
        idx = [i for i in item.get("sources") or [] if isinstance(i, int) and 1 <= i <= len(window)]
        if not idx:
            out.dropped.append(f"{title}: нет ссылки на фрагмент")
            continue
        cited = [window[i - 1] for i in dict.fromkeys(idx)]
        if not quote_found(str(item.get("quote") or ""), [f.text for f in cited]):
            out.dropped.append(f"{title}: цитата не найдена в источнике")
            continue
        summary = str(item.get("summary") or "").strip()
        if not summary:
            out.dropped.append(f"{title}: нет изложения")
            continue
        key = _key(item.get("key"), title)
        if key in seen:  # повтор внутри окна: источники складываем
            seen[key]["fragments"] += [f for f in cited if f not in seen[key]["fragments"]]
            continue
        confidence = item.get("confidence")
        confidence = float(confidence) if isinstance(confidence, int | float) else 0.5
        concept = {
            "key": key,
            "title": title[:200],
            "tier": item.get("tier") if item.get("tier") in ("core", "derived") else "derived",
            "summary": summary,
            "examples": [str(x) for x in item.get("examples") or [] if str(x).strip()][:4],
            "misconceptions": [str(x) for x in item.get("misconceptions") or [] if str(x).strip()][
                :4
            ],
            "confidence": round(max(0.0, min(MAX_CONFIDENCE, confidence)), 2),
            "fragments": cited,
        }
        seen[key] = concept
        out.concepts.append(concept)

    keys = set(seen)
    for item in raw.get("edges") or []:
        a, b, typ = _key(item.get("from"), ""), _key(item.get("to"), ""), item.get("type")
        idx = [i for i in item.get("sources") or [] if isinstance(i, int) and 1 <= i <= len(window)]
        if a == b or a not in keys or b not in keys or typ not in EDGE_TYPES:
            out.dropped.append(f"связь {a}→{b}: концы не из ответа или тип неизвестен")
        elif not idx:
            out.dropped.append(f"связь {a}→{b}: нет ссылки на фрагмент")
        else:
            out.edges.append(
                {
                    "from": a,
                    "to": b,
                    "type": typ,
                    "fragments": [window[i - 1] for i in dict.fromkeys(idx)],
                }
            )
    return out


# ---- запись в граф ----


def _content(c: dict[str, Any]) -> dict[str, Any]:
    section: dict[str, Any] = {"heading": c["title"], "body": c["summary"]}
    if c["examples"]:
        section["examples"] = c["examples"]
    if c["misconceptions"]:
        section["counter_examples"] = c["misconceptions"]
    return {"summary": c["summary"], "sections": [section], "references": []}


def save(session: Session, domain: str, cleaned: Cleaned) -> tuple[int, int]:
    """Понятия и связи окна в граф как черновики. Вернуть (новых понятий, новых связей)."""
    by_key: dict[str, Concept] = {}
    new_concepts = new_edges = 0
    for c in cleaned.concepts:
        concept = session.query(Concept).filter_by(domain=domain, key=c["key"]).one_or_none()
        if concept is None:
            concept = Concept(
                domain=domain,
                title=c["title"],
                key=c["key"],
                tier=c["tier"],
                content=_content(c),
                bloom_levels=["remember", "understand"],
                difficulty=2,
                source="doc",
                confidence=c["confidence"],
                status=provenance.DRAFT,
            )
            session.add(concept)
            session.flush()
            new_concepts += 1
        by_key[c["key"]] = concept
        provenance.link_concept(session, concept, [f.id for f in c["fragments"]])
    for e in cleaned.edges:
        a, b = by_key[e["from"]], by_key[e["to"]]
        edge = (
            session.query(ConceptEdge)
            .filter_by(from_id=a.id, to_id=b.id, type=e["type"])
            .one_or_none()
        )
        if edge is None:
            edge = ConceptEdge(from_id=a.id, to_id=b.id, type=e["type"], status=provenance.DRAFT)
            session.add(edge)
            session.flush()
            new_edges += 1
        provenance.link_edge(session, edge, [f.id for f in e["fragments"]])
    return new_concepts, new_edges


# ---- разбор целого документа ----


def ingest(
    session: Session,
    doc: SourceDocument,
    gateway,
    *,
    store: ObjectStore | None = None,
    max_windows: int | None = None,
) -> dict[str, Any]:
    """Разобрать документ окнами; продолжает с места остановки, готовый документ не трогает."""
    meta = dict(doc.meta or {})
    state = dict(meta.get("ingest") or {})
    if state.get("status") == "done":
        return {**state, "skipped": True}
    if not doc.domain:
        raise ValueError("У документа не указана область: графу некуда вносить понятия")
    if not doc.object_key:
        raise ValueError("Файл документа не сохранён в хранилище")

    data = (store or get_store()).get(doc.object_key)
    fragments = parse_document(session, doc, meta.get("filename") or doc.title, data)
    limit = max_windows if max_windows is not None else settings.ingest_max_windows
    plan = windows(fragments, settings.ingest_window_fragments)[:limit]
    state.setdefault("windows", len(plan))
    state.setdefault("done", 0)
    state.setdefault("concepts", 0)
    state.setdefault("edges", 0)
    state.setdefault("dropped", 0)
    state["truncated"] = len(windows(fragments, settings.ingest_window_fragments)) > len(plan)

    for number, window in enumerate(plan):
        if number < state["done"]:
            continue
        raw = gateway.structured(
            "submit_concepts",
            "Вернуть понятия и связи, найденные во фрагментах источника.",
            EXTRACT_SCHEMA,
            prompt_for(window, doc.domain, doc.title),
        )
        cleaned = clean_extraction(raw, window)
        c, e = save(session, doc.domain, cleaned)
        state["concepts"] += c
        state["edges"] += e
        state["dropped"] += len(cleaned.dropped)
        state["done"] = number + 1
        doc.meta = {**meta, "ingest": {**state, "status": "running"}}
        session.flush()  # прогресс виден и переживёт сбой следующего окна

    state["status"] = "done"
    doc.meta = {**meta, "ingest": state}
    session.flush()
    return state


def enqueue_ingest(session: Session, doc: SourceDocument, user_id: uuid.UUID) -> Job:
    """Поставить разбор в очередь: идёт в фоне воркером, не на запросе человека."""
    job = Job(
        user_id=user_id,
        type=JOB_TYPE,
        status="pending",
        input_ref={"documentId": str(doc.id)},
    )
    session.add(job)
    session.flush()
    return job


def ingest_job(session: Session, job: Job, gateway) -> dict[str, Any]:
    """Обработчик job: ValueError — навсегда (нет документа), остальное — временный сбой, повтор."""
    raw_id = (job.input_ref or {}).get("documentId")
    try:
        doc = session.get(SourceDocument, uuid.UUID(str(raw_id)))
    except ValueError as e:
        raise ValueError("documentId некорректен") from e
    if doc is None:
        raise ValueError("Документ не найден")
    state = ingest(session, doc, gateway)
    if not state.get("skipped") and state.get("concepts") and doc.domain:
        # Новые понятия могут дублировать уже стоящие: слияние области идёт следующей задачей.
        from modules.knowledge import merge

        merge.enqueue_merge(session, doc.domain, job.user_id)
    return state
