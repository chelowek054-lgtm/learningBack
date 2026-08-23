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


# JSON-schema графа (input_schema инструмента submit_graph для build_graph/expand_node).
GRAPH_IO_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "nodes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "snake_case латиницей"},
                    "title": {"type": "string"},
                    "tier": {"type": "string", "enum": ["core", "derived"]},
                    "content": {
                        "type": "object",
                        "properties": {"summary": {"type": "string"}},
                    },
                    "bloomLevels": {"type": "array", "items": {"type": "string"}},
                    "difficulty": {"type": "integer"},
                    "confidence": {"type": "number"},
                },
                "required": ["key", "title", "tier", "content"],
            },
        },
        "edges": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "from": {"type": "string"},
                    "to": {"type": "string"},
                    "type": {
                        "type": "string",
                        "enum": [
                            "prereq", "specializes", "part_of",
                            "related", "contrasts", "misconception", "example",
                        ],
                    },
                },
                "required": ["from", "to", "type"],
            },
        },
    },
    "required": ["nodes", "edges"],
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

    def build_graph(self, domain: str, topic: str) -> dict[str, Any]:
        """Черновой граф темы: {nodes:[{key,title,tier,content,...}], edges:[{from,to,type}]}.
        LLM помечает кандидатов в ядро (tier='core'); куратор подтверждает (KG2)."""
        ...

    def expand_node(self, node_title: str, direction: str) -> dict[str, Any]:
        """Дорастить ветку от узла в направлении интереса: {nodes, edges}."""
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
