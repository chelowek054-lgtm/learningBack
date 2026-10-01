"""Реестр backend-модулей (SPEC-01, ADR-0019).

Ядро не знает предметных модулей: ни импортов, ни имён. Какие модули подключены,
решает конфиг (`INSTALLED_MODULES`, как `INSTALLED_APPS` в Django): каждый пункт —
пакет, экспортирующий объект `backend` — наследника `BackendModule`. Ядро
обращается к модулям только через этот контракт.
"""

from __future__ import annotations

import importlib
import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter
from sqlalchemy.orm import Session

from core.config import settings
from core.models import Rubric

log = logging.getLogger(__name__)


class BackendModule:
    """Контракт модуля. Всё необязательное — no-op по умолчанию."""

    id: str = ""

    def router(self) -> APIRouter | None:
        """Собственные эндпоинты модуля."""
        return None

    def rubrics(self) -> list[dict[str, Any]]:
        """Рубрики-оценщики. Запись `(id, version)` неизменна: правка — новая версия."""
        return []

    def grade_jobs(self) -> dict[str, str]:
        """Типы jobs, оцениваемые по рубрике → модуль карточек для error-log."""
        return {}

    def provision(
        self, session: Session, user_id: Any, subject: dict[str, Any], now: datetime
    ) -> None:
        """Стартовый контент под выбранный предмет. Идемпотентно."""

    def apply_activity(self, domain: str) -> str | None:
        """Тип Activity для практики узла в этой области; None — обычное применение.

        Курс не знает предметов (A-0001): технический модуль сам объявляет, что
        его практика — задача на код, а не вопрос с выбором.
        """
        return None

    def admin_views(self) -> list[Any]:
        """Представления таблиц модуля для админки."""
        return []


_modules: list[BackendModule] | None = None


def load_modules(paths: str | None = None) -> list[BackendModule]:
    """Подключить модули из конфига (один раз)."""
    global _modules
    if _modules is not None and paths is None:
        return _modules
    loaded: list[BackendModule] = []
    for path in (paths if paths is not None else settings.installed_modules).split(","):
        path = path.strip()
        if not path:
            continue
        backend = importlib.import_module(path).backend
        if any(m.id == backend.id for m in loaded):
            raise ValueError(f"Модуль уже подключён: {backend.id}")
        loaded.append(backend)
    _modules = loaded
    return loaded


def practice_activity_type(domain: str, default: str = "concept_apply") -> str:
    """Тип практики узла: первый модуль, заявивший область, иначе `default`."""
    for m in load_modules():
        declared = m.apply_activity(domain)
        if declared:
            return declared
    return default


def grade_job_modules() -> dict[str, str]:
    """job.type → модуль карточек; коллизии типов между модулями запрещены."""
    out: dict[str, str] = {}
    for m in load_modules():
        for job_type, card_module in m.grade_jobs().items():
            if job_type in out:
                raise ValueError(f"Тип job объявлен дважды: {job_type}")
            out[job_type] = card_module
    return out


def sync_rubrics(session: Session) -> int:
    """Добавить недостающие рубрики модулей. Существующие `(id, version)` не трогает.

    Рубрика версионируется (NFR-06): правленый промпт — это новая версия, а не
    перезапись, иначе старые оценки потеряли бы смысл. Вернуть число добавленных.
    """
    added = 0
    for m in load_modules():
        for r in m.rubrics():
            if session.get(Rubric, (r["id"], r["version"])) is None:
                session.add(Rubric(**r))
                added += 1
    session.flush()
    return added


def provision_subject(
    session: Session, user_id: Any, subject: dict[str, Any], now: datetime
) -> None:
    """Предмет выбран или сменён: каждый модуль решает, нужен ли ему стартовый контент."""
    for m in load_modules():
        m.provision(session, user_id, subject, now)


def admin_views() -> list[Any]:
    return [v for m in load_modules() for v in m.admin_views()]


def routers() -> list[APIRouter]:
    return [r for m in load_modules() if (r := m.router()) is not None]
