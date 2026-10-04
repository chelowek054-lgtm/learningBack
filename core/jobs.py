"""Обработка отложенных AI-задач (WS2). Мост offline→online: job → скоринг → grade + error-log.

Сбои делятся на два рода (FR-SYNC-06):
  * **постоянные** — задача ссылается на то, чего нет (ответ, рубрика, тип): повтор
    ничего не изменит, поэтому сразу `failed` с причиной;
  * **временные** — недоступен провайдер, оборвалась сеть, модель не вызвала
    инструмент: задача возвращается в `pending` с отсрочкой и пробуется снова,
    пока не исчерпает `job_max_attempts`.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import or_
from sqlalchemy.orm import Session

from core.ai_gateway import AIGateway, get_rubric
from core.config import settings
from core.models import Activity, Job, Response
from core.modules import grade_job_modules, job_handler_for
from core.srs import errors_to_card_partials, insert_cards


def retry_delay(attempts: int) -> timedelta:
    """Экспоненциальная отсрочка: base, 2·base, 4·base… после `attempts` неудач."""
    return timedelta(seconds=settings.job_retry_backoff_seconds * 2 ** max(attempts - 1, 0))


def due_jobs(session: Session, user_id, now: datetime | None = None, force: bool = False):
    """Задачи пользователя, которые пора исполнять. `force` игнорирует отсрочку."""
    now = now or datetime.now(timezone.utc)
    q = session.query(Job).filter(Job.user_id == user_id, Job.status == "pending")
    if not force:
        q = q.filter(or_(Job.retry_after.is_(None), Job.retry_after <= now))
    return q.all()


def process_job(session: Session, job: Job, gateway: AIGateway) -> None:
    """Синхронно обработать один pending-job. Для MVP вызывается на /sync/push."""
    now = datetime.now(timezone.utc)
    job.status = "running"
    job.attempts += 1

    try:
        handler = job_handler_for(job.type)
        if handler is not None:
            # Фоновая работа модуля без рубрики: результат — то, что вернул обработчик.
            job.result = handler(session, job, gateway)
            job.retry_after = None
            job.status = "done"
            return
        card_modules = grade_job_modules()
        if job.type not in card_modules:
            raise ValueError(f"Неизвестный тип job: {job.type}")

        response_id = job.input_ref.get("responseId")
        rubric_id = job.input_ref.get("rubricId")
        response = session.get(Response, response_id) if response_id else None
        if response is None:
            raise ValueError("responseId не найден")

        rubric = get_rubric(session, rubric_id) if rubric_id else None
        if rubric is None:
            raise ValueError(f"Рубрика не найдена: {rubric_id}")

        activity = session.get(Activity, response.activity_id)
        grade = gateway.grade(rubric, activity.payload if activity else {}, response.user_answer)

        # Пометка рубрики об ограничениях оценки (например, «код не запускался»)
        # едет вместе с оценкой: клиент показывает её рядом с баллами.
        caveat = rubric.schema.get("caveat")
        if caveat:
            grade["caveat"] = caveat
        response.grade = grade
        # Ошибки → карточки SRS (error-log).
        partials = errors_to_card_partials(grade.get("errors", []))
        insert_cards(session, response.user_id, card_modules[job.type], partials, now)

        job.result = {"responseId": str(response.id), "cardsCreated": len(partials)}
        job.retry_after = None
        job.status = "done"
    except ValueError as exc:
        # Постоянная ошибка: повтор не поможет.
        job.status = "failed"
        job.retry_after = None
        job.result = {"error": str(exc)}
    except Exception as exc:  # noqa: BLE001 — временный сбой провайдера/сети
        if job.attempts >= settings.job_max_attempts:
            job.status = "failed"
            job.retry_after = None
            job.result = {"error": str(exc), "attempts": job.attempts}
        else:
            job.status = "pending"
            job.retry_after = now + retry_delay(job.attempts)
            job.result = {"error": str(exc), "retrying": True, "attempts": job.attempts}
    finally:
        job.updated_at = now
