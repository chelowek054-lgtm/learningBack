"""Профиль навыка: полное описание того, что нужно знать для цели (T-0088, R-0048).

Одним запросом на восемь понятий полный граф не получить. Поэтому сперва модель набрасывает контур:
из каких областей состоит навык, как они зависят друг от друга и из каких этапов состоит каждая; затем по
каждой области отдельным запросом — понятия с этапом, уровнем, пометкой «необязательно» и предпосылками.
Размер зависит от целевой ступени (A-0030). Результат — профиль: его человек видит и правит, а дальше он
сопоставляется с графом по близости (T-0089) и становится скелетом графа (T-0090).

Здесь чистая логика над словарями и вызовы модели; хранение и построение графа — снаружи. Модуль графа не
знает предметов (R-0028): заглушка без модели называет области и этапы ролями, а не предметными словами.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from core.ai_gateway import get_ai_gateway, has_llm
from modules.knowledge import stages

# Сколько понятий в области целевого размера (A-0030); меньше у начального уровня, больше у «создать».
SIZE_BY_LEVEL = {"remember": 12, "understand": 20, "apply": 32, "create": 48}
DEFAULT_LEVEL = "apply"
MAX_AREAS = 8
MAX_STAGES = 9
MIN_AREA_CONCEPTS = 4
PARALLEL_AREAS = 4
MAX_TEXT = 400

GOAL, FOUNDATION = "goal", "foundation"

OUTLINE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "areas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "snake_case латиницей"},
                    "title": {"type": "string"},
                    "summary": {"type": "string", "description": "одно предложение: что входит"},
                    "role": {"type": "string", "enum": [GOAL, FOUNDATION]},
                    "weight": {"type": "integer", "description": "объём области, от 1 до 5"},
                    "prereqs": {
                        "type": "array",
                        "description": "key областей, которые нужно знать до этой",
                        "items": {"type": "string"},
                    },
                    "stages": {
                        "type": "array",
                        "description": "этапы изучения области по порядку, от основ к сложному",
                        "items": {
                            "type": "object",
                            "properties": {"key": {"type": "string"}, "title": {"type": "string"}},
                            "required": ["key", "title"],
                        },
                    },
                },
                "required": ["key", "title", "role"],
            },
        }
    },
    "required": ["areas"],
}

CONCEPTS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "concepts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "snake_case латиницей"},
                    "title": {"type": "string"},
                    "summary": {
                        "type": "string",
                        "description": "2–3 предложения: что это и зачем",
                    },
                    "stage": {"type": "string", "description": "key этапа области"},
                    "level": {"type": "string", "enum": list(stages.LEVELS)},
                    "optional": {
                        "type": "boolean",
                        "description": "можно пропустить без ущерба для цели",
                    },
                    "prereqs": {
                        "type": "array",
                        "description": "key понятий этой области, нужных до этого",
                        "items": {"type": "string"},
                    },
                },
                "required": ["key", "title", "summary"],
            },
        }
    },
    "required": ["concepts"],
}


def target_size(level: str | None) -> int:
    return SIZE_BY_LEVEL.get(level or "", SIZE_BY_LEVEL[DEFAULT_LEVEL])


def _text(value: Any, limit: int = MAX_TEXT) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _slug(text: str, used: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "item"
    key, n = base, 2
    while key in used:
        key, n = f"{base}_{n}", n + 1
    used.add(key)
    return key


def _unique_key(raw: Any, title: str, used: set[str]) -> str:
    key = re.sub(r"[^a-z0-9_]+", "_", str(raw or "").strip().lower()).strip("_")
    if key and key not in used:
        used.add(key)
        return key
    return _slug(key or title, used)


def _drop_cycles(prereqs: dict[str, list[str]]) -> None:
    """Убрать предпосылки, замыкающие цикл: упорядочить граф с циклом нельзя."""
    state: dict[str, int] = {}

    def visit(key: str) -> None:
        state[key] = 1
        for p in list(prereqs[key]):
            if state.get(p) == 1:
                prereqs[key].remove(p)
            elif state.get(p) is None:
                visit(p)
        state[key] = 2

    for key in list(prereqs):
        if state.get(key) is None:
            visit(key)


def clean_outline(raw: Any, limit: int = MAX_AREAS) -> list[dict[str, Any]]:
    """Контур областей: уникальные ключи и названия, связи только на известные области без циклов,
    не больше `limit` областей и `MAX_STAGES` этапов, ровно одна область цели."""
    areas: list[dict[str, Any]] = []
    keys: set[str] = set()
    titles: set[str] = set()
    for a in (raw.get("areas") if isinstance(raw, dict) else None) or []:
        if not isinstance(a, dict):
            continue
        title = _text(a.get("title"), 120)
        if not title or title.lower() in titles:
            continue
        titles.add(title.lower())
        stage_list: list[dict[str, Any]] = []
        stage_keys: set[str] = set()
        for s in a.get("stages") or []:
            if not isinstance(s, dict) or not _text(s.get("title"), 120):
                continue
            stage_title = _text(s.get("title"), 120)
            stage_list.append(
                {
                    "key": _unique_key(s.get("key"), stage_title, stage_keys),
                    "title": stage_title,
                    "order": len(stage_list) + 1,
                }
            )
            if len(stage_list) >= MAX_STAGES:
                break
        try:
            weight = max(1, min(5, int(a.get("weight") or 3)))
        except (TypeError, ValueError):
            weight = 3
        areas.append(
            {
                "key": _unique_key(a.get("key"), title, keys),
                "title": title,
                "summary": _text(a.get("summary")),
                "role": GOAL if a.get("role") == GOAL else FOUNDATION,
                "weight": weight,
                "prereqs": [str(p) for p in (a.get("prereqs") or [])],
                "stages": stage_list,
                "concepts": [],
            }
        )
        if len(areas) >= max(1, min(limit, MAX_AREAS)):
            break
    known = {a["key"] for a in areas}
    for a in areas:
        a["prereqs"] = list(dict.fromkeys(p for p in a["prereqs"] if p in known and p != a["key"]))
    graph = {a["key"]: a["prereqs"] for a in areas}
    _drop_cycles(graph)
    _one_goal(areas)
    return areas


def _one_goal(areas: list[dict[str, Any]]) -> None:
    """Область цели одна: первая помеченная; если ни одной нет — та, от которой никто не зависит."""
    if not areas:
        return
    goals = [a for a in areas if a["role"] == GOAL]
    if goals:
        chosen = goals[0]
    else:
        needed = {p for a in areas for p in a["prereqs"]}
        free = [a for a in areas if a["key"] not in needed]
        chosen = max(free or areas, key=lambda a: len(a["prereqs"]))
    for a in areas:
        a["role"] = GOAL if a is chosen else FOUNDATION


def clean_concepts(raw: Any, stage_keys: list[str], budget: int) -> list[dict[str, Any]]:
    """Понятия области: уникальные ключи и названия, этап — из известных (иначе без этапа), уровень из
    трёх, предпосылки только среди понятий области и без циклов, не больше `budget`."""
    items: list[dict[str, Any]] = []
    keys: set[str] = set()
    titles: set[str] = set()
    for c in (raw.get("concepts") if isinstance(raw, dict) else None) or []:
        if not isinstance(c, dict):
            continue
        title = _text(c.get("title"), 160)
        if not title or title.lower() in titles:
            continue
        titles.add(title.lower())
        stage = c.get("stage") if c.get("stage") in stage_keys else None
        items.append(
            {
                "key": _unique_key(c.get("key"), title, keys),
                "title": title,
                "summary": _text(c.get("summary"), 600),
                "stage": stage,
                "level": stages.clean_level(c.get("level")),
                "optional": c.get("optional") is True,
                "prereqs": [str(p) for p in (c.get("prereqs") or [])],
            }
        )
        if len(items) >= max(1, budget):
            break
    known = {c["key"] for c in items}
    for c in items:
        c["prereqs"] = list(dict.fromkeys(p for p in c["prereqs"] if p in known and p != c["key"]))
    _drop_cycles({c["key"]: c["prereqs"] for c in items})
    return items


def area_budget(level: str | None, weight: int, max_weight: int) -> int:
    """Сколько понятий просить у области: самая большая получает полный размер уровня."""
    size = target_size(level)
    return max(MIN_AREA_CONCEPTS, min(size, round(size * weight / max(max_weight, 1))))


# ---- модель ----


def _fixture_outline(skill: str) -> dict[str, Any]:
    """Без модели: опорная область и область цели, три этапа в каждой — роли, а не предметные слова."""
    stage_list = [
        {"key": "basics", "title": "Основы"},
        {"key": "methods", "title": "Ключевые приёмы"},
        {"key": "practice", "title": "Применение"},
    ]
    return {
        "areas": [
            {
                "key": "foundations",
                "title": f"{skill}: предпосылки",
                "summary": "Что нужно знать до начала.",
                "role": FOUNDATION,
                "weight": 2,
                "stages": stage_list,
            },
            {
                "key": "main",
                "title": skill,
                "summary": "Основная область цели.",
                "role": GOAL,
                "weight": 4,
                "prereqs": ["foundations"],
                "stages": stage_list,
            },
        ]
    }


def _fixture_concepts(area: dict[str, Any]) -> dict[str, Any]:
    names = (
        ("basics", "Основные понятия"),
        ("methods", "Ключевые приёмы"),
        ("practice", "Применение"),
    )
    concepts = []
    for i, (stage, label) in enumerate(names):
        concepts.append(
            {
                "key": stage,
                "title": f"{area['title']} — {label.lower()}",
                "summary": f"{label} области «{area['title']}». Заглушка без модели: "
                "полное описание появится, когда будет подключён провайдер модели или найден источник, "
                "а пока это место в графе, которое держит порядок изучения.",
                "stage": stage,
                "level": ("basic", "middle", "advanced")[i],
                "optional": False,
                "prereqs": [names[i - 1][0]] if i else [],
            }
        )
    return {"concepts": concepts}


def propose_outline(
    skill: str, goal_text: str, level: str, limit: int = MAX_AREAS
) -> list[dict[str, Any]]:
    if not has_llm():
        return clean_outline(_fixture_outline(skill), limit)
    raw = get_ai_gateway().structured(
        "submit_outline",
        "Вернуть контур навыка: области и этапы.",
        OUTLINE_SCHEMA,
        (
            f"Человек хочет освоить навык «{skill}». Его цель и контекст: {goal_text}. "
            f"Целевая ступень: {level}. Определи, из каких областей знаний состоит этот навык и что нужно "
            f"знать ДО него (не больше {limit} областей). Одна область (role=goal) — сам навык, остальные "
            "(role=foundation) — то, на чём он стоит. Для каждой: key (snake_case латиницей), title, "
            "summary (одно предложение), weight от 1 до 5 (объём), prereqs — key областей, нужных до неё "
            "(без циклов), stages — 3–9 этапов изучения по порядку от основ к сложному. Не включай то, "
            "что человек уже знает и назвал в контексте, кроме случаев, когда без этого не обойтись."
        ),
    )
    return clean_outline(raw, limit)


def propose_concepts(
    skill: str, goal_text: str, level: str, area: dict[str, Any], budget: int
) -> list[dict[str, Any]]:
    stage_keys = [s["key"] for s in area["stages"]]
    if not has_llm():
        return clean_concepts(_fixture_concepts(area), stage_keys, budget)
    stage_lines = "; ".join(f"{s['key']} — {s['title']}" for s in area["stages"]) or "без этапов"
    raw = get_ai_gateway().structured(
        "submit_concepts",
        "Вернуть понятия области.",
        CONCEPTS_SCHEMA,
        (
            f"Навык «{skill}», цель и контекст: {goal_text}. Целевая ступень: {level}. "
            f"Область «{area['title']}» ({area['summary']}). Этапы: {stage_lines}.\n"
            f"Перечисли ВСЕ понятия, которые человеку на 100% нужно знать в этой области для цели, не больше "
            f"{budget}. Для каждого: key (snake_case латиницей), title, summary (2–3 предложения: что это и "
            "зачем), stage — key этапа, level (basic / middle / advanced), optional (true для того, что "
            "можно пропустить без ущерба для цели), prereqs — key понятий этой же области, нужных до него "
            "(без циклов). summary не короче 150 знаков. Порядок: от основ к сложному."
        ),
    )
    return clean_concepts(raw, stage_keys, budget)


def build_profile(
    skill: str, goal_text: str, level: str | None, limit: int = MAX_AREAS
) -> dict[str, Any]:
    """Полный профиль: контур, затем понятия каждой области (параллельно, области независимы)."""
    level = level if level in SIZE_BY_LEVEL else DEFAULT_LEVEL
    areas = propose_outline(skill, goal_text, level, limit)
    max_weight = max((a["weight"] for a in areas), default=1)

    def fill(area: dict[str, Any]) -> list[dict[str, Any]]:
        return propose_concepts(
            skill, goal_text, level, area, area_budget(level, area["weight"], max_weight)
        )

    if len(areas) > 1 and has_llm():
        with ThreadPoolExecutor(max_workers=PARALLEL_AREAS) as pool:
            results = list(pool.map(fill, areas))
    else:
        results = [fill(a) for a in areas]
    for area, concepts in zip(areas, results, strict=True):
        area["concepts"] = concepts
    return {"skill": skill, "level": level, "size": target_size(level), "areas": areas}


def clean_profile(raw: Any) -> dict[str, Any]:
    """Профиль после правки человеком: те же правила, что и для ответа модели."""
    raw = raw if isinstance(raw, dict) else {}
    outline = clean_outline({"areas": raw.get("areas")})
    by_key = {str(a.get("key")): a for a in (raw.get("areas") or []) if isinstance(a, dict)}
    for area in outline:
        source = by_key.get(area["key"], {})
        area["concepts"] = clean_concepts(
            {"concepts": source.get("concepts")},
            [s["key"] for s in area["stages"]],
            target_size(raw.get("level")) * 2,
        )
    level = raw.get("level") if raw.get("level") in SIZE_BY_LEVEL else DEFAULT_LEVEL
    return {
        "skill": _text(raw.get("skill"), 160),
        "level": level,
        "size": target_size(level),
        "areas": outline,
    }


def concept_count(profile: dict[str, Any]) -> int:
    return sum(len(a.get("concepts", [])) for a in profile.get("areas", []))
