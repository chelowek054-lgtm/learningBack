"""Push-уведомления вне приложения (T-0086, R-0044).

Канал нужен, чтобы человек узнал о курсе при закрытом приложении. Провайдер — Expo Push: он работает с
EAS-сборкой клиента и прячет за одним API FCM и APNs. Отправитель подменяем: другой провайдер — это новый
класс с методом `send`, остальной код не меняется.

Правила:
  * отправляется только на устройства, которые человек сам зарегистрировал и не отключил;
  * текст общий: вид уведомления и область, без названий понятий, источников и личных данных;
  * сбой канала не ломает создание уведомления: отправка — отдельная задача, временный сбой повторяется
    по правилам очереди (`job_max_attempts`), постоянный (нет уведомления) завершает задачу;
  * токен, который провайдер назвал недействительным, удаляется.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Callable
from typing import Any, Protocol

import httpx
from sqlalchemy.orm import Session

from core.config import settings
from core.models import Job, PushDevice

log = logging.getLogger("praxis.push")

JOB_TYPE = "push_send"
EXPO_URL = "https://exp.host/--/api/v2/push/send"
TOKEN_RE = re.compile(r"^Expo(nent)?PushToken\[[A-Za-z0-9_\-]{10,}\]$")
PLATFORMS = ("ios", "android", "web")
INVALID_TOKEN_ERRORS = ("DeviceNotRegistered", "InvalidCredentials")


class PushError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class Sender(Protocol):
    def send(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Отправить пачку; вернуть по элементу на каждое сообщение: {"status": "ok"|"error", ...}."""
        ...


class ExpoSender:
    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport

    def send(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        if settings.push_expo_access_token:
            headers["Authorization"] = f"Bearer {settings.push_expo_access_token}"
        with httpx.Client(timeout=15.0, transport=self._transport) as client:
            resp = client.post(EXPO_URL, json=messages, headers=headers)
        resp.raise_for_status()  # 5xx и сеть — временный сбой: задача повторится
        data = resp.json().get("data")
        if not isinstance(data, list) or len(data) != len(messages):
            raise RuntimeError("Ответ провайдера push не разобран")
        return data


_sender: Sender | None = None


def get_sender() -> Sender | None:
    """Отправитель по настройке; None — канал выключен (PUSH_PROVIDER=off)."""
    if _sender is not None:
        return _sender
    if settings.push_provider == "expo":
        return ExpoSender()
    return None


def set_sender(sender: Sender | None) -> None:
    """Подмена отправителя (тесты)."""
    global _sender
    _sender = sender


# ---- устройства ----


def register(session: Session, user_id: uuid.UUID, token: str, platform: str) -> PushDevice:
    """Зарегистрировать устройство: тем самым человек соглашается на push. Токен переходит к текущему человеку."""
    token = token.strip()
    if not TOKEN_RE.match(token):
        raise PushError("bad_token", "Токен устройства не похож на токен Expo Push")
    if platform not in PLATFORMS:
        raise PushError("bad_platform", f"Платформа: {', '.join(PLATFORMS)}")
    device = session.query(PushDevice).filter_by(token=token).one_or_none()
    if device is None:
        device = PushDevice(user_id=user_id, token=token, platform=platform, enabled=True)
        session.add(device)
    else:
        device.user_id, device.platform, device.enabled = user_id, platform, True
    session.flush()
    return device


def unregister(session: Session, user_id: uuid.UUID, token: str) -> bool:
    """Удалить устройство человека (выход из аккаунта). Чужой токен не трогается."""
    device = session.query(PushDevice).filter_by(token=token.strip(), user_id=user_id).one_or_none()
    if device is None:
        return False
    session.delete(device)
    session.flush()
    return True


def set_enabled(session: Session, user_id: uuid.UUID, enabled: bool) -> int:
    """Включить или отключить push на всех устройствах человека (настройка)."""
    devices = session.query(PushDevice).filter_by(user_id=user_id).all()
    for d in devices:
        d.enabled = enabled
    session.flush()
    return len(devices)


def state(session: Session, user_id: uuid.UUID) -> dict[str, Any]:
    devices = session.query(PushDevice).filter_by(user_id=user_id).all()
    return {
        "devices": len(devices),
        "enabled": any(d.enabled for d in devices),
        "available": settings.push_provider == "expo" or _sender is not None,
    }


# ---- отправка ----


def can_deliver(session: Session, user_id: uuid.UUID) -> bool:
    """Есть ли куда и чем отправлять: канал включён и у человека есть незаблокированное устройство."""
    if get_sender() is None:
        return False
    return session.query(PushDevice).filter_by(user_id=user_id, enabled=True).first() is not None


def deliver(
    session: Session, user_id: uuid.UUID, build: Callable[[str], dict[str, Any]]
) -> dict[str, Any]:
    """Отправить сообщение на все включённые устройства человека; `build(token)` собирает сообщение.

    Временный сбой провайдера пробрасывается (задача повторится), недействительные токены удаляются.
    """
    sender = get_sender()
    if sender is None:
        return {"sent": 0, "skipped": "provider_off"}
    devices = session.query(PushDevice).filter_by(user_id=user_id, enabled=True).all()
    if not devices:
        return {"sent": 0, "skipped": "no_devices"}

    results = sender.send([build(d.token) for d in devices])
    sent = removed = 0
    for device, res in zip(devices, results, strict=True):
        if res.get("status") == "ok":
            sent += 1
            continue
        error = (res.get("details") or {}).get("error")
        log.warning("push не доставлен: %s", error or res.get("message"))
        if error in INVALID_TOKEN_ERRORS:
            session.delete(device)
            removed += 1
    session.flush()
    return {"sent": sent, "removed": removed}


def enqueue(session: Session, user_id: uuid.UUID, input_ref: dict[str, Any]) -> Job | None:
    """Поставить отправку в очередь и сразу попробовать, если очередь inline.

    Не бросает: сбой канала не должен ломать создание уведомления (сборку курса, разбор документа).
    """
    try:
        if not can_deliver(session, user_id):
            return None
        job = Job(user_id=user_id, type=JOB_TYPE, status="pending", input_ref=input_ref)
        session.add(job)
        session.flush()
        if settings.jobs_mode == "inline":
            # В inline-режиме задачи человека идут только при его синхронизации, а push нужен
            # при закрытом приложении, поэтому пробуем отправить сразу; не вышло — остаётся pending.
            from core.jobs import process_job

            with session.begin_nested():
                process_job(session, job, None)  # type: ignore[arg-type]
        return job
    except Exception:  # noqa: BLE001
        log.warning("Не удалось поставить push в очередь", exc_info=True)
        return None
