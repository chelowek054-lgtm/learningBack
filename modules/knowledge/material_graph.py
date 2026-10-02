"""Личные узлы из материала пользователя (T-0015, R-0011).

Две стадии, как и вся личная ветка графа: модель **предлагает** узлы и связи по
материалу, человек **подтверждает** — только тогда они попадают в его личный граф.
Узел заземлён: его теория ссылается на фрагменты материала (`fragment_ids`), и
предложение без существующего фрагмента отбрасывается — иначе «из материала» было
бы просто словами, а узел — галлюцинацией модели.
"""

from __future__ import annotations

import re
from typing import Any

from core.ai_gateway import get_ai_gateway, has_llm
from core.models import Material

# Сколько текста материала отдаём модели за один запрос: остальное отрезается,
# и ответ честно говорит об этом (`truncated`), а не молча теряет хвост.
MAX_PROMPT_FRAGMENTS = 40
MAX_FRAGMENT_PROMPT_CHARS = 900
MAX_NODES = 12

PROPOSAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "nodes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "snake_case латиницей"},
                    "title": {"type": "string"},
                    "summary": {"type": "string", "description": "2–4 предложения по тексту"},
                    "sections": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "heading": {"type": "string"},
                                "body": {"type": "string"},
                            },
                            "required": ["heading", "body"],
                        },
                    },
                    "fragments": {
                        "type": "array",
                        "description": "id фрагментов материала, на которых стоит узел",
                        "items": {"type": "string"},
                    },
                },
                "required": ["key", "title", "summary", "fragments"],
            },
        },
        "edges": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "from": {"type": "string"},
                    "to": {"type": "string"},
                    "type": {"type": "string", "enum": ["prereq", "specializes", "related"]},
                },
                "required": ["from", "to", "type"],
            },
        },
    },
    "required": ["nodes", "edges"],
}


def material_fragments(material: Material) -> list[dict[str, Any]]:
    content = material.content if isinstance(material.content, dict) else {}
    fragments = content.get("fragments")
    return [f for f in fragments if isinstance(f, dict) and f.get("id")] if fragments else []


def _slug(text: str, used: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "node"
    key, n = base, 2
    while key in used:
        key, n = f"{base}_{n}", n + 1
    used.add(key)
    return key


def _fixture(fragments: list[dict[str, Any]]) -> dict[str, Any]:
    """Без ключа модели: по узлу на фрагмент с заголовком, цепочкой предпосылок.

    Детерминированно и честно заземлено — на тексте самого материала, а не на выдумке.
    """
    nodes: list[dict[str, Any]] = []
    used: set[str] = set()
    for f in fragments:
        heading = (f.get("heading") or "").strip()
        title = heading or f"Фрагмент {f['id']}"
        text = str(f.get("text", "")).strip()
        nodes.append(
            {
                "key": _slug(f"n_{f['id']}", used),
                "title": title,
                "summary": text[:300],
                "sections": [{"heading": title, "body": text}],
                "fragments": [f["id"]],
            }
        )
        if len(nodes) >= MAX_NODES:
            break
    edges = [{"from": a["key"], "to": b["key"], "type": "prereq"} for a, b in zip(nodes, nodes[1:])]
    return {"nodes": nodes, "edges": edges}


def _prompt(material: Material, fragments: list[dict[str, Any]]) -> str:
    parts = []
    for f in fragments:
        label = f"[{f['id']}]"
        if f.get("heading"):
            label += f" {f['heading']}"
        if f.get("page"):
            label += f" (стр. {f['page']})"
        parts.append(f"{label}\n{str(f.get('text', ''))[:MAX_FRAGMENT_PROMPT_CHARS]}")
    return (
        f"Из материала «{material.title}» выдели НЕ БОЛЕЕ {MAX_NODES} понятий для личного графа "
        "знаний. Каждое понятие описывай ТОЛЬКО по тексту материала, ничего не додумывай.\n"
        "Для каждого дай key (snake_case латиницей), title, summary (2–4 предложения), "
        "sections и fragments — id фрагментов в квадратных скобках, на которых понятие "
        "стоит. Без ссылки на фрагмент понятие не принимается.\n"
        "Связи: prereq (предпосылка), specializes (общее→частное), related.\n\n"
        + "\n\n".join(parts)
    )


def ground(
    proposal: dict[str, Any], fragments: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Оставить только заземлённое: существующие фрагменты, уникальные ключи, связи между своими."""
    known = {str(f["id"]) for f in fragments}
    nodes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for n in proposal.get("nodes", []) or []:
        if not isinstance(n, dict):
            continue
        key, title = str(n.get("key", "")).strip(), str(n.get("title", "")).strip()
        refs = [str(r) for r in (n.get("fragments") or []) if str(r) in known]
        if not key or not title or key in seen or not refs:
            continue
        seen.add(key)
        sections = [
            {"heading": str(s.get("heading", "")), "body": str(s.get("body", ""))}
            for s in (n.get("sections") or [])
            if isinstance(s, dict)
        ]
        nodes.append(
            {
                "key": key,
                "title": title,
                "summary": str(n.get("summary", "")),
                "sections": sections,
                "fragments": list(dict.fromkeys(refs)),
            }
        )
    edges = [
        {"from": e["from"], "to": e["to"], "type": e.get("type", "related")}
        for e in (proposal.get("edges") or [])
        if isinstance(e, dict)
        and e.get("from") in seen
        and e.get("to") in seen
        and e.get("from") != e.get("to")
    ]
    return nodes[:MAX_NODES], edges


def propose(material: Material) -> dict[str, Any]:
    """Предложение узлов и связей по материалу (в БД ничего не пишется)."""
    fragments = material_fragments(material)
    used = fragments[:MAX_PROMPT_FRAGMENTS]
    if has_llm():
        raw = get_ai_gateway().structured(
            "submit_material_graph",
            "Вернуть понятия материала со ссылками на фрагменты.",
            PROPOSAL_SCHEMA,
            _prompt(material, used),
        )
    else:
        raw = _fixture(used)
    nodes, edges = ground(raw, used)
    return {
        "materialId": str(material.id),
        "nodes": nodes,
        "edges": edges,
        "truncated": len(fragments) > len(used),
    }


def node_content(material: Material, node: dict[str, Any]) -> dict[str, Any]:
    """Теория узла для личного слоя: ссылки на фрагменты лежат в references."""
    return {
        "summary": node.get("summary", ""),
        "sections": node.get("sections", []),
        "references": [
            {
                "title": material.title,
                "material_id": str(material.id),
                "fragment_ids": node.get("fragments", []),
            }
        ],
    }
