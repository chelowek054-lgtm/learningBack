"""Общая обвязка тестов.

Тесты идут против ОТДЕЛЬНОЙ базы (`<db>_test`), а не рабочей: модели используют
JSONB/UUID, так что SQLite не подходит, а ронять данные стенда нельзя. База
создаётся один раз за сессию, схема — из общей metadata; каждый тест работает
внутри транзакции, которая откатывается, поэтому тесты не видят друг друга.

Запуск (Postgres стенда должен быть поднят):
    cd learningBack && uv run pytest
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import psycopg
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from core.app import app
from core.config import settings
from core.db import Base, get_session
from core.deps import get_current_user
from core.models import User

# Импорт ради регистрации таблиц модуля в общей metadata.
import modules.knowledge.models  # noqa: F401

TEST_DB_SUFFIX = "_test"


@pytest.fixture(autouse=True, scope="session")
def _no_live_llm() -> Iterator[None]:
    """Тесты не ходят к провайдеру, даже когда ключ настроен.

    Раньше это выходило само собой: своего `.env` у backend'а не было, прогон шёл
    без ключа и гейтвей отдавал заглушки. После переезда на общий `.env` ключ
    виден и тестам — прогон начал стучаться в живую модель, стал медленным,
    сетезависимым и платным. Герметичность должна держаться явно, а не тем,
    что файла нет.

    Тестам про сам выбор провайдера это не мешает: они подменяют ключ через
    monkeypatch и восстанавливают его сами.
    """
    saved = settings.llm_api_key
    settings.llm_api_key = ""
    try:
        yield
    finally:
        settings.llm_api_key = saved


def _test_database_url() -> str:
    base, _, name = settings.database_url.rpartition("/")
    return f"{base}/{name}{TEST_DB_SUFFIX}"


def _ensure_database(url: str) -> None:
    """CREATE DATABASE, если её ещё нет (подключаемся к служебной `postgres`)."""
    base, _, name = url.rpartition("/")
    admin_dsn = f"{base}/postgres".replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        exists = conn.execute("select 1 from pg_database where datname = %s", (name,)).fetchone()
        if not exists:
            conn.execute(f'CREATE DATABASE "{name}"')


@pytest.fixture(scope="session")
def engine():
    url = _test_database_url()
    _ensure_database(url)
    eng = create_engine(url, pool_pre_ping=True, future=True)
    # Пересоздаём схему целиком: create_all не эволюционирует существующие таблицы,
    # и добавленная в модель колонка молча не появлялась бы в тестовой базе.
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine) -> Iterator[Session]:
    """Сессия в транзакции, которая всегда откатывается."""
    connection = engine.connect()
    transaction = connection.begin()
    factory = sessionmaker(bind=connection, autoflush=False, autocommit=False)
    db = factory()
    try:
        yield db
    finally:
        db.close()
        transaction.rollback()
        connection.close()


def make_user(session: Session, *, superuser: bool = False) -> User:
    user = User(
        email=f"{uuid.uuid4().hex[:10]}@example.com",
        password_hash="x",
        is_superuser=superuser,
        profile={},
    )
    session.add(user)
    session.flush()
    return user


@pytest.fixture
def client(session: Session):
    """TestClient поверх той же транзакции; аутентификацию подменяем фикстурой."""

    def _client_for(user: User) -> TestClient:
        app.dependency_overrides[get_session] = lambda: session
        app.dependency_overrides[get_current_user] = lambda: user
        return TestClient(app)

    yield _client_for
    app.dependency_overrides.clear()
