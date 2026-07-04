"""Синхронизация клиента (двухфазная push/pull). WS2. Контракт: 02-logical §5.2.

Push: клиент шлёт локальные изменения (activities/responses/srs/jobs) → сервер
принимает (user_id форсится из токена), обрабатывает pending-jobs, отдаёт ack.
Pull: сервер отдаёт свои изменения (grade'ы, сгенерированные карточки, активности).
MVP: LWW перезаписью по id; `since`-оптимизация — позже.
"""

from fastapi import APIRouter

from core.ai_gateway import get_ai_gateway
from core.deps import CurrentUser, SessionDep
from core.jobs import process_job
from core.models import Activity, Job, Response, SrsCard
from core.schemas import (
    ActivityIO,
    JobIO,
    ResponseIO,
    SrsCardIO,
    SyncPullOut,
    SyncPushIn,
    SyncPushOut,
)

router = APIRouter(prefix="/sync", tags=["sync"])


@router.post("/push", response_model=SyncPushOut)
def push(body: SyncPushIn, user: CurrentUser, session: SessionDep) -> SyncPushOut:
    ack: list = []

    for a in body.activities:
        obj = session.get(Activity, a.id)
        if obj is None:
            obj = Activity(id=a.id, user_id=user.id)
            session.add(obj)
        obj.user_id = user.id
        obj.module, obj.type, obj.connectivity, obj.payload = (
            a.module,
            a.type,
            a.connectivity,
            a.payload,
        )
        if a.due_at is not None:
            obj.due_at = a.due_at
        ack.append(a.id)

    for r in body.responses:
        obj = session.get(Response, r.id)
        if obj is None:
            obj = Response(id=r.id, user_id=user.id)
            session.add(obj)
        obj.user_id = user.id
        obj.activity_id = r.activity_id
        obj.user_answer = r.user_answer
        obj.local_created_at = r.local_created_at
        if r.grade is not None:
            obj.grade = r.grade
        obj.synced = True
        ack.append(r.id)

    for c in body.srs_cards:
        obj = session.get(SrsCard, c.id)
        if obj is None:
            obj = SrsCard(id=c.id, user_id=user.id)
            session.add(obj)
        obj.user_id = user.id
        obj.module, obj.front, obj.back, obj.source = c.module, c.front, c.back, c.source
        obj.fsrs_state, obj.due_at = c.fsrs_state, c.due_at
        ack.append(c.id)

    # Ставим jobs (идемпотентно по id).
    for j in body.jobs:
        obj = session.get(Job, j.id)
        if obj is None:
            session.add(
                Job(
                    id=j.id,
                    user_id=user.id,
                    type=j.type,
                    status="pending",
                    input_ref=j.input_ref,
                )
            )
        ack.append(j.id)

    session.flush()

    # Обрабатываем pending-jobs пользователя (MVP: синхронно).
    gateway = get_ai_gateway()
    pending = session.query(Job).filter(Job.user_id == user.id, Job.status == "pending").all()
    for job in pending:
        process_job(session, job, gateway)

    session.commit()
    return SyncPushOut(ack_ids=ack)


@router.get("/pull", response_model=SyncPullOut)
def pull(user: CurrentUser, session: SessionDep) -> SyncPullOut:
    activities = session.query(Activity).filter(Activity.user_id == user.id).all()
    responses = session.query(Response).filter(Response.user_id == user.id).all()
    jobs = session.query(Job).filter(Job.user_id == user.id, Job.status == "done").all()
    cards = session.query(SrsCard).filter(SrsCard.user_id == user.id).all()

    return SyncPullOut(
        activities=[ActivityIO.model_validate(a) for a in activities],
        responses=[ResponseIO.model_validate(r) for r in responses],
        jobs=[JobIO.model_validate(j) for j in jobs],
        srs_cards=[SrsCardIO.model_validate(c) for c in cards],
    )
