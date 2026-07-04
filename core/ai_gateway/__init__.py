"""AI-gateway: единая точка вызова LLM (см. 02-logical §6).

Ключ Claude — из окружения (инвариант №2). Нет ключа → MockAIGateway.
"""

from sqlalchemy import desc
from sqlalchemy.orm import Session

from core.ai_gateway.base import AIGateway, render_prompt
from core.ai_gateway.claude import ClaudeAIGateway
from core.ai_gateway.mock import MockAIGateway
from core.config import settings
from core.models import Rubric

__all__ = ["AIGateway", "get_ai_gateway", "get_rubric", "render_prompt"]


def get_ai_gateway() -> AIGateway:
    """Claude при заданном CLAUDE_API_KEY, иначе детерминированный Mock."""
    if settings.claude_api_key:
        return ClaudeAIGateway()
    return MockAIGateway()


def get_rubric(session: Session, rubric_id: str, version: int | None = None) -> Rubric | None:
    """Рубрика по id: конкретная версия или последняя."""
    q = session.query(Rubric).filter(Rubric.id == rubric_id)
    if version is not None:
        return q.filter(Rubric.version == version).first()
    return q.order_by(desc(Rubric.version)).first()
