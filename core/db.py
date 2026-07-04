"""SQLAlchemy engine/session и декларативная база. Движок ленив — соединение
устанавливается при первом запросе, поэтому импорт (и /health) БД не трогают."""

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from core.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    """Общая база ORM-моделей."""


def get_session() -> Iterator[Session]:
    """FastAPI-зависимость: сессия на запрос."""
    with SessionLocal() as session:
        yield session
