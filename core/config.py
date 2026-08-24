"""Конфигурация из окружения (pydantic-settings). Секреты — ТОЛЬКО отсюда."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"
    # Внутри docker-compose host = postgres; локально = localhost.
    database_url: str = "postgresql+psycopg://praxis:praxis@localhost:5432/praxis"
    # Используется только backend'ом (инвариант №2). Пусто → MockAIGateway.
    claude_api_key: str = ""

    # LLM по OpenAI-совместимому протоколу (chat/completions + tool calling).
    # Провайдер задаётся адресом и слагом модели, а не отдельной реализацией:
    # OpenRouter, RouterAI и им подобные говорят на одном протоколе.
    llm_api_key: str = ""
    llm_base_url: str = "https://routerai.ru/api/v1"
    llm_model_generation: str = "deepseek/deepseek-v4-flash-0731"
    llm_model_scoring: str = "deepseek/deepseek-v4-flash-0731"
    # max_tokens ОБЯЗАТЕЛЕН: без явного лимита провайдер резервирует потолок
    # контекста модели и может отклонить запрос как неоплачиваемый.
    llm_max_tokens: int = 16384
    llm_timeout_seconds: float = 120.0
    # Необязательные заголовки атрибуции (их читает OpenRouter; другие игнорируют).
    llm_site_url: str = ""
    llm_site_title: str = "Praxis"

    # Совместимость с прежними .env.
    openrouter_api_key: str = ""

    @property
    def effective_llm_key(self) -> str:
        """Ключ провайдера.

        Старый OPENROUTER_API_KEY подхватывается ТОЛЬКО если адрес по-прежнему
        указывает на OpenRouter: иначе ключ одного сервиса ушёл бы в другой и
        отказ выглядел бы как «неверный ключ», а не как ошибка настройки.
        """
        if self.llm_api_key:
            return self.llm_api_key
        return self.openrouter_api_key if "openrouter.ai" in self.llm_base_url else ""

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
