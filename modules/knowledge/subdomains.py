"""Граф цели из субдоменов (T-0060, R-0032): разбиение, раздельная сборка, объединение.

Большая цель одним запросом даёт плоский граф плохого качества. Поэтому цель делится на
субдомены (модель предлагает, человек правит), каждый строится отдельным запросом как
примитивная область, а итог — объединение с связями-предпосылками через границы
субдоменов. Здесь только чистая логика над словарями: модель и база — снаружи.
"""

from __future__ import annotations

import re
from typing import Any

from core.ai_gateway import get_ai_gateway, has_llm

MAX_SUBDOMAINS = 8
# Сколько узлов строить в одном субдомене: примитивная область помещается в один запрос.
SUBDOMAIN_NODES = 6
# Сколько узлов-источников соседнего субдомена цепляем к одному «хвосту» предыдущего.
BRIDGE_FANOUT = 2

SPLIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "subdomains": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "snake_case латиницей"},
                    "title": {"type": "string"},
                    "summary": {"type": "string", "description": "одно предложение: что входит"},
                    "prereqs": {
                        "type": "array",
                        "description": "key субдоменов, которые нужно знать до этого",
                        "items": {"type": "string"},
                    },
                },
                "required": ["key", "title"],
            },
        }
    },
    "required": ["subdomains"],
}


def _slug(text: str, used: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "part"
    key, n = base, 2
    while key in used:
        key, n = f"{base}_{n}", n + 1
    used.add(key)
    return key


def clean_split(raw: dict[str, Any], limit: int = MAX_SUBDOMAINS) -> list[dict[str, Any]]:
    """Привести ответ модели (или правку человека) к безопасному виду.

    Дубли ключей и заголовков убираются, число ограничено, связи — только на известные
    субдомены без петель; цикл предпосылок разрывается, потому что граф с циклом
    невозможно упорядочить.
    """
    limit = max(1, min(limit, MAX_SUBDOMAINS))
    subs: list[dict[str, Any]] = []
    used: set[str] = set()
    titles: set[str] = set()
    for s in raw.get("subdomains", []) or []:
        if not isinstance(s, dict):
            continue
        title = str(s.get("title", "")).strip()
        if not title or title.lower() in titles:
            continue
        titles.add(title.lower())
        key = str(s.get("key", "")).strip()
        key = key if key and key not in used else _slug(key or title, used)
        used.add(key)
        subs.append(
            {
                "key": key,
                "title": title,
                "summary": str(s.get("summary", "")).strip(),
                "prereqs": [str(p) for p in (s.get("prereqs") or [])],
            }
        )
        if len(subs) >= limit:
            break
    known = {s["key"] for s in subs}
    for s in subs:
        s["prereqs"] = list(dict.fromkeys(p for p in s["prereqs"] if p in known and p != s["key"]))
    _break_cycles(subs)
    return subs


def _break_cycles(subs: list[dict[str, Any]]) -> None:
    """Убрать предпосылки, замыкающие цикл (обход в глубину по порядку появления)."""
    by_key = {s["key"]: s for s in subs}
    state: dict[str, int] = {}  # 1 — в обходе, 2 — завершён

    def visit(key: str) -> None:
        state[key] = 1
        for p in list(by_key[key]["prereqs"]):
            if state.get(p) == 1:
                by_key[key]["prereqs"].remove(p)  # ребро замыкает цикл
            elif state.get(p) is None:
                visit(p)
        state[key] = 2

    for s in subs:
        if state.get(s["key"]) is None:
            visit(s["key"])


def _fixture_split(topic: str) -> dict[str, Any]:
    """Без ключа модели: три последовательных субдомена, чтобы весь путь был проверяем."""
    return {
        "subdomains": [
            {"key": "foundations", "title": f"{topic}: основы", "summary": "Базовые понятия."},
            {
                "key": "core",
                "title": f"{topic}: ядро",
                "summary": "Основные методы.",
                "prereqs": ["foundations"],
            },
            {
                "key": "practice",
                "title": f"{topic}: применение",
                "summary": "Практика и сложные случаи.",
                "prereqs": ["core"],
            },
        ]
    }


def propose_split(domain: str, topic: str, limit: int = MAX_SUBDOMAINS) -> list[dict[str, Any]]:
    """Разбиение цели на субдомены; в БД ничего не пишется."""
    goal = topic.strip() or domain
    if has_llm():
        raw = get_ai_gateway().structured(
            "submit_split",
            "Вернуть разбиение цели на субдомены.",
            SPLIT_SCHEMA,
            (
                f"Цель обучения: «{goal}» (область «{domain}»). Раздели её на "
                f"{min(limit, MAX_SUBDOMAINS)} или меньше субдоменов — крупных частей, каждая из "
                "которых строится отдельным графом из 4–8 понятий. Для каждого: key "
                "(snake_case латиницей), title, summary (одно предложение) и prereqs — key "
                "субдоменов, которые нужно знать до него. Предпосылки не должны образовывать цикл."
            ),
        )
    else:
        raw = _fixture_split(goal)
    return clean_split(raw, limit)


def _fixture_subgraph(sub: dict[str, Any]) -> dict[str, Any]:
    title = sub["title"]
    return {
        "nodes": [
            {
                "key": "basic",
                "title": f"{title} — базовое понятие",
                "tier": "core",
                "content": {"summary": f"Базовое понятие субдомена «{title}». Заглушка."},
            },
            {
                "key": "applied",
                "title": f"{title} — применение",
                "tier": "derived",
                "content": {"summary": f"Применение понятий субдомена «{title}». Заглушка."},
            },
        ],
        "edges": [{"from": "basic", "to": "applied", "type": "prereq"}],
    }


def build_subdomain(domain: str, goal: str, sub: dict[str, Any]) -> dict[str, Any]:
    """Один субдомен — один запрос к модели (граф «примитивной области»)."""
    from modules.knowledge import ai

    if not has_llm():
        return _fixture_subgraph(sub)
    return ai.build_graph(
        domain, f"{goal}: {sub['title']}. {sub['summary']}".strip(), SUBDOMAIN_NODES
    )


def assemble(split: list[dict[str, Any]], subgraphs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Объединить графы субдоменов.

    Ключи узлов получают префикс субдомена — разные субдомены вправе называть узлы
    одинаково. Предпосылка субдомена превращается в связи prereq: «хвосты» (узлы без
    исходящих предпосылок внутри) предыдущего → «корни» (узлы без входящих) следующего.
    Так порядок «что до чего» сохраняется через границы, а не теряется при сливании.
    """
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    sinks: dict[str, list[str]] = {}
    sources: dict[str, list[str]] = {}
    for sub in split:
        g = subgraphs.get(sub["key"]) or {"nodes": [], "edges": []}
        prefix = sub["key"]
        keys = [f"{prefix}.{n['key']}" for n in g.get("nodes", []) if n.get("key")]
        for n in g.get("nodes", []):
            if n.get("key"):
                nodes.append({**n, "key": f"{prefix}.{n['key']}"})
        inner = [
            {"from": f"{prefix}.{e['from']}", "to": f"{prefix}.{e['to']}", "type": e["type"]}
            for e in g.get("edges", [])
            if f"{prefix}.{e['from']}" in keys and f"{prefix}.{e['to']}" in keys
        ]
        edges.extend(inner)
        has_out = {e["from"] for e in inner if e["type"] == "prereq"}
        has_in = {e["to"] for e in inner if e["type"] == "prereq"}
        sinks[sub["key"]] = [k for k in keys if k not in has_out]
        sources[sub["key"]] = [k for k in keys if k not in has_in]
    for sub in split:
        for pre in sub["prereqs"]:
            for tail in sinks.get(pre, []):
                for root in sources.get(sub["key"], [])[:BRIDGE_FANOUT]:
                    edges.append({"from": tail, "to": root, "type": "prereq"})
    return {"nodes": nodes, "edges": edges}


def budget(split: list[dict[str, Any]]) -> dict[str, int]:
    """Сколько запросов к модели стоит построение: видно человеку до того, как он согласился."""
    return {"requests": len(split), "subdomains": len(split), "limit": MAX_SUBDOMAINS}
