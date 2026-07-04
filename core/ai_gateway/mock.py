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
