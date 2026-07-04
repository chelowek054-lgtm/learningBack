"""Очередь отложенных AI-задач (WS2). Обычно обрабатываются на /sync/push;
здесь — список задач пользователя и ручной триггер обработки (для тестов/отладки)."""

from fastapi import APIRouter

from core.ai_gateway import get_ai_gateway
from core.deps import CurrentUser, SessionDep
from core.jobs import process_job
from core.models import Job
from core.schemas import JobIO

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=list[JobIO])
def list_jobs(user: CurrentUser, session: SessionDep) -> list[Job]:
    return session.query(Job).filter(Job.user_id == user.id).all()


@router.post("/process", response_model=list[JobIO])
def process_pending(user: CurrentUser, session: SessionDep) -> list[Job]:
    gateway = get_ai_gateway()
    pending = session.query(Job).filter(Job.user_id == user.id, Job.status == "pending").all()
    for job in pending:
        process_job(session, job, gateway)
    session.commit()
    return session.query(Job).filter(Job.user_id == user.id).all()
