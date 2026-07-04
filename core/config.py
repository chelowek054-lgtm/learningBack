"""Конфигурация из окружения (pydantic-settings). Секреты — ТОЛЬКО отсюда."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    # Внутри docker-compose host = postgres; локально = localhost.
    database_url: str = "postgresql+psycopg://praxis:praxis@localhost:5432/praxis"
    # Используется только backend'ом (инвариант №2). Пусто → MockAIGateway.
    claude_api_key: str = ""

    # JWT (свой auth). Секрет — из окружения; дефолт только для dev.
    jwt_secret: str = "dev-insecure-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 30  # 30 дней (MVP «для себя»)

    # Модели Claude (сверять актуальность через skill claude-api).
    model_scoring: str = "claude-opus-4-8"
    model_generation: str = "claude-sonnet-4-6"


settings = Settings()
