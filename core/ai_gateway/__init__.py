"""AI-gateway: единая точка вызова LLM (см. 02-logical §6).

Ключ провайдера — из окружения (инвариант №2). Нет ключа → MockAIGateway.
"""

from sqlalchemy import desc
from sqlalchemy.orm import Session

from core.ai_gateway.base import AIGateway, render_prompt
from core.ai_gateway.mock import MockAIGateway
from core.ai_gateway.openai_compatible import OpenAICompatibleGateway
from core.config import settings
from core.models import Rubric

__all__ = ["AIGateway", "get_ai_gateway", "get_rubric", "has_llm", "render_prompt"]


def get_ai_gateway() -> AIGateway:
    """Настроенный провайдер → Mock (инвариант №2: ключи из окружения).

    Реализация одна на всех: провайдер выбирается адресом в конфиге, а не
    отдельным классом. Чтобы добавить нового — хватит `LLM_BASE_URL`,
    `LLM_API_KEY` и слага модели, править код не нужно.
    """
    if settings.llm_api_key:
        return OpenAICompatibleGateway()
    return MockAIGateway()


def has_llm() -> bool:
    """Настроен ли реальный провайдер.

    Модули спрашивают это, а не переменную окружения напрямую: иначе появление
    нового провайдера пришлось бы разносить по всем местам проверки.
    """
    return bool(settings.llm_api_key)


def get_rubric(session: Session, rubric_id: str, version: int | None = None) -> Rubric | None:
    """Рубрика по id: конкретная версия или последняя."""
    q = session.query(Rubric).filter(Rubric.id == rubric_id)
    if version is not None:
        return q.filter(Rubric.version == version).first()
    return q.order_by(desc(Rubric.version)).first()
