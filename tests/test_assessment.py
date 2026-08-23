"""Генерация заданий из теории узла (KG3-02).

Главное, что проверяется, — заземление: задание должно опираться на контент
конкретного узла, а не на общие знания модели.
"""

from __future__ import annotations

import pytest

from modules.knowledge.assessment import (
    BLOOM_LEVELS,
    KIND_BLOOMS,
    AssessmentItem,
    NotGroundable,
    _parse_items,
    generate_assessment,
    render_content,
    validate_request,
)
from modules.knowledge.content import NodeContent

RICH = NodeContent.model_validate(
    {
        "summary": "Backprop считает градиенты по цепному правилу.",
        "sections": [
            {
                "heading": "Обратный проход",
                "body": "Градиент течёт от потерь к весам через цепное правило.",
                "examples": ["двуслойная сеть на MSE"],
                "counter_examples": ["градиент считают прямым проходом"],
            }
        ],
        "references": [{"title": "Rumelhart 1986"}],
    }
)

BARE = NodeContent(summary="ярлык")


# ---- допустимые сочетания ступени и формы ----


def test_test_kind_rejects_practice_blooms():
    with pytest.raises(ValueError, match="не подходит"):
        validate_request("apply", "test")


def test_practice_kind_rejects_recall_blooms():
    with pytest.raises(ValueError, match="не подходит"):
        validate_request("remember", "practice")


def test_probe_accepts_any_bloom():
    for bloom in BLOOM_LEVELS:
        validate_request(bloom, "probe")


@pytest.mark.parametrize("bad", ["unknown", "", "REMEMBER"])
def test_unknown_bloom_is_rejected(bad):
    with pytest.raises(ValueError):
        validate_request(bad, "probe")


def test_unknown_kind_is_rejected():
    with pytest.raises(ValueError, match="kind"):
        validate_request("remember", "quiz")


def test_every_kind_maps_to_known_blooms():
    for blooms in KIND_BLOOMS.values():
        assert set(blooms) <= set(BLOOM_LEVELS)


# ---- заземление ----


def test_rendered_content_carries_the_whole_theory():
    text = render_content("Backprop", RICH)

    assert "Backprop" in text
    assert "Обратный проход" in text
    assert "цепное правило" in text
    assert "двуслойная сеть на MSE" in text
    assert "градиент считают прямым проходом" in text
    assert "Rumelhart 1986" in text


def test_node_without_theory_is_refused_before_any_generation():
    with pytest.raises(NotGroundable):
        generate_assessment("Ярлык", BARE, "remember", "test")


def test_long_summary_alone_is_enough_to_ground():
    content = NodeContent(summary="я" * 200)

    payload = generate_assessment("Длинный", content, "remember", "test")

    assert payload.items


# ---- детерминированная генерация без ключа ----


def test_recall_item_uses_the_summary():
    payload = generate_assessment("Backprop", RICH, "remember", "test")

    assert payload.bloom == "remember"
    assert payload.kind == "test"
    assert payload.items[0].expected == RICH.summary


def test_understand_item_turns_misconceptions_into_distractors():
    payload = generate_assessment("Backprop", RICH, "understand", "test")

    options = payload.items[0].options
    correct = [o for o in options if o.correct]
    wrong = [o for o in options if not o.correct]

    assert len(correct) == 1
    assert wrong and wrong[0].text == "градиент считают прямым проходом"


def test_practice_item_uses_an_example_and_carries_criteria():
    payload = generate_assessment("Backprop", RICH, "apply", "practice")

    item = payload.items[0]
    assert "двуслойная сеть на MSE" in item.prompt
    assert item.criteria
    assert item.grounded_in == "Обратный проход"


def test_raw_dict_content_is_accepted():
    """Контент приходит из БД как dict, а не как модель."""
    payload = generate_assessment("Backprop", RICH.model_dump(), "remember", "test")

    assert payload.items


# ---- разбор вывода LLM ----


def test_broken_items_are_dropped_individually():
    raw = {
        "items": [
            {"prompt": "хороший", "expected": "ответ"},
            {"expected": "нет prompt"},
            "вообще не объект",
        ]
    }

    items = _parse_items(raw)

    assert [i.prompt for i in items] == ["хороший"]


@pytest.mark.parametrize("junk", [None, [], "строка", {}, {"items": None}])
def test_garbage_output_yields_no_items(junk):
    assert _parse_items(junk) == []


def test_item_defaults_are_safe():
    item = AssessmentItem(prompt="p")

    assert item.options == []
    assert item.criteria == []
    assert item.expected == ""
