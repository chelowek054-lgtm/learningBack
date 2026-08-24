"""Конфигурация из окружения (pydantic-settings). Секреты — ТОЛЬКО отсюда.

Единственный источник правды — `.env` в корне суперпроекта: его же читает
docker-compose. Отдельного `learningBack/.env` намеренно нет — две копии одних
и тех же переменных расходились молча. Реальные переменные окружения (то, что
подставляет compose) приоритетнее файла, поэтому в контейнере всё работает
без него.
"""

from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# learningBack/core/config.py → learningBack → корень суперпроекта.
# В образе корня нет: файла не окажется, и настройки придут из окружения.
ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_ENV, extra="ignore")

    app_env: str = "development"

    # Postgres. Один набор на всех: compose поднимает контейнер из этих же
    # значений, поэтому DATABASE_URL отдельно задавать не нужно — он собирается
    # ниже. Внутри compose host подменяется на postgres через переменную.
    postgres_user: str = "praxis"
    postgres_password: str = "praxis"
    postgres_db: str = "praxis"
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    # Пусто → собирается из POSTGRES_*. Compose передаёт готовый URL явно.
    database_url: str = ""

    @model_validator(mode="after")
    def _compose_database_url(self) -> "Settings":
        if not self.database_url:
            self.database_url = (
                f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
            )
        return self

    # LLM по OpenAI-совместимому протоколу (chat/completions + tool calling).
    # Провайдер задаётся адресом и слагом модели, а не отдельной реализацией:
    # подойдёт любой сервис, говорящий на этом протоколе. Сейчас — RouterAI.
    # Ключ используется только backend'ом (инвариант №2). Пусто → MockAIGateway.
    llm_api_key: str = ""
    llm_base_url: str = "https://routerai.ru/api/v1"
    llm_model_generation: str = "deepseek/deepseek-v4-flash-0731"
    llm_model_scoring: str = "deepseek/deepseek-v4-flash-0731"
    # max_tokens ОБЯЗАТЕЛЕН: без явного лимита провайдер резервирует потолок
    # контекста модели и может отклонить запрос как неоплачиваемый.
    llm_max_tokens: int = 16384
    llm_timeout_seconds: float = 120.0
    # Необязательные заголовки атрибуции: часть провайдеров их читает,
    # остальные игнорируют.
    llm_site_url: str = ""
    llm_site_title: str = "Praxis"

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


settings = Settings()
