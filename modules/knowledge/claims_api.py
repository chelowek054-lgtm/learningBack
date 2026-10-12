"""API утверждений: источники для учащегося, пул проблем и жалобы «здесь ошибка» (R-0072, R-0074, R-0077).

Учащийся видит у утверждения название источника, лицензию, место и ссылку на оригинал; текст цитаты — только
если лицензия разрешает воспроизведение. Пул проблем и решения — администратору.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func

from core.deps import CurrentSuperuser, CurrentUser, SessionDep
from modules.knowledge import freshness, funnel, langs, reconcile
from modules.knowledge.models import (
    Claim,
    ClaimSource,
    Concept,
    ReviewLog,
    SourceDocument,
    SourceFragment,
)

router = APIRouter(tags=["claims"])


class ResolveIn(BaseModel):
    resolution: str
    note: str | None = Field(default=None, max_length=500)


class ReportIn(BaseModel):
    note: str | None = Field(default=None, max_length=500)


def _uuid(raw: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(raw)
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{what} не найден") from e


def _anchor(doc: SourceDocument, page: int | None) -> str | None:
    """Адрес оригинала с якорем на страницу; без открытого адреса ссылки нет."""
    if not doc.origin_url:
        return None
    if page and doc.origin_url.lower().split("?")[0].endswith(".pdf"):
        return f"{doc.origin_url}#page={page}"
    return doc.origin_url


@router.get("/nodes/{concept_id}/claims")
def node_claims(
    concept_id: str,
    _: CurrentUser,
    session: SessionDep,
    lang: str = Query("ru", max_length=8),
) -> list[dict]:
    """Утверждения узла с источниками для учащегося: принятые, без открытых проблем."""
    cid = _uuid(concept_id, "Понятие")
    if session.get(Concept, cid) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Понятие не найдено")
    claims = (
        session.query(Claim)
        .filter_by(concept_id=cid, status="accepted")
        .order_by(Claim.created_at)
        .all()
    )
    out = []
    for c in claims:
        rows = (
            session.query(ClaimSource, SourceFragment, SourceDocument)
            .join(SourceFragment, SourceFragment.id == ClaimSource.fragment_id)
            .join(SourceDocument, SourceDocument.id == SourceFragment.document_id)
            .filter(ClaimSource.claim_id == c.id, SourceDocument.quarantined.is_(False))
            .all()
        )
        sources = []
        for cs, f, d in rows:
            shows_text, _why = funnel.license_ok(d.license)
            sources.append(
                {
                    "title": d.title,
                    "license": d.license,
                    "lang": d.lang,
                    "tier": d.trust_tier,
                    "page": f.page,
                    "heading": f.heading,
                    "url": _anchor(d, f.page),
                    # Оригинал недоступен: ссылка помечена, сохранённый фрагмент и атрибуция остаются.
                    "linkDead": bool((d.meta or {}).get("linkDead")),
                    # Цитата показывается с атрибуцией только при разрешающей лицензии.
                    "quote": cs.quote if shows_text else None,
                }
            )
        out.append(
            {
                "id": str(c.id),
                "kind": c.kind,
                "text": (c.text_i18n or {}).get(lang) or c.text,
                "lang": lang if (c.text_i18n or {}).get(lang) else langs.CANONICAL,
                "asOf": c.as_of.isoformat() if c.as_of else None,
                "reviewAfter": c.review_after.isoformat() if c.review_after else None,
                "applicability": c.applicability,
                "sources": sources,
            }
        )
    return out


@router.post("/claims/{claim_id}/report", status_code=status.HTTP_201_CREATED)
def report_error(claim_id: str, body: ReportIn, user: CurrentUser, session: SessionDep) -> dict:
    """«Здесь ошибка»: жалоба привязана к утверждению, несколько жалоб поднимают его в очереди."""
    cid = _uuid(claim_id, "Утверждение")
    if session.get(Claim, cid) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Утверждение не найдено")
    session.add(
        ReviewLog(
            target_type="claim",
            target_id=cid,
            reviewer_id=user.id,
            action="report",
            note=(body.note or "")[:500],
        )
    )
    session.commit()
    return {"ok": True}


@router.get("/claims/reports")
def list_reports(_: CurrentSuperuser, session: SessionDep) -> list[dict]:
    """Утверждения с жалобами: больше жалоб — выше."""
    rows = (
        session.query(ReviewLog.target_id, func.count().label("n"))
        .filter(ReviewLog.target_type == "claim", ReviewLog.action == "report")
        .group_by(ReviewLog.target_id)
        .order_by(func.count().desc())
        .limit(200)
        .all()
    )
    out = []
    for target_id, n in rows:
        claim = session.get(Claim, target_id)
        if claim is None:
            continue
        concept = session.get(Concept, claim.concept_id)
        notes = [
            r[0]
            for r in session.query(ReviewLog.note)
            .filter_by(target_type="claim", target_id=target_id, action="report")
            .limit(5)
            .all()
            if r[0]
        ]
        out.append(
            {
                "claimId": str(target_id),
                "text": claim.text,
                "concept": concept.title if concept else None,
                "reports": n,
                "notes": notes,
            }
        )
    return out


@router.get("/claims/problems")
def list_problems(
    _: CurrentSuperuser,
    session: SessionDep,
    domain: str | None = Query(None, max_length=200),
    state: str = Query("open", pattern="^(open|resolved)$"),
) -> list[dict]:
    return reconcile.pool(session, domain, state)


@router.post("/claims/problems/{problem_id}/resolve")
def resolve_problem(
    problem_id: str, body: ResolveIn, admin: CurrentSuperuser, session: SessionDep
) -> dict:
    """Решение по проблеме: a, b, both, disputed, outdated. Узел при следующей сборке пересоберётся."""
    pid = _uuid(problem_id, "Проблема")
    try:
        cf = reconcile.resolve(session, pid, body.resolution, admin.id, body.note or "")
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)) from e
    except LookupError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    session.commit()
    return {"id": str(cf.id), "status": cf.status, "resolution": cf.resolution}


@router.get("/nodes/{concept_id}/items")
def node_items(
    concept_id: str,
    _: CurrentUser,
    session: SessionDep,
    lang: str = Query("ru", max_length=8),
) -> list[dict]:
    """Готовые задания узла с ключами ответа: проверка на устройстве, без запросов к модели (R-0075)."""
    from modules.knowledge.models import LearningItem

    cid = _uuid(concept_id, "Понятие")
    concept = session.get(Concept, cid)
    if concept is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Понятие не найдено")
    rows = (
        session.query(LearningItem)
        .filter_by(concept_id=cid, lang=lang, concept_version=concept.version)
        .order_by(LearningItem.kind, LearningItem.created_at)
        .all()
    )
    return [
        {
            "id": str(r.id),
            "kind": r.kind,
            "payload": r.payload,
            "answerKey": r.answer_key,
            "claimIds": r.claim_ids,
        }
        for r in rows
    ]


@router.post("/claims/rebuild", status_code=status.HTTP_202_ACCEPTED)
def rebuild_domain(
    admin: CurrentSuperuser,
    session: SessionDep,
    domain: str = Query(..., min_length=1, max_length=200),
) -> dict:
    """Поставить сверку утверждений и сборку узлов области в очередь (после решения проблем или правок)."""
    job = reconcile.enqueue(session, domain, admin.id)
    session.commit()
    return {"jobId": str(job.id), "domain": domain}


@router.post("/claims/refresh", status_code=status.HTTP_202_ACCEPTED)
def refresh_stale(admin: CurrentSuperuser, session: SessionDep) -> dict:
    """Найти просроченные утверждения и поставить поиск свежих источников по их областям (T-0163)."""
    stale = freshness.stale_by_domain(session)
    job = freshness.enqueue_refresh(session, admin.id)
    session.commit()
    return {"jobId": str(job.id), "staleDomains": {d: len(t) for d, t in stale.items()}}


@router.post("/sources/check-links", status_code=status.HTTP_202_ACCEPTED)
def check_links(admin: CurrentSuperuser, session: SessionDep) -> dict:
    """Проверить адреса источников; недоступные помечаются, фрагменты остаются (T-0164)."""
    job = freshness.enqueue_links(session, admin.id)
    session.commit()
    return {"jobId": str(job.id)}


class RevokeIn(BaseModel):
    reason: str = Field(default="", max_length=300)


@router.post("/sources/{doc_id}/revoke")
def revoke_source(
    doc_id: str, body: RevokeIn, admin: CurrentSuperuser, session: SessionDep
) -> dict:
    """Отозвать источник: удалить его и выведенное только из него; изучаемое отклоняется (T-0167)."""
    try:
        out = freshness.revoke_source(session, _uuid(doc_id, "Источник"), body.reason)
    except LookupError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(e)) from e
    session.commit()
    return out
