"""MockAIGateway — детерминированная заглушка для разработки/тестов без ключа."""

from typing import Any

from core.models import Rubric


class MockAIGateway:
    """Правдоподобные, но фиктивные оценки. Не вызывает LLM."""

    def grade(
        self, rubric: Rubric, activity_payload: dict[str, Any], answer: Any
    ) -> dict[str, Any]:
        text = str(answer or "")
        words = len(text.split())

        # Псевдо-band по объёму (детерминированно, для проверки пайплайна).
        band = 5.0 if words < 150 else (6.5 if words < 250 else 7.5)

        criteria = [
            {"name": c["name"], "score": band, "max": c.get("max", 9), "comment": "mock"}
            for c in rubric.schema.get("criteria", [{"name": "Overall", "max": 9}])
        ]
        errors = []
        if "recieve" in text.lower():
            errors.append(
                {
                    "kind": "spelling",
                    "excerpt": "recieve",
                    "correction": "receive",
                    "explanation": "i перед e, кроме после c (mock)",
                }
            )
        return {
            "rubricId": rubric.id,
            "rubricVersion": rubric.version,
            "criteria": criteria,
            "overall": band,
            "errors": errors,
            "exemplar": "" if words else "",
            "gradedOfflineFallback": False,
        }

    def generate(self, generator_id: str, params: dict[str, Any]) -> dict[str, Any]:
        return {"generator": generator_id, "items": []}

    def build_graph(self, domain: str, topic: str) -> dict[str, Any]:
        # Детерминированный черновой ML-граф; ядро помечено tier='core'.
        def node(key, title, tier):
            return {
                "key": key,
                "title": title,
                "tier": tier,
                "content": {"summary": f"{title} — теория (mock)", "sections": [], "references": []},
                "bloomLevels": ["remember", "understand", "apply"],
                "difficulty": 2,
                "confidence": 0.6,
            }

        nodes = [
            node("linear_algebra", "Линейная алгебра", "core"),
            node("neural_nets", "Нейросети", "core"),
            node("backprop", "Backprop", "core"),
            node("softmax", "Softmax", "derived"),
            node("attention", "Attention", "derived"),
            node("transformers", topic or "Трансформеры", "derived"),
        ]
        edges = [
            {"from": "linear_algebra", "to": "neural_nets", "type": "prereq"},
            {"from": "neural_nets", "to": "backprop", "type": "prereq"},
            {"from": "neural_nets", "to": "softmax", "type": "prereq"},
            {"from": "backprop", "to": "attention", "type": "prereq"},
            {"from": "softmax", "to": "attention", "type": "prereq"},
            {"from": "attention", "to": "transformers", "type": "specializes"},
        ]
        return {"domain": domain, "nodes": nodes, "edges": edges}

    def expand_node(self, node_title: str, direction: str) -> dict[str, Any]:
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
