"""SQLAlchemy engine/session и декларативная база. Движок ленив — соединение
устанавливается при первом запросе, поэтому импорт (и /health) БД не трогают."""

import uuid
from collections.abc import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from core.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    """Общая база ORM-моделей (ядро + модули: одна metadata, одна миграционная линия)."""


def uuid_pk() -> Mapped[uuid.UUID]:
    # Фабрика: каждый вызов — НОВЫЙ столбец (один объект нельзя делить между таблицами).
    return mapped_column(
        UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()")
    )


TS = TIMESTAMP(timezone=True)


def get_session() -> Iterator[Session]:
    """FastAPI-зависимость: сессия на запрос."""
    with SessionLocal() as session:
        yield session
