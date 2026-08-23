"""AI-роли слоя знаний: построение и рост графа.

Домен живёт ЗДЕСЬ, а не в ядре: JSON-схема графа, промпты и детерминированная
заглушка на случай отсутствия ключа. Ядро (`core.ai_gateway`) даёт только
доменно-нейтральный `structured()` — вызов LLM с произвольной схемой.
"""

from __future__ import annotations

from typing import Any

from core.ai_gateway import get_ai_gateway
from core.config import settings

# JSON-schema инструмента submit_graph (structured output для build/expand).
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
                        "description": "Теория узла: из неё генерируются тест и практика.",
                        "properties": {
                            "summary": {
                                "type": "string",
                                "description": "2-4 предложения: что это и зачем",
                            },
                            "sections": {
                                "type": "array",
                                "description": "1-3 раздела, раскрывающих концепцию",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "heading": {"type": "string"},
                                        "body": {"type": "string"},
                                        "examples": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                        },
                                        "counter_examples": {
                                            "type": "array",
                                            "description": "типичные заблуждения и что в них неверно",
                                            "items": {"type": "string"},
                                        },
                                    },
                                    "required": ["heading", "body"],
                                },
                            },
                            "references": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "title": {"type": "string"},
                                        "url": {"type": "string"},
                                    },
                                    "required": ["title"],
                                },
                            },
                        },
                        "required": ["summary", "sections"],
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

_TOOL = "submit_graph"
_TOOL_DESC = "Вернуть граф концепций (узлы с теорией + связи)."


def build_graph(domain: str, topic: str) -> dict[str, Any]:
    """Черновой граф темы: {nodes:[{key,title,tier,content,...}], edges:[{from,to,type}]}.

    LLM помечает кандидатов в ядро (tier='core'); куратор подтверждает (KG2).
    """
    if not settings.claude_api_key:
        return _fixture_graph(topic)
    prompt = (
        f"Построй граф концепций темы «{topic}» в области «{domain}».\n"
        "Каждый узел несёт ТЕОРИЮ, из которой потом генерируются тест и практика:\n"
        "  content.summary — 2–4 предложения: что это и зачем;\n"
        "  content.sections — 1–3 раздела, в каждом examples и counter_examples "
        "(типичные заблуждения);\n"
        "  content.references — источники, если уверен в них.\n"
        "Связи: prereq (предпосылка) и specializes (общее→частное).\n"
        "Пометь ФУНДАМЕНТАЛЬНЫЕ узлы (примитивы, от которых зависит многое) tier='core', "
        "остальные tier='derived'. Дай key (snake_case латиницей), title, bloomLevels, "
        "difficulty 1–5, confidence 0–1."
    )
    g = get_ai_gateway().structured(_TOOL, _TOOL_DESC, GRAPH_IO_SCHEMA, prompt)
    g.setdefault("domain", domain)
    return g


def expand_node(node_title: str, direction: str) -> dict[str, Any]:
    """Дорастить ветку от узла в направлении интереса: {nodes, edges}."""
    if not settings.claude_api_key:
        return _fixture_expand(node_title, direction)
    prompt = (
        f"Дорасти граф от узла «{node_title}» в направлении «{direction}»: "
        "2–4 новых узла (tier='derived') с теорией (summary + sections с примерами) "
        "и связями (prereq/specializes/related) "
        "от исходного узла. key — snake_case латиницей."
    )
    return get_ai_gateway().structured(_TOOL, _TOOL_DESC, GRAPH_IO_SCHEMA, prompt)


# ---- детерминированные заглушки (разработка без ключа) ----
# Живут в модуле, а не в Mock-gateway: предметное знание — не дело ядра.


def _node(key: str, title: str, tier: str) -> dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "tier": tier,
        "content": {
            "summary": (
                f"{title} — базовая концепция области. Заглушка для разработки без ключа: "
                "текст фиктивный, но форма настоящая, чтобы по ней работала генерация заданий."
            ),
            "sections": [
                {
                    "heading": f"Как устроен {title}",
                    "body": f"Разбор механики: что именно делает {title} и на чём это держится.",
                    "examples": [f"типовое применение {title}"],
                    "counter_examples": [f"частое заблуждение про {title}"],
                }
            ],
            "references": [{"title": "mock reference", "url": None}],
        },
        "bloomLevels": ["remember", "understand", "apply"],
        "difficulty": 2,
        "confidence": 0.6,
    }


def _fixture_graph(topic: str) -> dict[str, Any]:  # noqa: ARG001
    nodes = [
        _node("linear_algebra", "Линейная алгебра", "core"),
        _node("neural_nets", "Нейросети", "core"),
        _node("backprop", "Backprop", "core"),
        _node("softmax", "Softmax", "derived"),
        _node("attention", "Attention", "derived"),
        # Заголовок НЕ зависит от topic: дедупликация идёт по title, и
        # переменное имя плодило дубли узла при каждом новом topic.
        _node("transformers", "Трансформеры", "derived"),
    ]
    edges = [
        {"from": "linear_algebra", "to": "neural_nets", "type": "prereq"},
        {"from": "neural_nets", "to": "backprop", "type": "prereq"},
        {"from": "neural_nets", "to": "softmax", "type": "prereq"},
        {"from": "backprop", "to": "attention", "type": "prereq"},
        {"from": "attention", "to": "transformers", "type": "prereq"},
        {"from": "softmax", "to": "attention", "type": "prereq"},
    ]
    return {"nodes": nodes, "edges": edges}


def _fixture_expand(node_title: str, direction: str) -> dict[str, Any]:
    return {
        "nodes": [
            {
                "key": f"{direction}_ext",
                "title": f"{node_title} → {direction}",
                "tier": "derived",
                "content": {"summary": f"ветка про {direction} (mock)"},
                "bloomLevels": ["understand", "apply"],
                "difficulty": 3,
                "confidence": 0.5,
            }
        ],
        "edges": [{"from": node_title, "to": f"{direction}_ext", "type": "related"}],
    }
