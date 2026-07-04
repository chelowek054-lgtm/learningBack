"""ORM-модели Praxis (7 таблиц). См. docs/architecture/02-logical.md §2.1.

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
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.db import Base


def _uuid_pk() -> Mapped[uuid.UUID]:
    # Фабрика: каждый вызов — НОВЫЙ столбец (один объект нельзя делить между таблицами).
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )


_ts = TIMESTAMP(timezone=True)


class User(Base):
    __tablename__ = "user"

    id: Mapped[uuid.UUID] = _uuid_pk()
    email: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(_ts, server_default=func.now())
    profile: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))


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
    fsrs_state: Mapped[dict] = mapped_column(JSONB, nullable=False)
    due_at: Mapped[datetime] = mapped_column(_ts, nullable=False)
    created_at: Mapped[datetime] = mapped_column(_ts, server_default=func.now())

    __table_args__ = (Index("idx_srs_user_due", "user_id", "due_at"),)


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
