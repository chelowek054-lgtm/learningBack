"""Манифест модуля и его проверка (C-0001, T-0051, R-0027).

Модуль описывает себя одним манифестом: идентификатор, название, версия, версия контракта
ядра, что он даёт, что просит и от каких модулей зависит. Ядро проверяет манифест при
подключении и отказывает с понятной причиной — вместо молчаливого сбоя посреди работы.

Здесь только чистая проверка данных, без БД и без импорта модулей: так её можно проверить
тестом на выдуманных манифестах и применять к чужим модулям, которым ядро не доверяет.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Версия контракта ядра. Мажорная меняется при несовместимом изменении; модуль, написанный
# под прежнюю мажорную, не подключается до обновления. Минорная — совместимые дополнения:
# модуль под более старую минорную работает, под более новую — нет.
CONTRACT_VERSION = "1.0"

# Что модуль может ДАВАТЬ ядру: каждое — точка расширения контракта `BackendModule`.
# Список намеренно короткий: ровно то, чем пользуются три базовых модуля (без «на вырост»).
PROVIDES = frozenset(
    {
        "routes",
        "rubrics",
        "grade_jobs",
        "provision",
        "apply_activity",
        "admin_views",
        "study_methods",
        "evidence",
        "activity_payload",
        "study_preferences",
    }
)

# Что модуль может ПРОСИТЬ у ядра: доступ к общим данным и к модели. Пока это декларация —
# проверка при каждом обращении появится вместе с хранилищем данных пользователя и
# разрешениями (T-0054, T-0055); но неизвестный запрос отклоняется уже сейчас.
REQUESTS = frozenset(
    {
        "ai.structured",
        "ai.grade",
        "data.activity",
        "data.response",
        "data.srs_card",
        "data.job",
        "data.material",
        "data.profile",
        "data.mastery",
        "data.course",
        "data.goal",
    }
)

_ID = re.compile(r"^[a-z][a-z0-9_]{1,31}$")


class ManifestError(ValueError):
    """Манифест отклонён. `code` — для машин и тестов, текст — для человека."""

    def __init__(self, code: str, module_id: str, message: str) -> None:
        super().__init__(f"Модуль «{module_id}»: {message}")
        self.code = code
        self.module_id = module_id


@dataclass(frozen=True)
class ModuleManifest:
    id: str
    title: str
    version: str
    contract: str = CONTRACT_VERSION
    provides: frozenset[str] = field(default_factory=frozenset)
    requires: frozenset[str] = field(default_factory=frozenset)
    depends_on: tuple[str, ...] = ()

    def describe(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "version": self.version,
            "contract": self.contract,
            "provides": sorted(self.provides),
            "requires": sorted(self.requires),
            "dependsOn": list(self.depends_on),
        }


def parse_version(text: str) -> tuple[int, ...] | None:
    """`1.2` → (1, 2); `1.2.3` → (1, 2, 3); непонятное → None."""
    if not re.fullmatch(r"\d+(\.\d+){1,2}", (text or "").strip()):
        return None
    return tuple(int(p) for p in text.strip().split("."))


def contract_compatible(module_contract: str, core_contract: str = CONTRACT_VERSION) -> bool:
    """Мажорная совпадает, минорная модуля не новее ядра."""
    m, c = parse_version(module_contract), parse_version(core_contract)
    if m is None or c is None:
        return False
    return m[0] == c[0] and m[1] <= c[1]


def check_manifest(manifest: ModuleManifest | None, *, module_id: str = "?") -> None:
    """Проверить один манифест сам по себе; зависимости между модулями — `check_set`."""
    if manifest is None:
        raise ManifestError(
            "missing_manifest",
            module_id,
            "не объявил манифест: без него ядро не знает, что модуль даёт и просит",
        )
    mid = manifest.id
    if not _ID.match(mid or ""):
        raise ManifestError(
            "bad_id", mid or module_id, "идентификатор — строчные латинские буквы, цифры и «_»"
        )
    if not (manifest.title or "").strip():
        raise ManifestError("bad_title", mid, "не указано название")
    if parse_version(manifest.version) is None:
        raise ManifestError(
            "bad_version", mid, f"версия «{manifest.version}» не вида 1.2 или 1.2.3"
        )
    if parse_version(manifest.contract) is None:
        raise ManifestError(
            "bad_contract", mid, f"версия контракта «{manifest.contract}» не вида 1.2"
        )
    if not contract_compatible(manifest.contract):
        raise ManifestError(
            "contract_incompatible",
            mid,
            f"написан под контракт {manifest.contract}, а ядро поддерживает {CONTRACT_VERSION}: "
            "обновите модуль или ядро",
        )
    unknown_provides = sorted(manifest.provides - PROVIDES)
    if unknown_provides:
        raise ManifestError(
            "unknown_provides",
            mid,
            f"даёт неизвестную ядру возможность: {', '.join(unknown_provides)}",
        )
    unknown_requests = sorted(manifest.requires - REQUESTS)
    if unknown_requests:
        raise ManifestError(
            "unknown_request",
            mid,
            f"просит у ядра то, чего оно не выдаёт: {', '.join(unknown_requests)}",
        )
    if mid in manifest.depends_on:
        raise ManifestError("self_dependency", mid, "зависит сам от себя")


def check_set(manifests: list[ModuleManifest]) -> None:
    """Проверить набор подключаемых модулей: занятые id, отсутствующие зависимости, циклы."""
    seen: set[str] = set()
    for m in manifests:
        if m.id in seen:
            raise ManifestError("duplicate_id", m.id, "идентификатор уже занят другим модулем")
        seen.add(m.id)
    by_id = {m.id: m for m in manifests}
    for m in manifests:
        for dep in m.depends_on:
            if dep not in by_id:
                raise ManifestError(
                    "missing_dependency", m.id, f"зависит от «{dep}», а он не подключён"
                )
    # Цикл ищем обходом в глубину: два модуля, ждущих друг друга, не поднять по очереди.
    state: dict[str, int] = {}

    def visit(mid: str, path: tuple[str, ...]) -> None:
        state[mid] = 1
        for dep in by_id[mid].depends_on:
            if state.get(dep) == 1:
                cycle = " → ".join((*path[path.index(dep) :], dep)) if dep in path else dep
                raise ManifestError("dependency_cycle", mid, f"зависимости образуют цикл: {cycle}")
            if state.get(dep) is None:
                visit(dep, (*path, dep))
        state[mid] = 2

    for m in manifests:
        if state.get(m.id) is None:
            visit(m.id, (m.id,))


def load_order(manifests: list[ModuleManifest]) -> list[str]:
    """Порядок, в котором модули поднимаются: зависимости раньше зависимых."""
    by_id = {m.id: m for m in manifests}
    out: list[str] = []
    done: set[str] = set()

    def visit(mid: str) -> None:
        if mid in done:
            return
        done.add(mid)
        for dep in by_id[mid].depends_on:
            visit(dep)
        out.append(mid)

    for m in manifests:
        visit(m.id)
    return out
