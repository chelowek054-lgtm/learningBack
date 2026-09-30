"""Обработка отложенных AI-задач (WS2). Мост offline→online: job → скоринг → grade + error-log."""

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from core.ai_gateway import AIGateway, get_rubric
from core.modules import grade_job_modules
from core.models import Activity, Job, Response
from core.srs import errors_to_card_partials, insert_cards


def process_job(session: Session, job: Job, gateway: AIGateway) -> None:
    """Синхронно обработать один pending-job. Для MVP вызывается на /sync/push."""
    now = datetime.now(timezone.utc)
    job.status = "running"
    job.attempts += 1

    try:
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

        response.grade = grade
        # Ошибки → карточки SRS (error-log).
        partials = errors_to_card_partials(grade.get("errors", []))
        insert_cards(session, response.user_id, card_modules[job.type], partials, now)

        job.result = {"responseId": str(response.id), "cardsCreated": len(partials)}
        job.status = "done"
    except Exception as exc:  # noqa: BLE001 — фиксируем причину в job
        job.status = "failed"
        job.result = {"error": str(exc)}
    finally:
        job.updated_at = now
