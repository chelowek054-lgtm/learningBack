"""Граф без связей перезапрашивается один раз (находка прототипа «Английский B2», T-0019)."""

from __future__ import annotations

import pytest

from modules.knowledge import ai
from modules.knowledge.ai import has_links


def node(key):
    return {"key": key, "title": key, "tier": "core", "content": {}}


def graph(n, edges):
    return {"nodes": [node(f"n{i}") for i in range(n)], "edges": edges}


EDGE = {"from": "n0", "to": "n1", "type": "prereq"}


class Gateway:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts = []

    def structured(self, tool, desc, schema, prompt, **kw):
        self.prompts.append(prompt)
        return self.answers.pop(0)


@pytest.fixture
def llm(monkeypatch):
    def install(*answers):
        gw = Gateway(*answers)
        monkeypatch.setattr(ai, "has_llm", lambda: True)
        monkeypatch.setattr(ai, "get_ai_gateway", lambda: gw)
        return gw

    return install


def test_has_links_ignores_dangling_and_self_references():
    assert has_links(graph(3, [EDGE]))
    assert not has_links(graph(3, []))
    assert not has_links(graph(3, [{"from": "n0", "to": "ghost", "type": "prereq"}]))
    assert not has_links(graph(3, [{"from": "n0", "to": "n0", "type": "prereq"}]))


def test_graph_without_links_is_requested_again_with_a_direct_instruction(llm):
    gw = llm(graph(8, []), graph(8, [EDGE]))

    g = ai.build_graph("d", "тема")

    assert has_links(g) and len(gw.prompts) == 2
    assert "не было ни одной связи" in gw.prompts[1] and "не было" not in gw.prompts[0]


def test_graph_with_links_is_not_requested_again(llm):
    gw = llm(graph(8, [EDGE]))

    ai.build_graph("d", "тема")

    assert len(gw.prompts) == 1


def test_small_graph_may_have_no_links_and_costs_one_request(llm):
    gw = llm(graph(4, []))

    g = ai.build_graph("d", "тема")

    assert not has_links(g) and len(gw.prompts) == 1


def test_if_the_retry_is_also_empty_the_first_answer_is_kept_and_nothing_loops(llm):
    first = graph(8, [])
    gw = llm(first, graph(9, []))

    g = ai.build_graph("d", "тема")

    assert len(gw.prompts) == 2 and g is first


def test_retry_that_loses_the_nodes_is_not_taken(llm):
    first = graph(8, [])
    llm(first, {"nodes": [], "edges": [EDGE]})

    assert ai.build_graph("d", "тема") is first
