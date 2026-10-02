"""Реестр backend-модулей (SPEC-01, ADR-0019).

Ядро не знает предметных модулей: ни импортов, ни имён. Какие модули подключены,
решает конфиг (`INSTALLED_MODULES`, как `INSTALLED_APPS` в Django): каждый пункт —
пакет, экспортирующий объект `backend` — наследника `BackendModule`. Ядро
обращается к модулям только через этот контракт.
"""

from __future__ import annotations

import importlib
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy.orm import Session

from core.config import settings
from core.manifest import ManifestError, ModuleManifest, check_manifest, check_set
from core.methods import APPLY, MethodError, StudyMethod, check_methods, for_purpose
from core.models import ModuleState, Rubric

log = logging.getLogger(__name__)


class BackendModule:
    """Контракт модуля. Всё необязательное — no-op по умолчанию."""

    id: str = ""
    # Манифест обязателен (C-0001): без него ядро не подключит модуль.
    manifest: ModuleManifest | None = None

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

    def study_methods(self) -> list[StudyMethod]:
        """Способы изучения, которые даёт модуль (контракт — core.methods)."""
        return []

    def accept_evidence(self, session: Session, user_id: Any, domain: str, evidence: Any) -> None:
        """Принять свидетельство об освоении (core.evidence). Принимает тот, кто хранит освоенность."""

    def accepts_evidence(self) -> bool:
        return type(self).accept_evidence is not BackendModule.accept_evidence

    def purge_data(self, session: Session) -> None:
        """Удалить ВСЕ данные модуля. Вызывается только при явном удалении модуля.

        По умолчанию не поддерживается: ядро не знает таблиц модуля и не вправе гадать.
        """
        raise NotImplementedError


_modules: list[BackendModule] | None = None


# Какой метод контракта соответствует заявленной возможности.
_PROVIDES_METHODS = {
    "routes": "router",
    "rubrics": "rubrics",
    "grade_jobs": "grade_jobs",
    "provision": "provision",
    "apply_activity": "apply_activity",
    "admin_views": "admin_views",
    "study_methods": "study_methods",
    "evidence": "accept_evidence",
}


def actual_provides(module: BackendModule) -> frozenset[str]:
    """Что модуль реально реализует: переопределённые методы контракта."""
    return frozenset(
        cap
        for cap, method in _PROVIDES_METHODS.items()
        if getattr(type(module), method) is not getattr(BackendModule, method)
    )


def validate_modules(loaded: list[BackendModule]) -> None:
    """Проверить манифесты подключаемых модулей и их согласованность с кодом.

    Манифест не должен врать: заявленное обязано быть реализовано, а реализованное —
    заявлено, иначе по нему нельзя будет решать, что модулю разрешено.
    """
    for m in loaded:
        check_manifest(m.manifest, module_id=m.id or "?")
        assert m.manifest is not None
        if m.manifest.id != m.id:
            raise ManifestError(
                "id_mismatch", m.id, f"в манифесте указан другой идентификатор «{m.manifest.id}»"
            )
        actual = actual_provides(m)
        missing = sorted(m.manifest.provides - actual)
        if missing:
            raise ManifestError(
                "provides_not_implemented",
                m.id,
                f"заявляет {', '.join(missing)}, но не реализует",
            )
        undeclared = sorted(actual - m.manifest.provides)
        if undeclared:
            raise ManifestError(
                "undeclared_capability",
                m.id,
                f"реализует {', '.join(undeclared)}, но не объявил в манифесте",
            )
    check_set([m.manifest for m in loaded if m.manifest is not None])
    # Способы разных модулей живут в одном пространстве id: «письмо» нельзя объявить дважды.
    try:
        check_methods([sm for m in loaded for sm in m.study_methods()])
    except MethodError as e:
        raise ManifestError(e.code, "study_methods", str(e)) from e


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
    validate_modules(loaded)
    _modules = loaded
    return loaded


def practice_activity_type(domain: str, default: str | None = None) -> str | None:
    """Тип практики узла: первый модуль, заявивший область, иначе `default`."""
    for m in enabled_modules():
        declared = m.apply_activity(domain)
        if declared:
            return declared
    return default if default is not None else activity_type_for(APPLY)


def grade_job_modules() -> dict[str, str]:
    """job.type → модуль карточек; коллизии типов между модулями запрещены."""
    out: dict[str, str] = {}
    for m in enabled_modules():
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
    for m in enabled_modules():
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
    for m in enabled_modules():
        m.provision(session, user_id, subject, now)


def admin_views() -> list[Any]:
    return [v for m in load_modules() for v in m.admin_views()]


def routers() -> list[APIRouter]:
    return [r for m in load_modules() if (r := m.router()) is not None]


# ---- способы изучения (T-0053) ----


def study_methods() -> list[StudyMethod]:
    """Способы включённых модулей; у каждого проставлен модуль-владелец."""
    out: list[StudyMethod] = []
    for m in enabled_modules():
        for sm in m.study_methods():
            out.append(sm if sm.module else StudyMethod(**{**sm.__dict__, "module": m.id}))
    return out


def activity_type_for(purpose: str, preferred: str | None = None) -> str | None:
    """Тип активности для шага изучения: способ выбирает ядро среди включённых модулей.

    None — ни один включённый модуль не даёт способа для этого шага: курс его пропускает,
    а не падает (отключили модуль повторения — шага «удержать» нет, остальное работает).
    """
    method = for_purpose(study_methods(), purpose, preferred)
    return method.activity_type if method else None


# ---- жизненный цикл (C-0001, T-0051) ----
# Состояние модулей лежит в БД, а не в конфиге: отключение — решение администратора на
# работающей системе. Данные модуля при отключении не трогаются; удаление — отдельная,
# явная команда. Кэш живёт в процессе API (один процесс по A-0021); при нескольких
# воркерах его нужно заменить общим хранилищем.

_enabled: dict[str, bool] | None = None


class LifecycleError(ValueError):
    """Операцию над модулем нельзя выполнить; `code` — для клиента."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def is_enabled(module_id: str) -> bool:
    """Включён ли модуль. До первой синхронизации с БД считается включённым."""
    return True if _enabled is None else _enabled.get(module_id, True)


def enabled_modules() -> list[BackendModule]:
    return [m for m in load_modules() if is_enabled(m.id)]


def _refresh_cache(session: Session) -> None:
    global _enabled
    _enabled = {row.id: row.enabled for row in session.query(ModuleState).all()}


def reset_state_cache() -> None:
    """Для тестов: забыть кэш состояния, чтобы тесты не влияли друг на друга."""
    global _enabled
    _enabled = None


def sync_module_state(session: Session) -> list[str]:
    """Сверить подключённые модули с БД: новые — установка, другая версия — обновление.

    Модуль, убранный из конфига, остаётся в таблице с `installed=False`: его данные не
    удаляются молча. Возвращает строки журнала о том, что изменилось.
    """
    now = datetime.now(timezone.utc)
    log_lines: list[str] = []
    seen: set[str] = set()
    for m in load_modules():
        seen.add(m.id)
        version = m.manifest.version if m.manifest else "0.0"
        row = session.get(ModuleState, m.id)
        if row is None:
            session.add(ModuleState(id=m.id, version=version, enabled=True, installed=True))
            log_lines.append(f"{m.id}: установлен, версия {version}")
        else:
            if row.version != version:
                log_lines.append(f"{m.id}: обновлён {row.version} → {version}")
                row.previous_version, row.version = row.version, version
            if not row.installed:
                row.installed = True
                log_lines.append(f"{m.id}: подключён снова")
            row.updated_at = now
    for row in session.query(ModuleState).filter(ModuleState.id.notin_(seen)):
        if row.installed:
            row.installed = False
            row.updated_at = now
            log_lines.append(f"{row.id}: убран из конфигурации, данные сохранены")
    session.flush()
    _refresh_cache(session)
    return log_lines


def _manifest_of(module_id: str) -> ModuleManifest:
    for m in load_modules():
        if m.id == module_id and m.manifest is not None:
            return m.manifest
    raise LifecycleError("unknown_module", f"Модуль «{module_id}» не подключён")


def set_enabled(session: Session, module_id: str, enabled: bool) -> None:
    """Включить или отключить модуль. Данные не затрагиваются ни в одном из случаев."""
    manifest = _manifest_of(module_id)
    row = session.get(ModuleState, module_id)
    if row is None:
        raise LifecycleError("not_installed", f"Модуль «{module_id}» ещё не установлен")
    if enabled:
        off = [d for d in manifest.depends_on if not is_enabled(d)]
        if off:
            raise LifecycleError(
                "dependency_disabled",
                f"Сначала включите {', '.join(off)}: модуль «{module_id}» от них зависит",
            )
    else:
        # Отключать то, на чём стоят другие, нельзя: они бы остались в рабочем виде без основы.
        dependents = [
            m.id
            for m in load_modules()
            if m.manifest and module_id in m.manifest.depends_on and is_enabled(m.id)
        ]
        if dependents:
            raise LifecycleError(
                "has_dependents",
                f"От «{module_id}» зависят включённые модули: {', '.join(dependents)}",
            )
    row.enabled = enabled
    row.updated_at = datetime.now(timezone.utc)
    session.flush()
    _refresh_cache(session)


def uninstall(session: Session, module_id: str, *, confirm: bool) -> None:
    """Удалить данные модуля и его состояние. Только отключённый, только по явному `confirm`.

    Данные удаляет сам модуль (`purge_data`): ядро не знает его таблиц. Модуль без такой
    возможности удалить нельзя — это честнее, чем сделать вид и оставить мусор.
    """
    module = next((m for m in load_modules() if m.id == module_id), None)
    if module is None:
        raise LifecycleError("unknown_module", f"Модуль «{module_id}» не подключён")
    if not confirm:
        raise LifecycleError(
            "confirmation_required", "Удаление данных модуля нужно подтвердить явно"
        )
    if is_enabled(module_id):
        raise LifecycleError("still_enabled", "Сначала отключите модуль")
    if type(module).purge_data is BackendModule.purge_data:
        raise LifecycleError(
            "purge_unsupported", f"Модуль «{module_id}» не умеет удалять свои данные"
        )
    module.purge_data(session)
    row = session.get(ModuleState, module_id)
    if row is not None:
        session.delete(row)
    session.flush()
    _refresh_cache(session)


def module_status(session: Session) -> list[dict[str, Any]]:
    """Подключённые модули с манифестом и состоянием — для панели администратора."""
    rows = {r.id: r for r in session.query(ModuleState).all()}
    out = []
    for m in load_modules():
        row = rows.get(m.id)
        out.append(
            {
                **(m.manifest.describe() if m.manifest else {"id": m.id}),
                "enabled": row.enabled if row else True,
                "installed": row is not None and row.installed,
                "previousVersion": row.previous_version if row else None,
            }
        )
    return out


def require_enabled(module_id: str):
    """Зависимость маршрутов модуля: отключённый модуль отвечает 503, а не ломается."""

    def guard() -> None:
        if not is_enabled(module_id):
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                {"code": "module_disabled", "detail": f"Модуль «{module_id}» отключён"},
            )

    return guard


def module_routers() -> list[tuple[str, APIRouter]]:
    """Маршруты модулей вместе с id: app навешивает на каждый проверку «включён»."""
    return [(m.id, r) for m in load_modules() if (r := m.router()) is not None]
