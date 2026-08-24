"""ORM-модели ядра Praxis (8 таблиц). См. docs/architecture/02-logical.md §2.1.

Модель знаний (граф) — НЕ здесь: она данные модуля, см. modules/knowledge/models.py.

Оси разделения — это КОЛОНКИ: `module` (тема) и `connectivity` (online/offline),
а не отдельные схемы. Единый event log — таблица `response` (инвариант №4).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.db import TS as _ts
from core.db import Base, uuid_pk as _uuid_pk


class User(Base):
    __tablename__ = "user"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    # Доступ в админку и курирование канона. Выдаётся только через scripts/createsuperuser.
    is_superuser: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    created_at: Mapped[datetime] = mapped_column(_ts, server_default=func.now())
    profile: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


class PasswordResetCode(Base):
    """Временный код восстановления пароля, привязанный к пользователю.

    Код хранится в открытом виде: доставки (почта/SMS) ещё нет, и читать его
    предполагается из БД через pgAdmin. При появлении доставки — хешировать.
    """

    __tablename__ = "password_reset_code"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(8), nullable=False)  # 8 цифр, ведущие нули значимы
    expires_at: Mapped[datetime] = mapped_column(_ts, nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(_ts, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(_ts, server_default=func.now())

    __table_args__ = (Index("idx_password_reset_user", "user_id", "expires_at"),)


class Activity(Base):
    __tablename__ = "activity"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False)
    module: Mapped[str] = mapped_column(String, nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    connectivity: Mapped[str] = mapped_column(String, nullable=False)  # 'offline' | 'online'
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(_ts, server_default=func.now())
    due_at: Mapped[datetime | None] = mapped_column(_ts, nullable=True)

    __table_args__ = (Index("idx_activity_user_module_type", "user_id", "module", "type"),)


class Response(Base):
    """Единый event log ответов (растёт офлайн, синкается наверх)."""

    __tablename__ = "response"

    id: Mapped[uuid.UUID] = _uuid_pk()
    activity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("activity.id"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False)
    user_answer: Mapped[dict] = mapped_column(JSONB, nullable=False)
    grade: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # null пока job pending
    local_created_at: Mapped[datetime] = mapped_column(_ts, nullable=False)
    synced: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    __table_args__ = (Index("idx_response_user_synced", "user_id", "synced"),)


class SrsCard(Base):
    __tablename__ = "srs_card"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False)
    module: Mapped[str] = mapped_column(String, nullable=False)
    front: Mapped[dict] = mapped_column(JSONB, nullable=False)
    back: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)  # error_log|awl|imported|generated
    # Узел графа, к которому относится карточка. Nullable: карточки Фазы 1
    # (AWL, ошибки из эссе) к графу не привязаны.
    concept_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    fsrs_state: Mapped[dict] = mapped_column(JSONB, nullable=False)
    due_at: Mapped[datetime] = mapped_column(_ts, nullable=False)
    created_at: Mapped[datetime] = mapped_column(_ts, server_default=func.now())

    __table_args__ = (
        Index("idx_srs_user_due", "user_id", "due_at"),
        Index("idx_srs_card_concept", "user_id", "concept_id"),
    )


class Job(Base):
    """Мост offline → online: отложенная AI-задача."""

    __tablename__ = "job"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user.id"), nullable=False)
    type: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)  # pending|running|done|failed
    input_ref: Mapped[dict] = mapped_column(JSONB, nullable=False)
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(_ts, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(_ts, server_default=func.now())

    __table_args__ = (Index("idx_job_user_status", "user_id", "status"),)


class Material(Base):
    __tablename__ = "material"

    id: Mapped[uuid.UUID] = _uuid_pk()
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("user.id"), nullable=True
    )  # null = общий
    module: Mapped[str] = mapped_column(String, nullable=False)
    source: Mapped[str] = mapped_column(String, nullable=False)  # pdf|note|generated|seed
    title: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(_ts, server_default=func.now())


class Rubric(Base):
    """Версионируемый промпт-оценщик (инвариант №6). PK = (id, version)."""

    __tablename__ = "rubric"

    id: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    module: Mapped[str] = mapped_column(String, nullable=False)
    model: Mapped[str] = mapped_column(String, nullable=False)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    schema: Mapped[dict] = mapped_column(JSONB, nullable=False)

    __table_args__ = (PrimaryKeyConstraint("id", "version"),)
