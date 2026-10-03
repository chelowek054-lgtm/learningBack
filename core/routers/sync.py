"""Синхронизация клиента (двухфазная push/pull). WS2. Контракт: 02-logical §5.2.

Push: клиент шлёт локальные изменения (activities/responses/srs/jobs) → сервер
принимает (user_id форсится из токена), обрабатывает pending-jobs, отдаёт ack.
Pull: сервер отдаёт свои изменения (grade'ы, сгенерированные карточки, активности).
LWW по id; pull принимает `since` (курсор прошлого ответа) и отдаёт только новое.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from core.ai_gateway import get_ai_gateway
from core.config import settings
from core.deps import CurrentUser, SessionDep
from core.jobs import due_jobs, process_job
from core.srs import incoming_wins
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


def _foreign(obj, user) -> bool:
    """Запись с таким id уже принадлежит другому пользователю.

    id генерирует клиент, поэтому чужой UUID нельзя принимать за «обновление
    своей записи»: иначе push перехватывает чужие данные (SPEC-03, AC-03.10).
    """
    return obj is not None and obj.user_id != user.id


@router.post("/push", response_model=SyncPushOut)
def push(body: SyncPushIn, user: CurrentUser, session: SessionDep) -> SyncPushOut:
    ack: list = []

    for a in body.activities:
        obj = session.get(Activity, a.id)
        if _foreign(obj, user):
            continue
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
        if _foreign(obj, user):
            continue
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
        if _foreign(obj, user):
            continue
        if obj is None:
            obj = SrsCard(id=c.id, user_id=user.id)
            session.add(obj)
        obj.user_id = user.id
        # Побеждает карточка с более поздним ревью, а не с более поздней записью на сервер
        # (R-0019): повторение на втором устройстве не откатывается синхронизацией первого.
        # Проигравшую версию подтверждаем — клиенту её повторять незачем, а победившую он
        # получит при следующем pull.
        if obj.fsrs_state is not None and not incoming_wins(c.fsrs_state, obj.fsrs_state):
            ack.append(c.id)
            continue
        obj.module, obj.front, obj.back, obj.source = c.module, c.front, c.back, c.source
        obj.fsrs_state, obj.due_at = c.fsrs_state, c.due_at
        obj.updated_at = c.updated_at or datetime.now(timezone.utc)
        ack.append(c.id)

    # Ставим jobs (идемпотентно по id).
    for j in body.jobs:
        obj = session.get(Job, j.id)
        if _foreign(obj, user):
            continue
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

    # Обрабатываем pending-jobs пользователя синхронно — только в режиме inline. В режиме worker
    # задачи остаются в очереди, их берёт отдельный процесс, и /sync/push не ждёт модель.
    if settings.jobs_mode == "inline":
        gateway = get_ai_gateway()
        for job in due_jobs(session, user.id):
            process_job(session, job, gateway)

    session.commit()
    return SyncPushOut(ack_ids=ack)


# Запас курсора: транзакция, начатая до момента курсора, может закоммититься после
# него, и её строки с меткой раньше курсора не попали бы ни в один pull. Повторная
# отдача безвредна — клиент применяет записи идемпотентно.
CURSOR_SLACK = timedelta(seconds=10)


@router.get("/pull", response_model=SyncPullOut)
def pull(
    user: CurrentUser,
    session: SessionDep,
    since: datetime | None = Query(None, description="курсор из прошлого pull; пусто — всё"),
) -> SyncPullOut:
    """Серверные изменения пользователя: всё или только новее `since`."""
    # Время берём из БД: метки строк ставит она, и курсор должен идти по её часам.
    cursor = session.execute(select(func.clock_timestamp())).scalar_one() - CURSOR_SLACK

    def changed(q, column):
        return q if since is None else q.filter(column >= since)

    activities = changed(
        session.query(Activity).filter(Activity.user_id == user.id), Activity.server_updated_at
    ).all()
    responses = changed(
        session.query(Response).filter(Response.user_id == user.id), Response.server_updated_at
    ).all()
    jobs = changed(
        session.query(Job).filter(Job.user_id == user.id, Job.status.in_(("done", "failed"))),
        Job.server_updated_at,
    ).all()
    cards = changed(
        session.query(SrsCard).filter(SrsCard.user_id == user.id), SrsCard.server_updated_at
    ).all()

    return SyncPullOut(
        user_id=user.id,
        cursor=cursor,
        activities=[ActivityIO.model_validate(a) for a in activities],
        responses=[ResponseIO.model_validate(r) for r in responses],
        jobs=[JobIO.model_validate(j) for j in jobs],
        srs_cards=[SrsCardIO.model_validate(c) for c in cards],
    )
