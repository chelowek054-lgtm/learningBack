"""Отчёт полноты графа против профиля навыка (T-0099, R-0053).

Профиль — «что нужно знать». Отчёт сверяет его с графом и по каждой области и понятию говорит: есть ли
оно в графе и на чём стоит. Понятие находится по ключу скелета, а если скелет слит с разобранным из
источника (ключ другой) — по близости вектора. Человек видит, чего не хватает и что проверено; администратор
дополнительно видит, что подтверждено источником, а что только «со слов модели» (R-0045: у человека этого нет).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from modules.knowledge import domains, profile_match, provenance
from modules.knowledge.models import Concept, ConceptSource, Domain, SkillProfile

MISSING_LIMIT = 50  # сколько недостающих названий отдавать по области


def _domain_title(session: Session, row: SkillProfile, area: dict[str, Any]) -> str:
    """Область графа для области профиля: найденная при сопоставлении либо построенная под этим названием."""
    for decision in (row.profile.get("match") or {}).get("areas", []):
        if (
            decision.get("key") == area["key"]
            and decision.get("decision") == profile_match.EXISTING
        ):
            found = session.get(Domain, decision.get("domain"))
            if found is not None:
                return found.title
    return row.domain if area["role"] == "goal" else area["title"]


def _sourced(session: Session, concept_ids: list[Any]) -> set[Any]:
    if not concept_ids:
        return set()
    rows = session.query(ConceptSource.concept_id).filter(ConceptSource.concept_id.in_(concept_ids))
    return {r[0] for r in rows}


def report(session: Session, row: SkillProfile, admin: bool = False) -> dict[str, Any]:
    """Полнота по областям и итог; `admin` добавляет разбивку по источникам."""
    profile = row.profile or {}
    areas_out = []
    totals = {"total": 0, "required": 0, "found": 0, "verified": 0, "sourced": 0, "modelOnly": 0}
    for area in profile.get("areas", []):
        title = _domain_title(session, row, area)
        key = domains.normalize(title)
        existing = (
            session.query(Concept)
            .filter(Concept.domain.in_({title, key}), Concept.status != provenance.REJECTED)
            .all()
        )
        by_key = {c.key: c for c in existing if c.key}
        found: dict[str, Concept] = {}
        lost: list[dict[str, Any]] = []
        for concept in area.get("concepts", []):
            hit = by_key.get(concept["key"])
            if hit is not None:
                found[concept["key"]] = hit
            else:
                lost.append(concept)
        if lost and existing:  # слитое с источником понятие: ключ другой, смысл тот же
            domain = domains.resolve(session, title)
            if domain is not None:
                taken = {
                    str(c.id) for c in found.values()
                }  # одно понятие не закрывает два пункта профиля
                for m in profile_match.match_concepts(session, domain, lost):
                    if m["decision"] == profile_match.EXISTING and m["conceptId"] not in taken:
                        hit = next((c for c in existing if str(c.id) == m["conceptId"]), None)
                        if hit is not None:
                            found[m["key"]] = hit
                            taken.add(m["conceptId"])
        sourced = _sourced(session, [c.id for c in found.values()])
        missing = [c for c in area.get("concepts", []) if c["key"] not in found]
        verified = [c for c in found.values() if c.status == provenance.APPROVED]
        model_only = [c for c in found.values() if c.id not in sourced]
        required = [c for c in area.get("concepts", []) if not c.get("optional")]
        required_found = [c for c in required if c["key"] in found]
        item = {
            "key": area["key"],
            "title": area["title"],
            "domain": title,
            "total": len(area.get("concepts", [])),
            "found": len(found),
            "verified": len(verified),
            "missing": [c["title"] for c in missing if not c.get("optional")][:MISSING_LIMIT],
            "optionalMissing": sum(1 for c in missing if c.get("optional")),
            "coverage": round(len(required_found) / len(required), 3) if required else 1.0,
        }
        if admin:
            item["sourced"] = len(sourced)
            item["modelOnly"] = len(model_only)
        areas_out.append(item)
        totals["total"] += item["total"]
        totals["required"] += len(required)
        totals["found"] += len(required_found)
        totals["verified"] += len(verified)
        totals["sourced"] += len(sourced)
        totals["modelOnly"] += len(model_only)
    summary = {
        "total": totals["total"],
        "verified": totals["verified"],
        "missing": sum(len(a["missing"]) for a in areas_out),
        "coverage": round(totals["found"] / totals["required"], 3) if totals["required"] else 1.0,
    }
    if admin:
        summary["sourced"] = totals["sourced"]
        summary["modelOnly"] = totals["modelOnly"]
    return {"skill": profile.get("skill"), "areas": areas_out, "summary": summary}
