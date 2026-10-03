"""Данные человека: единое хранилище с доступом по разрешениям (T-0054, R-0031, A-0020).

Данные учащегося принадлежат платформе, а не модулям. Модуль не ходит в таблицы сам: он просит
тип данных через этот интерфейс, и ядро проверяет три вещи — модуль объявил запрос в манифесте
(`data.<тип>`), человек не отозвал разрешение, и пишет в журнал, кто что и зачем получил. Выдаётся
только то, что принадлежит этому человеку: чужих данных интерфейс отдать не может по построению.

Модули платформы (`first_party`) имеют разрешения на объявленное по умолчанию — человек может их
отозвать; сторонним модулям нужно явное согласие. Выгрузка и удаление идут по реестру типов, поэтому
доходят до всего, что зарегистрировано, включая данные подключённых модулей.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy.orm import Session

from core.models import (
    Activity,
    DataAccessLog,
    DataPermission,
    Job,
    Material,
    Response,
    SrsCard,
    User,
)

READ, WRITE = "read", "write"
MODES = (READ, WRITE)

Records = list[dict[str, Any]]


class DataAccessError(PermissionError):
    """Доступ отклонён; `code` — для тестов и клиента."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class DataType:
    """Тип данных человека: кто владеет, зачем нужен, сколько хранится и как выгрузить и стереть."""

    id: str
    title: str
    owner: str
    purpose: str
    retention_days: int | None  # None — пока человек не удалит
    read: Callable[[Session, uuid.UUID], Records]
    erase: Callable[[Session, uuid.UUID], int]
    write: Callable[[Session, uuid.UUID, Records], int] | None = None

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "owner": self.owner,
            "purpose": self.purpose,
            "retentionDays": self.retention_days,
            "writable": self.write is not None,
        }


def row_dict(row: Any) -> dict[str, Any]:
    """Строка таблицы как словарь без служебного: даты и идентификаторы — строками."""
    out: dict[str, Any] = {}
    for column in row.__table__.columns:
        value = getattr(row, column.key)
        if isinstance(value, uuid.UUID):
            value = str(value)
        elif isinstance(value, datetime | date):
            value = value.isoformat()
        out[column.key] = value
    return out


def model_type(
    model: Any,
    type_id: str,
    title: str,
    owner: str,
    purpose: str,
    retention_days: int | None,
    *,
    user_column: str = "user_id",
) -> DataType:
    """Тип данных, лежащий в одной таблице с колонкой владельца."""
    column = getattr(model, user_column)

    def read(session: Session, user_id: uuid.UUID) -> Records:
        return [row_dict(r) for r in session.query(model).filter(column == user_id)]

    def erase(session: Session, user_id: uuid.UUID) -> int:
        n = session.query(model).filter(column == user_id).delete(synchronize_session=False)
        session.flush()
        return n

    return DataType(type_id, title, owner, purpose, retention_days, read, erase)


def _profile_type() -> DataType:
    def read(session: Session, user_id: uuid.UUID) -> Records:
        user = session.get(User, user_id)
        return [{"profile": user.profile or {}}] if user else []

    def erase(session: Session, user_id: uuid.UUID) -> int:
        user = session.get(User, user_id)
        if user is None or not user.profile:
            return 0
        user.profile = {}
        session.flush()
        return 1

    return DataType(
        "profile",
        "Профиль и предмет",
        "core",
        "Выбор предмета, цели и способов изучения",
        None,
        read,
        erase,
    )


def core_types() -> list[DataType]:
    return [
        _profile_type(),
        model_type(Activity, "activity", "Задания", "core", "Задания, выданные человеку", None),
        model_type(Response, "response", "Ответы и оценки", "core", "Ответы и их разбор", None),
        model_type(
            SrsCard, "srs_card", "Карточки повторения", "core", "Расписание повторений", None
        ),
        model_type(Job, "job", "Фоновые задачи", "core", "Оценка и разбор ответов", 30),
        model_type(
            Material, "material", "Загруженные материалы", "core", "Материалы человека", None
        ),
    ]


def types(modules: list[Any]) -> dict[str, DataType]:
    """Реестр: типы ядра плюс типы, которые объявили включённые модули."""
    registry = {t.id: t for t in core_types()}
    for module in modules:
        for t in module.data_types():
            if t.id in registry:
                raise ValueError(f"Тип данных «{t.id}» объявлен дважды")
            registry[t.id] = t
    return registry


# ---- разрешения ----


def _default_granted(module: Any) -> bool:
    return bool(getattr(module, "first_party", False))


def is_allowed(
    session: Session, module: Any, user_id: uuid.UUID, type_id: str, mode: str
) -> tuple[bool, str]:
    """Пустят ли модуль: (да/нет, причина). Объявление в манифесте обязательно."""
    if f"data.{type_id}" not in module.manifest.requires:
        return False, "not_declared"
    row = session.get(DataPermission, (user_id, module.id, type_id, mode))
    if row is not None:
        return (True, "granted") if row.granted else (False, "revoked")
    return (True, "default") if _default_granted(module) else (False, "not_granted")


def _log(
    session: Session,
    user_id: uuid.UUID,
    module_id: str,
    type_id: str,
    mode: str,
    purpose: str,
    allowed: bool,
    reason: str,
) -> None:
    session.add(
        DataAccessLog(
            user_id=user_id,
            module_id=module_id,
            data_type=type_id,
            mode=mode,
            purpose=purpose[:200],
            allowed=allowed,
            reason=reason,
        )
    )
    session.flush()


def _gate(
    session: Session,
    module: Any,
    user_id: uuid.UUID,
    type_id: str,
    mode: str,
    purpose: str,
    registry: dict[str, DataType],
) -> DataType:
    data_type = registry.get(type_id)
    if data_type is None:
        raise DataAccessError("unknown_type", f"Неизвестный тип данных «{type_id}»")
    allowed, reason = is_allowed(session, module, user_id, type_id, mode)
    # Попытку пишем в журнал в любом случае: отказ человеку тоже интересен.
    _log(session, user_id, module.id, type_id, mode, purpose, allowed, reason)
    if not allowed:
        raise DataAccessError(
            reason, f"Модулю «{module.id}» не разрешено: {mode} {type_id} ({reason})"
        )
    return data_type


def read(
    session: Session,
    module: Any,
    user_id: uuid.UUID,
    type_id: str,
    purpose: str,
    registry: dict[str, DataType],
) -> Records:
    """Данные этого человека для модуля — только если разрешено; доступ пишется в журнал."""
    return _gate(session, module, user_id, type_id, READ, purpose, registry).read(session, user_id)


def write(
    session: Session,
    module: Any,
    user_id: uuid.UUID,
    type_id: str,
    purpose: str,
    records: Records,
    registry: dict[str, DataType],
) -> int:
    data_type = _gate(session, module, user_id, type_id, WRITE, purpose, registry)
    if data_type.write is None:
        raise DataAccessError("read_only", f"Тип «{type_id}» только для чтения")
    return data_type.write(session, user_id, records)


def set_permission(
    session: Session, user_id: uuid.UUID, module: Any, type_id: str, mode: str, granted: bool
) -> None:
    """Решение человека. Выдать можно только то, что модуль объявил в манифесте."""
    if mode not in MODES:
        raise DataAccessError("bad_mode", "Режим — read или write")
    if f"data.{type_id}" not in module.manifest.requires:
        raise DataAccessError("not_declared", f"Модуль «{module.id}» этот тип данных не запрашивал")
    row = session.get(DataPermission, (user_id, module.id, type_id, mode))
    if row is None:
        session.add(
            DataPermission(
                user_id=user_id, module_id=module.id, data_type=type_id, mode=mode, granted=granted
            )
        )
    else:
        row.granted = granted
    session.flush()


def permissions(session: Session, user_id: uuid.UUID, modules: list[Any]) -> list[dict[str, Any]]:
    """Что каждый модуль запрашивает и как это решено — для экрана «Доступ модулей»."""
    out = []
    for module in modules:
        wanted = sorted(r[5:] for r in module.manifest.requires if r.startswith("data."))
        out.append(
            {
                "module": module.id,
                "title": module.manifest.title,
                "firstParty": bool(getattr(module, "first_party", False)),
                "data": [
                    {
                        "type": t,
                        **{m: is_allowed(session, module, user_id, t, m)[0] for m in MODES},
                    }
                    for t in wanted
                ],
            }
        )
    return out


def access_log(session: Session, user_id: uuid.UUID, limit: int = 100) -> list[dict[str, Any]]:
    rows = (
        session.query(DataAccessLog)
        .filter(DataAccessLog.user_id == user_id)
        .order_by(DataAccessLog.at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "module": r.module_id,
            "type": r.data_type,
            "mode": r.mode,
            "purpose": r.purpose,
            "allowed": r.allowed,
            "reason": r.reason,
            "at": r.at.isoformat(),
        }
        for r in rows
    ]


# ---- выгрузка и удаление ----


def export_all(
    session: Session, user_id: uuid.UUID, registry: dict[str, DataType]
) -> dict[str, Any]:
    """Все данные человека по реестру типов — одним документом."""
    return {
        "userId": str(user_id),
        "exportedAt": datetime.now().astimezone().isoformat(),
        "data": {t.id: t.read(session, user_id) for t in registry.values()},
    }


def erase_all(
    session: Session, user_id: uuid.UUID, registry: dict[str, DataType]
) -> dict[str, int]:
    """Удалить данные человека по каждому типу реестра, его разрешения и журнал доступа.

    Порядок: зависимые типы раньше тех, на которые они ссылаются (ответы — до заданий).
    """
    order = ["response", "job", "srs_card", "activity", "material", "profile"]
    ids = [i for i in order if i in registry] + [i for i in registry if i not in order]
    counts = {i: registry[i].erase(session, user_id) for i in ids}
    session.query(DataPermission).filter(DataPermission.user_id == user_id).delete(
        synchronize_session=False
    )
    session.query(DataAccessLog).filter(DataAccessLog.user_id == user_id).delete(
        synchronize_session=False
    )
    session.flush()
    return counts
