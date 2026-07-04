"""Контракт AI-gateway (см. 02-logical §6). Реализации: mock.py, claude.py."""

from typing import Any, Protocol

from core.models import Rubric

# JSON-schema результата Grade (общая для рубрик; используется как input_schema
# инструмента submit_grade в Claude). Соответствует Grade из 02-logical §7.
GRADE_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "criteria": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "score": {"type": "number"},
                    "max": {"type": "number"},
                    "comment": {"type": "string"},
                },
                "required": ["name", "score", "max", "comment"],
            },
        },
        "overall": {"type": "number"},
        "errors": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string"},
                    "excerpt": {"type": "string"},
                    "correction": {"type": "string"},
                    "explanation": {"type": "string"},
                },
                "required": ["kind", "excerpt", "correction", "explanation"],
            },
        },
        "exemplar": {"type": "string"},
    },
    "required": ["criteria", "overall", "errors"],
}


class AIGateway(Protocol):
    def grade(
        self, rubric: Rubric, activity_payload: dict[str, Any], answer: Any
    ) -> dict[str, Any]:
        """Оценить продукцию по рубрике → структурированный Grade (по rubric.schema)."""
        ...

    def generate(self, generator_id: str, params: dict[str, Any]) -> dict[str, Any]:
        """Сгенерировать контент (для MVP — минимально)."""
        ...


def render_prompt(rubric: Rubric, activity_payload: dict[str, Any], answer: Any) -> str:
    """Промпт = шаблон рубрики + задание из payload + ответ пользователя."""
    task = activity_payload.get("prompt") or activity_payload.get("task") or ""
    return (
        f"{rubric.prompt}\n\n"
        f"=== ЗАДАНИЕ ===\n{task}\n\n"
        f"=== ОТВЕТ ПОЛЬЗОВАТЕЛЯ ===\n{answer}\n\n"
        f"Оцени строго по критериям рубрики и верни результат через инструмент."
    )
