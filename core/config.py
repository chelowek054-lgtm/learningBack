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

# Окружения, где запуск с dev-умолчаниями недопустим.
DEPLOYED_ENVS = frozenset({"staging", "production", "prod"})
DEV_JWT_SECRET = "dev-insecure-change-me"
DEV_POSTGRES_PASSWORD = "praxis"
MIN_SECRET_LENGTH = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT_ENV, extra="ignore")

    app_env: str = "development"

    # Версия сервера и минимальная версия клиента (T-0033). Клиенты ниже минимума получают
    # 426 с просьбой обновиться; поднимается при несовместимом изменении протокола.
    app_version: str = "0.0.0"
    min_client_version: str = "0.0.0"

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

    @property
    def is_deployed(self) -> bool:
        """staging/production: здесь dev-умолчания недопустимы и секреты не пишутся в лог."""
        return self.app_env.lower() in DEPLOYED_ENVS

    @model_validator(mode="after")
    def _forbid_dev_secrets(self) -> "Settings":
        """Сервер не стартует в staging/production с dev-секретами (T-0032).

        Проверка идёт до сборки `database_url`: пароль БД важен, только если URL
        собирается из POSTGRES_*, а не задан compose'ом целиком.
        """
        if not self.is_deployed:
            return self
        problems: list[str] = []
        if self.jwt_secret == DEV_JWT_SECRET:
            problems.append("JWT_SECRET: dev-значение по умолчанию")
        elif len(self.jwt_secret) < MIN_SECRET_LENGTH:
            problems.append(f"JWT_SECRET: короче {MIN_SECRET_LENGTH} символов")
        if not self.database_url and self.postgres_password == DEV_POSTGRES_PASSWORD:
            problems.append("POSTGRES_PASSWORD: dev-значение по умолчанию")
        if problems:
            raise ValueError(
                f"APP_ENV={self.app_env}: небезопасные секреты — " + "; ".join(problems)
            )
        return self

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

    # Озвучка материалов (порт core.tts). Пустая модель → заглушка; файлы лежат в audio_dir.
    tts_model: str = ""
    tts_voice: str = "alloy"
    audio_dir: str = ".data/audio"

    # Подключённые предметные модули (ADR-0019). Ядро про них ничего не знает:
    # каждый пункт — пакет с объектом `backend` (наследник core.modules.BackendModule).
    installed_modules: str = (
        "modules.languages,modules.ml,modules.knowledge,modules.srs,modules.mnemonic"
    )

    # CORS: список origin через запятую, или "*" (для web-клиента Expo на :8081).
    cors_origins: str = "*"

    # JWT (свой auth). Секрет — из окружения; дефолт только для dev.
    jwt_secret: str = DEV_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 30  # 30 дней (MVP «для себя»)

    # Пороги алертов мониторинга (T-0050). Окно общее; 0 у лимита выключает соответствующий алерт.
    alert_window_hours: int = 24
    # Доля упавших среди завершённых задач и минимум задач, начиная с которого доля что-то значит.
    alert_failed_jobs_ratio: float = 0.2
    alert_min_jobs: int = 5
    alert_tokens_per_window: int = 0
    alert_client_errors: int = 0

    # Повтор AI-задач при временных сбоях: сколько попыток и базовая отсрочка
    # (растёт вдвое с каждой неудачей).
    job_max_attempts: int = 3
    # inline — задачи исполняются на /sync/push (как раньше); worker — их берёт отдельный процесс (scripts/worker.py).
    jobs_mode: str = "inline"
    # Через сколько минут задачу в running считаем брошенной (воркер упал) и возвращаем в очередь.
    job_stale_minutes: int = 15
    job_retry_backoff_seconds: int = 60

    # Админка (sqladmin): секрет cookie-сессии. Пусто → берётся jwt_secret.
    admin_session_secret: str = ""

    # Восстановление пароля: 8-значный числовой код в таблице password_reset_code.
    # Доставки (почта/SMS) пока нет — код читается из БД (pgAdmin). См. ROADMAP.
    password_reset_code_ttl_minutes: int = 15
    # Версия политики данных, с которой соглашается человек при регистрации (R-0018).
    policy_version: str = "1.0"
    password_reset_max_attempts: int = 5
    # Запросы кода: не больше N на email и на IP за окно (защита от заспамливания
    # почты и от перебора адресов). Ответ при превышении — 429.
    password_reset_request_limit: int = 5
    password_reset_request_window_seconds: int = 900


settings = Settings()
