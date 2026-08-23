"""Конфигурация из окружения (pydantic-settings). Секреты — ТОЛЬКО отсюда."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    # Внутри docker-compose host = postgres; локально = localhost.
    database_url: str = "postgresql+psycopg://praxis:praxis@localhost:5432/praxis"
    # Используется только backend'ом (инвариант №2). Пусто → MockAIGateway.
    claude_api_key: str = ""

    # OpenRouter — OpenAI-совместимый шлюз к множеству моделей. Если задан ключ,
    # он выигрывает у claude_api_key: это осознанный выбор провайдера, а не запасной путь.
    openrouter_api_key: str = ""
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # Слаги OpenRouter (не путать с именами моделей Anthropic ниже).
    openrouter_model_scoring: str = "google/gemini-3.7-flash"
    openrouter_model_generation: str = "google/gemini-3.7-flash"
    # ОБЯЗАТЕЛЕН: без явного лимита OpenRouter резервирует потолок контекста модели
    # (65536) и отклоняет запрос как неоплачиваемый.
    openrouter_max_tokens: int = 16384
    openrouter_timeout_seconds: float = 120.0
    # Необязательные заголовки атрибуции OpenRouter.
    openrouter_site_url: str = ""
    openrouter_site_title: str = "Praxis"

    # CORS: список origin через запятую, или "*" (для web-клиента Expo на :8081).
    cors_origins: str = "*"

    # JWT (свой auth). Секрет — из окружения; дефолт только для dev.
    jwt_secret: str = "dev-insecure-change-me"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 30  # 30 дней (MVP «для себя»)

    # Админка (sqladmin): секрет cookie-сессии. Пусто → берётся jwt_secret.
    admin_session_secret: str = ""

    # Восстановление пароля: 8-значный числовой код в таблице password_reset_code.
    # Доставки (почта/SMS) пока нет — код читается из БД (pgAdmin). См. ROADMAP.
    password_reset_code_ttl_minutes: int = 15
    password_reset_max_attempts: int = 5

    # Модели Claude (сверять актуальность через skill claude-api).
    model_scoring: str = "claude-opus-4-8"
    model_generation: str = "claude-sonnet-4-6"


settings = Settings()
