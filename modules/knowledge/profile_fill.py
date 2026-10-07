"""Наполнение контура понятиями: все области параллельно, статус по каждой (T-0103, R-0056).

Контур (области и этапы) человек уже подтвердил. Теперь по каждой области отдельным запросом приходят
понятия; готовая область сразу сохраняется и «публикуется» — её скелет попадает в граф, и с ней можно
работать, не дожидаясь остальных. Прогресс лежит в профиле (`progress`) и виден через API профиля.
Повтор после сбоя продолжает с недостающих областей: сделанное не пересчитывается.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from modules.knowledge import goal_intake, profile_build, profile_match, skill_profile
from modules.knowledge.models import SkillProfile

log = logging.getLogger(__name__)

WAITING, WORKING, DONE, FAILED = "waiting", "working", "done", "failed"


def init_progress(profile: dict[str, Any]) -> dict[str, Any]:
    """Статус по областям: готово там, где понятия уже есть; остальное ждёт."""
    states = {a["key"]: DONE if a.get("concepts") else WAITING for a in profile.get("areas", [])}
    return {"phase": "fill", "areas": states, "created": {}}


def _save(session: Session, row: SkillProfile, profile: dict[str, Any]) -> None:
    row.profile = profile
    flag_modified(row, "profile")
    row.updated_at = datetime.now(timezone.utc)
    session.commit()


def _publish(session: Session, row: SkillProfile, profile: dict[str, Any], gateway: Any) -> None:
    """Завести в графе готовые области: вызов повторяем, существующее не дублируется.

    Сбой публикации не роняет наполнение: итоговый проход всё равно соберёт скелет целиком.
    """
    done = [a for a in profile["areas"] if a.get("concepts")]
    try:
        with session.begin_nested():
            report = profile_build.build_skeleton(
                session, row.domain, {**profile, "areas": done}, profile.get("match")
            )
    except Exception:  # noqa: BLE001
        log.warning("Область не опубликована досрочно", exc_info=True)
        return
    created = profile["progress"]["created"]
    for item in report["areas"]:
        created[item["key"]] = created.get(item["key"], 0) + item["created"]


def _decide(session: Session, profile: dict[str, Any], area: dict[str, Any], gateway: Any) -> None:
    """Решение «уже есть / спорно / новое» для области; вместе с понятиями, когда они пришли."""
    decision = profile_match.match_profile(session, {"areas": [area]}, gateway)["areas"][0]
    match = profile.setdefault("match", {"areas": [], "summary": {}})
    match["areas"] = [d for d in match["areas"] if d["key"] != area["key"]] + [decision]


def fill_profile(session: Session, row: SkillProfile, goal_text: str, gateway: Any) -> list[str]:
    """Наполнить недостающие области; вернуть названия тех, что не удались (профиль сохранён)."""
    profile = dict(row.profile)
    profile["progress"] = profile.get("progress") or init_progress(profile)
    states = profile["progress"]["areas"]
    areas = profile["areas"]
    todo = [a for a in areas if not a.get("concepts")]
    level = profile.get("level")
    max_weight = max((a["weight"] for a in areas), default=1)

    for a in todo:
        _decide(session, profile, a, gateway)
        states[a["key"]] = WORKING
    _save(session, row, profile)

    def fill(area: dict[str, Any]) -> list[dict[str, Any]]:
        budget = skill_profile.concept_budget(area, level, max_weight)
        return skill_profile.propose_concepts(profile["skill"], goal_text, level, area, budget)

    failed: list[str] = []
    if todo:
        with ThreadPoolExecutor(max_workers=len(todo)) as pool:
            futures = {pool.submit(fill, a): a for a in todo}
            for future in as_completed(futures):
                area = futures[future]
                try:
                    area["concepts"] = future.result()
                except Exception:  # noqa: BLE001 — остальные области не страдают
                    log.warning("Область «%s» не наполнена", area["title"], exc_info=True)
                    states[area["key"]] = FAILED
                    failed.append(area["title"])
                    _save(session, row, profile)
                    continue
                states[area["key"]] = DONE
                _decide(session, profile, area, gateway)
                _publish(session, row, profile, gateway)
                _save(session, row, profile)
    return failed


def goal_text_for(session: Session, row: SkillProfile) -> str:
    goal = goal_intake.get_confirmed(session, row.user_id, row.domain)
    return goal_intake.as_goal_text(goal.summary) if goal is not None else ""
