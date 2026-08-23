"""AI-gateway: единая точка вызова LLM (см. 02-logical §6).

Ключ Claude — из окружения (инвариант №2). Нет ключа → MockAIGateway.
"""

from sqlalchemy import desc
from sqlalchemy.orm import Session

from core.ai_gateway.base import AIGateway, render_prompt
from core.ai_gateway.claude import ClaudeAIGateway
from core.ai_gateway.mock import MockAIGateway
from core.ai_gateway.openrouter import OpenRouterAIGateway
from core.config import settings
from core.models import Rubric

__all__ = ["AIGateway", "get_ai_gateway", "get_rubric", "has_llm", "render_prompt"]


def get_ai_gateway() -> AIGateway:
    """OpenRouter → Claude → Mock. Ключи только из окружения (инвариант №2).

    OpenRouter проверяется первым: если он настроен, это осознанный выбор
    провайдера, а не запасной путь.
    """
    if settings.openrouter_api_key:
        return OpenRouterAIGateway()
    if settings.claude_api_key:
        return ClaudeAIGateway()
    return MockAIGateway()


def has_llm() -> bool:
    """Настроен ли реальный провайдер.

    Модули спрашивают это, а не конкретный ключ: раньше они проверяли
    `claude_api_key`, и при настроенном OpenRouter всё равно отдавали заглушки.
    """
    return bool(settings.openrouter_api_key or settings.claude_api_key)


def get_rubric(session: Session, rubric_id: str, version: int | None = None) -> Rubric | None:
    """Рубрика по id: конкретная версия или последняя."""
    q = session.query(Rubric).filter(Rubric.id == rubric_id)
    if version is not None:
        return q.filter(Rubric.version == version).first()
    return q.order_by(desc(Rubric.version)).first()
