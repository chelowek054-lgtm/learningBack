"""Охраняемое исполнение модуля и его контекст (T-0055, R-0030, A-0020).

Что здесь есть: модуль получает данные только через `ModuleContext` — интерфейс платформы с
проверкой разрешений и журналом; сеть закрыта, открыты адреса из манифеста; запись ограничена по
размеру; вызов модуля идёт под `guarded`: сбой не роняет ядро, превышение времени или числа
обращений останавливает модуль (не систему).

Чего здесь нет — и не стоит выдавать за него: это защита внутри одного процесса (A-0021). Она не
останавливает код, который сам вызывает `open`, `socket` или читает память процесса. Пока модули
пишут только мы, этого достаточно; перед подключением сторонних авторов нужна изоляция уровнем
процесса или контейнера, и граница для неё — именно этот интерфейс.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from core import modules, userdata


class SandboxError(RuntimeError):
    """Нарушение границы модуля; `code` — для тестов и журнала."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Limits:
    timeout_s: float = 2.0
    max_calls_per_minute: int = 600
    max_write_bytes: int = 256 * 1024
    max_fetch_bytes: int = 1024 * 1024
    max_consecutive_failures: int = 3


DEFAULT_LIMITS = Limits()

_calls: dict[str, deque[float]] = defaultdict(deque)
_failures: dict[str, int] = defaultdict(int)


def reset_counters() -> None:
    _calls.clear()
    _failures.clear()


def _stop(session: Session, module_id: str, why: str) -> None:
    """Остановить модуль: он отключается, данные остаются, ядро и другие модули работают."""
    try:
        modules.set_enabled(session, module_id, False)
    except modules.LifecycleError:
        pass  # уже отключён или нельзя отключить из-за зависимых — вызов всё равно отклонён
    _failures[module_id] = 0


def _spend_call(module_id: str, limits: Limits) -> None:
    now = time.monotonic()
    window = _calls[module_id]
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= limits.max_calls_per_minute:
        raise SandboxError("call_budget", f"Модуль «{module_id}» превысил число обращений в минуту")
    window.append(now)


def guarded(
    session: Session,
    module: Any,
    fn: Callable[..., Any],
    *args: Any,
    limits: Limits = DEFAULT_LIMITS,
    **kwargs: Any,
) -> Any:
    """Вызвать код модуля под охраной; вернуть результат или поднять `SandboxError`.

    Исключение модуля превращается в SandboxError("module_failed"); после нескольких подряд модуль
    отключается. Вызов, не уложившийся во время, бросается (поток останется доживать — убить его
    изнутри процесса нельзя), а модуль отключается сразу.
    """
    if not modules.is_enabled(module.id):
        raise SandboxError("module_disabled", f"Модуль «{module.id}» отключён")
    try:
        _spend_call(module.id, limits)
    except SandboxError:
        _stop(session, module.id, "call_budget")
        raise

    box: dict[str, Any] = {}

    def run() -> None:
        try:
            box["result"] = fn(*args, **kwargs)
        except BaseException as e:  # noqa: BLE001 — код модуля не должен ронять ядро
            box["error"] = e

    worker = threading.Thread(target=run, daemon=True, name=f"module-{module.id}")
    worker.start()
    worker.join(limits.timeout_s)
    if worker.is_alive():
        _stop(session, module.id, "timeout")
        raise SandboxError("timeout", f"Модуль «{module.id}» не уложился во время и остановлен")
    if "error" in box:
        _failures[module.id] += 1
        if _failures[module.id] >= limits.max_consecutive_failures:
            _stop(session, module.id, "failures")
        raise SandboxError("module_failed", f"Модуль «{module.id}» завершился ошибкой") from box[
            "error"
        ]
    _failures[module.id] = 0
    return box.get("result")


class ModuleContext:
    """Единственный путь модуля к данным и сети. Привязан к одному модулю и одному человеку.

    Модуль не выбирает, чьи данные читать и под каким именем: идентификаторы заданы платформой при
    создании контекста, поэтому «прочитать чужое» или «назваться другим модулем» через него нельзя.
    """

    def __init__(
        self,
        session: Session,
        module: Any,
        user_id: uuid.UUID,
        registry: dict[str, userdata.DataType],
        *,
        fetcher: Callable[[str], bytes] | None = None,
        limits: Limits = DEFAULT_LIMITS,
    ) -> None:
        self._session, self._module, self._user_id = session, module, user_id
        self._registry, self._fetcher, self._limits = registry, fetcher, limits

    def read(self, type_id: str, purpose: str) -> userdata.Records:
        _spend_call(self._module.id, self._limits)
        return userdata.read(
            self._session, self._module, self._user_id, type_id, purpose, self._registry
        )

    def write(self, type_id: str, purpose: str, records: userdata.Records) -> int:
        _spend_call(self._module.id, self._limits)
        size = len(json.dumps(records, default=str).encode())
        if size > self._limits.max_write_bytes:
            raise SandboxError(
                "write_too_large",
                f"Запись {size} байт больше лимита {self._limits.max_write_bytes}",
            )
        return userdata.write(
            self._session, self._module, self._user_id, type_id, purpose, records, self._registry
        )

    def fetch(self, url: str) -> bytes:
        """Запрос наружу: только к адресам из манифеста; без разрешённого адреса — отказ."""
        host = (urlparse(url).hostname or "").lower()
        if host not in self._module.manifest.network:
            raise SandboxError(
                "network_denied", f"Модулю «{self._module.id}» нельзя ходить на «{host}»"
            )
        if self._fetcher is None:
            raise SandboxError("network_unavailable", "Выход в сеть на этой установке не настроен")
        _spend_call(self._module.id, self._limits)
        body = self._fetcher(url)
        if len(body) > self._limits.max_fetch_bytes:
            raise SandboxError("response_too_large", "Ответ больше допустимого размера")
        return body
