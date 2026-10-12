"""Воронка отсева источников и уровень доверия (R-0072, R-0078, T-0152, T-0168, T-0169, T-0170).

Дешёвые проверки первыми, модель последней: лицензия → язык → объём и качество текста → дубли →
близость к области → одна проба моделью по выборке фрагментов. Отказ на любом шаге пишется в
`trust_reasons` документа с причиной и ставит `quarantined`: в извлечение и сборку узла такой источник
не идёт, пока администратор его не посмотрит. Запросы к модели тратятся только на прошедших проверки.
"""

from __future__ import annotations

import logging
import re
import zlib
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from core.config import settings
from modules.knowledge import langs
from modules.knowledge.models import (
    ClaimSource,
    Domain,
    SourceDocument,
    SourceFragment,
)

log = logging.getLogger("praxis.funnel")

TIERS = ("A", "B", "C")

PROBE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "is_learning_text": {
            "type": "boolean",
            "description": "учебный или справочный текст по предмету, а не реклама, не новость и не художественный текст",
        },
        "tier": {
            "type": "string",
            "enum": list(TIERS),
            "description": "A — выверенный учебник или документация; B — хороший, но с пробелами; C — сомнительный",
        },
        "has_errors": {"type": "boolean", "description": "есть явные фактические ошибки"},
        "reasons": {"type": "array", "items": {"type": "string"}, "description": "1-3 причины"},
    },
    "required": ["is_learning_text", "tier", "reasons"],
}


@dataclass
class Screening:
    ok: bool = True
    tier: str | None = None
    reasons: list[str] = field(default_factory=list)
    step: str = ""

    def reject(self, step: str, reason: str) -> Screening:
        self.ok, self.step = False, step
        self.reasons.append(reason)
        return self


# ---- 1. лицензия ----


def allowed_licenses() -> set[str]:
    return {x.strip().lower() for x in settings.source_licenses_allowed.split(",") if x.strip()}


def license_ok(license_: str | None) -> tuple[bool, str]:
    """Лицензия из белого списка (или отметка о правах на материал); без лицензии — отказ."""
    if not settings.sources_require_license:
        return True, "проверка лицензии выключена"
    value = (license_ or "").strip().lower()
    if not value:
        return False, "лицензия не указана"
    if value in allowed_licenses():
        return True, f"лицензия {license_}"
    return False, f"лицензия «{license_}» не входит в разрешённые"


# ---- 2. качество и объём текста ----

_NOISE = re.compile(r"[^\w\s.,;:!?()\[\]{}\"'«»—–\-+*/=<>%$°^&|\\∑∫√≤≥≠≈∈∞]", re.UNICODE)


def text_quality(fragments: list[SourceFragment]) -> tuple[bool, str]:
    total = sum(len(f.text) for f in fragments)
    if total < settings.funnel_min_chars:
        return False, f"слишком мало текста: {total} знаков"
    sample = "".join(f.text for f in fragments[:200])[:60000]
    if sample:
        noise = len(_NOISE.findall(sample)) / len(sample)
        if noise > settings.funnel_max_noise:
            return (
                False,
                f"много мусорных символов ({noise:.0%}): скорее всего плохое распознавание",
            )
    avg = total / max(1, len(fragments))
    if avg < settings.funnel_min_avg_fragment:
        return False, f"фрагменты слишком короткие в среднем ({int(avg)} знаков)"
    return True, "текст читаем"


# ---- 3. дубли ----


SHINGLE = 6  # слов в одном «отпечатке»
SHINGLE_SAMPLE = 4  # берём каждый четвёртый отпечаток: хватает для сравнения и держит память малой


def _shingles(fragments: list[SourceFragment]) -> set[int]:
    """Выборка отпечатков текста: пара документов с мелкими правками (новое издание) делит большую их часть."""
    out: set[int] = set()
    for f in fragments[:600]:
        words = re.sub(r"\s+", " ", f.text.lower()).split()
        for i in range(0, max(0, len(words) - SHINGLE + 1)):
            h = zlib.crc32(" ".join(words[i : i + SHINGLE]).encode())
            if h % SHINGLE_SAMPLE == 0:
                out.add(h)
    return out


def duplicate_of(
    session: Session, doc: SourceDocument, fragments: list[SourceFragment]
) -> str | None:
    """Название документа, с которым этот делит не менее `funnel_dup_share` текста: точный дубль или почти-дубль."""
    mine = _shingles(fragments)
    if len(mine) < 20:
        return None
    others = (
        session.query(SourceDocument)
        .filter(SourceDocument.id != doc.id, SourceDocument.quarantined.is_(False))
        .all()
    )
    for other in others:
        frags = session.query(SourceFragment).filter_by(document_id=other.id).all()
        theirs = _shingles(frags)
        if not theirs:
            continue
        if len(mine & theirs) / len(mine) >= settings.funnel_dup_share:
            return other.title
    return None


# ---- 4. близость к области ----


def topic_ok(
    session: Session, doc: SourceDocument, fragments: list[SourceFragment]
) -> tuple[bool, str]:
    """Заголовки документа близки по смыслу к области; без настоящих эмбеддингов шаг пропускается."""
    from core.embeddings import get_embedder

    embedder = get_embedder()
    if embedder.model == "hash-ngram" or not doc.domain:
        return True, "близость к области не проверялась"
    headings = list(dict.fromkeys(f.heading for f in fragments if f.heading))[:40]
    sample = ", ".join(headings) if headings else " ".join(f.text[:200] for f in fragments[:5])
    domain = session.get(Domain, doc.domain)
    title = domain.title if domain is not None else doc.domain
    vec_a, vec_b = embedder.embed([f"{title}", sample[:1500]])
    sim = sum(x * y for x, y in zip(vec_a, vec_b, strict=True))
    if sim < settings.funnel_topic_min:
        return False, f"оглавление далеко от области «{title}» (близость {sim:.2f})"
    return True, f"тема близка к области (близость {sim:.2f})"


# ---- 5. проба моделью ----


def probe(gateway, doc: SourceDocument, fragments: list[SourceFragment]) -> dict[str, Any]:
    n = settings.funnel_probe_fragments
    if len(fragments) <= n:
        picked = fragments
    else:
        step = len(fragments) / n
        picked = [fragments[int(i * step)] for i in range(n)]
    body = "\n\n".join(f"[{i}] {f.text[:900]}" for i, f in enumerate(picked, start=1))
    prompt = (
        f"Источник: «{doc.title}», область «{doc.domain or '?'}». Ниже выборка фрагментов из разных мест.\n"
        "Оцени, годится ли это как источник знаний для учебного графа: учебный ли это текст, нет ли явных "
        "фактических ошибок, выверенный ли он. Сомневаешься — ставь C.\n\n" + body
    )
    raw = gateway.structured("rate_source", "Оценить источник знаний.", PROBE_SCHEMA, prompt)
    raw = raw if isinstance(raw, dict) else {}
    tier = raw.get("tier") if raw.get("tier") in TIERS else "C"
    return {
        "is_learning_text": bool(raw.get("is_learning_text", False)),
        "tier": tier,
        "has_errors": bool(raw.get("has_errors", False)),
        "reasons": [str(x)[:200] for x in (raw.get("reasons") or [])][:3],
    }


# ---- вся воронка ----


def screen(
    session: Session, doc: SourceDocument, fragments: list[SourceFragment], gateway
) -> Screening:
    """Прогнать документ через воронку; результат пишется в документ. Уже оценённый документ не трогается."""
    if not settings.funnel_enabled:
        return Screening(ok=True, tier=doc.trust_tier, reasons=["воронка выключена"])
    if doc.trust_tier and not doc.quarantined:
        return Screening(ok=True, tier=doc.trust_tier, reasons=list(doc.trust_reasons or []))
    result = Screening()

    ok, why = license_ok(doc.license)
    if not ok:
        return _finish(doc, result.reject("license", why))
    result.reasons.append(why)

    lang = langs.detect_lang(" ".join(f.text for f in fragments[:60]))
    doc.lang = lang
    if lang is None or lang not in langs.supported():
        return _finish(
            doc, result.reject("language", "язык текста не опознан или не поддерживается")
        )
    result.reasons.append(f"язык: {lang}")

    ok, why = text_quality(fragments)
    if not ok:
        return _finish(doc, result.reject("quality", why))

    dup = duplicate_of(session, doc, fragments)
    if dup:
        return _finish(doc, result.reject("duplicate", f"совпадает с уже принятым «{dup}»"))

    ok, why = topic_ok(session, doc, fragments)
    if not ok:
        return _finish(doc, result.reject("topic", why))
    result.reasons.append(why)

    verdict = probe(gateway, doc, fragments)
    result.tier = verdict["tier"]
    result.reasons += verdict["reasons"]
    if not verdict["is_learning_text"]:
        return _finish(doc, result.reject("probe", "модель: это не учебный текст"))
    if verdict["has_errors"] and verdict["tier"] != "A":
        result.tier = "C"
        result.reasons.append("модель нашла явные ошибки")
    return _finish(doc, result)


def _finish(doc: SourceDocument, result: Screening) -> Screening:
    doc.trust_tier = result.tier if result.ok else (result.tier or "C")
    doc.trust_reasons = result.reasons
    doc.quarantined = not result.ok
    return result


# ---- метрики после извлечения (T-0170) ----


def after_extraction(doc: SourceDocument, concepts: int, dropped: int) -> None:
    """Много отброшенных (выдуманные цитаты) — понижение уровня или карантин."""
    seen = concepts + dropped
    if seen < 10:
        return
    share = dropped / seen
    if share >= settings.funnel_quarantine_dropped:
        doc.quarantined = True
        doc.trust_reasons = [
            *(doc.trust_reasons or []),
            f"отброшено {share:.0%} извлечённого: цитаты не нашлись в тексте",
        ]
    elif share >= settings.funnel_demote_dropped and doc.trust_tier != "C":
        doc.trust_tier = "C"
        doc.trust_reasons = [
            *(doc.trust_reasons or []),
            f"отброшено {share:.0%} извлечённого: уровень понижен до C",
        ]


def sole_support_is_weak(session: Session, claim_id) -> bool:
    """Утверждение опирается только на источники уровня C: в теорию в одиночку идти не может (R-0078)."""
    rows = (
        session.query(SourceDocument.trust_tier, SourceDocument.quarantined)
        .join(SourceFragment, SourceFragment.document_id == SourceDocument.id)
        .join(ClaimSource, ClaimSource.fragment_id == SourceFragment.id)
        .filter(ClaimSource.claim_id == claim_id)
        .all()
    )
    # Источник в карантине опорой не считается: как будто он уровня C.
    tiers = {"C" if r[1] else r[0] for r in rows}
    # Без оценки (воронка не гонялась) источник слабым не считается: слабый — только явно уровня C.
    return bool(tiers) and tiers == {"C"}


def after_reconcile(session: Session, domain: str) -> list[str]:
    """Источник, у которого слишком много утверждений оказались спорными, уходит в карантин (T-0170)."""
    from sqlalchemy import func

    from modules.knowledge.models import Claim, Concept

    rows = (
        session.query(
            SourceDocument,
            func.count(func.distinct(Claim.id)),
            func.count(func.distinct(Claim.id)).filter(Claim.status == "disputed"),
        )
        .join(SourceFragment, SourceFragment.document_id == SourceDocument.id)
        .join(ClaimSource, ClaimSource.fragment_id == SourceFragment.id)
        .join(Claim, Claim.id == ClaimSource.claim_id)
        .join(Concept, Concept.id == Claim.concept_id)
        .filter(Concept.domain == domain, SourceDocument.quarantined.is_(False))
        .group_by(SourceDocument.id)
        .all()
    )
    hit: list[str] = []
    for doc, total, disputed in rows:
        if total >= settings.funnel_min_claims_for_conflicts and disputed / total >= (
            settings.funnel_quarantine_conflicts
        ):
            doc.quarantined = True
            doc.trust_reasons = [
                *(doc.trust_reasons or []),
                f"много противоречий: {disputed} из {total} утверждений спорные",
            ]
            hit.append(str(doc.id))
    session.flush()
    return hit
