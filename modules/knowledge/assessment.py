"""Генерация заданий из теории узла (KG3-02).

Ключевой инвариант слоя: генерация **заземлена на `content` узла**, а не
свободная. Поэтому промпт собирается из разделов, примеров и контрпримеров
конкретного узла, а узлы без теории отсекаются до вызова LLM — по одному
короткому `summary` задание построить нельзя, получится пересказ заголовка.

Ступень Блума задаёт, что именно спрашивать, а `kind` — форму:
  * `test`     — вспомнить и объяснить (remember / understand);
  * `practice` — применить и создать (apply / analyze / evaluate / create);
  * `probe`    — одиночный зонд для плейсмента (KG4), любая ступень.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, ValidationError

from core.ai_gateway import get_ai_gateway, has_llm
from modules.knowledge.content import NodeContent, coerce_content

BLOOM_LEVELS = ("remember", "understand", "apply", "analyze", "evaluate", "create")

# Какие ступени осмысленны для каждой формы задания.
KIND_BLOOMS: dict[str, tuple[str, ...]] = {
    "test": ("remember", "understand"),
    "practice": ("apply", "analyze", "evaluate", "create"),
    "probe": BLOOM_LEVELS,
}

ITEMS_PER_REQUEST = 3


class NotGroundable(ValueError):
    """У узла нет теории, на которую можно опереться."""


class Option(BaseModel):
    text: str
    correct: bool = False
    why: str = ""


class AssessmentItem(BaseModel):
    prompt: str
    expected: str = ""
    options: list[Option] = Field(default_factory=list)
    criteria: list[str] = Field(default_factory=list)
    grounded_in: str = ""


class AssessmentPayload(BaseModel):
    bloom: str
    kind: str
    items: list[AssessmentItem]


ASSESSMENT_IO_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "вопрос или задание"},
                    "expected": {
                        "type": "string",
                        "description": "эталонный ответ или ключевые пункты для проверки",
                    },
                    "options": {
                        "type": "array",
                        "description": "варианты для закрытого вопроса; дистракторы — из заблуждений узла",
                        "items": {
                            "type": "object",
                            "properties": {
                                "text": {"type": "string"},
                                "correct": {"type": "boolean"},
                                "why": {"type": "string", "description": "почему верно/неверно"},
                            },
                            "required": ["text", "correct"],
                        },
                    },
                    "criteria": {
                        "type": "array",
                        "description": "критерии проверки для практики",
                        "items": {"type": "string"},
                    },
                    "grounded_in": {
                        "type": "string",
                        "description": "заголовок раздела узла, на который опирается задание",
                    },
                },
                "required": ["prompt", "expected"],
            },
        }
    },
    "required": ["items"],
}

_TOOL = "submit_assessment"
_TOOL_DESC = "Вернуть задания строго по теории переданного узла."


def validate_request(bloom: str, kind: str) -> None:
    if kind not in KIND_BLOOMS:
        raise ValueError(f"неизвестный kind: {kind!r}")
    if bloom not in BLOOM_LEVELS:
        raise ValueError(f"неизвестная ступень Блума: {bloom!r}")
    if bloom not in KIND_BLOOMS[kind]:
        allowed = ", ".join(KIND_BLOOMS[kind])
        raise ValueError(f"ступень {bloom!r} не подходит для kind={kind!r}; допустимы: {allowed}")


def render_content(title: str, content: NodeContent) -> str:
    """Теория узла в текст для промпта — это и есть «заземление»."""
    lines = [f"КОНЦЕПЦИЯ: {title}", "", f"КРАТКО: {content.summary}"]
    for section in content.sections:
        lines += ["", f"РАЗДЕЛ: {section.heading}", section.body]
        if section.examples:
            lines.append("Примеры: " + "; ".join(section.examples))
        if section.counter_examples:
            lines.append("Типичные заблуждения: " + "; ".join(section.counter_examples))
    if content.references:
        lines += ["", "Источники: " + "; ".join(r.title for r in content.references)]
    return "\n".join(lines)


def generate_assessment(
    title: str, raw_content: Any, bloom: str, kind: str
) -> AssessmentPayload:
    """Задания по теории узла. Без ключа — детерминированные, но тоже заземлённые."""
    validate_request(bloom, kind)
    content = NodeContent.model_validate(coerce_content(raw_content))
    if not content.is_groundable():
        raise NotGroundable(
            f"у узла «{title}» нет теории для генерации: нужен разбор в sections "
            "либо развёрнутый summary"
        )

    if not has_llm():
        items = _fixture_items(title, content, bloom, kind)
    else:
        raw = get_ai_gateway().structured(
            _TOOL, _TOOL_DESC, ASSESSMENT_IO_SCHEMA, _prompt(title, content, bloom, kind)
        )
        items = _parse_items(raw)
    if not items:
        raise NotGroundable(f"генерация не дала ни одного валидного задания для «{title}»")
    return AssessmentPayload(bloom=bloom, kind=kind, items=items)


def _prompt(title: str, content: NodeContent, bloom: str, kind: str) -> str:
    what = (
        "вопросы на воспроизведение и понимание"
        if kind == "test"
        else "практические задания на применение и создание"
    )
    return (
        f"Ниже теория одной концепции. Составь {ITEMS_PER_REQUEST} {what} "
        f"ступени Блума «{bloom}».\n\n"
        "ЖЁСТКОЕ ПРАВИЛО: опирайся ТОЛЬКО на приведённый текст. Не привлекай факты, "
        "которых в нём нет. В grounded_in укажи заголовок раздела, на котором строится "
        "задание. Если в узле есть заблуждения — используй их как дистракторы в options.\n\n"
        f"{render_content(title, content)}"
    )


def _parse_items(raw: Any) -> list[AssessmentItem]:
    """Разобрать вывод LLM: битые задания отбрасываем поштучно, а не всю пачку."""
    if not isinstance(raw, dict):
        return []
    items: list[AssessmentItem] = []
    for entry in raw.get("items") or []:
        try:
            items.append(AssessmentItem.model_validate(entry))
        except ValidationError:
            continue
    return items


# ---- детерминированные задания без ключа ----
# Собираются из того же контента, что ушёл бы в промпт: пайплайн KG3 можно
# разрабатывать и тестировать без обращения к LLM.


def _fixture_items(
    title: str, content: NodeContent, bloom: str, kind: str
) -> list[AssessmentItem]:  # noqa: ARG001
    first = content.sections[0] if content.sections else None

    # Ветвимся по СТУПЕНИ, а не по форме: probe может прийти на любой ступени,
    # и раньше он молча получал практическое задание вместо вопроса на вспоминание.
    if bloom == "remember":
        return [
            AssessmentItem(
                prompt=f"Что такое «{title}» и зачем оно нужно?",
                expected=content.summary,
                grounded_in="КРАТКО",
            )
        ]

    if first is None:
        return []

    if bloom == "understand":
        options = [Option(text=first.body, correct=True, why="соответствует разделу узла")]
        options += [
            Option(text=ce, correct=False, why="типичное заблуждение из теории узла")
            for ce in first.counter_examples
        ]
        return [
            AssessmentItem(
                prompt=f"Объясните своими словами: {first.heading}",
                expected=first.body,
                options=options,
                grounded_in=first.heading,
            )
        ]

    example = first.examples[0] if first.examples else title
    return [
        AssessmentItem(
            prompt=f"Примените «{title}» к задаче: {example}",
            expected=first.body,
            criteria=[
                "решение опирается на механику из раздела теории",
                "разобран хотя бы один пример из узла",
                "не воспроизведено заблуждение, названное в теории",
            ],
            grounded_in=first.heading,
        )
    ]
