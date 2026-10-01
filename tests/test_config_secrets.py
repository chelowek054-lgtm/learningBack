"""T-0032: сервер не стартует в staging/production с dev-секретами."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.config import DEV_JWT_SECRET, Settings

GOOD = "x" * 40


def make(**kw) -> Settings:
    # _env_file=None: тест не должен зависеть от .env разработчика.
    return Settings(_env_file=None, **kw)


@pytest.mark.parametrize("env", ["staging", "production", "prod", "Production"])
def test_default_jwt_secret_is_refused_when_deployed(env):
    with pytest.raises(ValidationError, match="JWT_SECRET"):
        make(app_env=env, postgres_password="strong-db-password")


def test_short_jwt_secret_is_refused_when_deployed():
    with pytest.raises(ValidationError, match="короче"):
        make(app_env="production", jwt_secret="short", postgres_password="strong-db-password")


def test_default_db_password_is_refused_when_url_is_composed():
    with pytest.raises(ValidationError, match="POSTGRES_PASSWORD"):
        make(app_env="staging", jwt_secret=GOOD)


def test_db_password_is_not_checked_when_url_is_given_explicitly():
    s = make(app_env="staging", jwt_secret=GOOD, database_url="postgresql+psycopg://u:p@db/x")
    assert s.database_url.endswith("/x")


def test_all_problems_are_reported_at_once():
    with pytest.raises(ValidationError) as e:
        make(app_env="production")
    assert "JWT_SECRET" in str(e.value) and "POSTGRES_PASSWORD" in str(e.value)


@pytest.mark.parametrize("env", ["development", "test", "ci"])
def test_dev_defaults_are_fine_outside_deployed_envs(env):
    assert make(app_env=env).jwt_secret == DEV_JWT_SECRET


def test_proper_secrets_pass_when_deployed():
    s = make(app_env="production", jwt_secret=GOOD, postgres_password="strong-db-password")
    assert s.is_deployed
