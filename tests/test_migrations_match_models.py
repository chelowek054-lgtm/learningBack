"""Миграции дают ту же схему, что и модели: тесты строят базу из моделей, а продакшен — из миграций.

Расхождение здесь тихое: все тесты зелёные, а на настоящей базе вставка падает. Так случилось с
таблицами происхождения (T-0076): у `id` в миграции не было `gen_random_uuid()`.
"""

from __future__ import annotations

import os
import subprocess
import sys

import psycopg
import pytest
from sqlalchemy import create_engine, inspect

from core.config import settings
from core.db import Base

import core.models  # noqa: F401  # регистрация таблиц
import modules.knowledge.models  # noqa: F401

SCRATCH = "praxis_migrations_check"


@pytest.fixture(scope="module")
def engine(migrated_url):
    eng = create_engine(migrated_url)
    yield eng
    eng.dispose()  # иначе базу не удалить: на ней висит соединение


@pytest.fixture(scope="module")
def migrated_url():
    base, _, _ = settings.database_url.rpartition("/")
    url = f"{base}/{SCRATCH}"
    admin = f"{base}/postgres".replace("postgresql+psycopg://", "postgresql://")
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{SCRATCH}"')
        conn.execute(f'CREATE DATABASE "{SCRATCH}"')
    env = {**os.environ, "DATABASE_URL": url}
    done = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if done.returncode != 0:
        pytest.fail(f"alembic upgrade head не прошёл:\n{done.stderr[-1500:]}")
    yield url
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{SCRATCH}"')


def test_every_table_and_column_of_the_models_exists_after_migrations(engine):
    insp = inspect(engine)
    have = set(insp.get_table_names())
    missing_tables = [t for t in Base.metadata.tables if t not in have]
    assert missing_tables == []
    missing_cols = [
        f"{t.name}.{c.name}"
        for t in Base.metadata.sorted_tables
        for c in t.columns
        if c.name not in {col["name"] for col in insp.get_columns(t.name)}
    ]
    assert missing_cols == []


def test_primary_keys_with_a_server_default_have_it_in_the_migrated_database(engine):
    insp = inspect(engine)
    lost = []
    for table in Base.metadata.sorted_tables:
        real = {c["name"]: c for c in insp.get_columns(table.name)}
        for col in table.primary_key.columns:
            if col.server_default is not None and real[col.name]["default"] is None:
                lost.append(f"{table.name}.{col.name}")
    assert lost == []
