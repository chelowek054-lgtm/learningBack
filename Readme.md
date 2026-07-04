# learningBack

Backend Praxis — FastAPI (AI-оркестрация, sync, схема БД). Связан с клиентом только по HTTP.
Архитектура: [../docs/architecture](../docs/architecture/README.md) · план: [Фаза 0](../docs/plans/phase-0-foundation.md) (WS2, WS3).

## Стек
Python 3.12 · FastAPI · SQLAlchemy 2 · Alembic · Postgres · пакетный менеджер **uv**.

## Структура
```
core/
  app.py         FastAPI (/health + роутеры)
  config.py      pydantic-settings (из .env)
  db.py          engine/session, Base
  models.py      7 таблиц (02-logical §2.1)
  routers/       sync, jobs, content, auth (заглушки Ф0)
  ai_gateway/    контракт вызова LLM (без реализации в Ф0)
modules/         languages, ml (заглушки контрактов)
migrations/      Alembic (0001_init)
scripts/seed.py  seed-заглушка
```

## Запуск через Docker (рекомендуется)
Оркестрация — в **корне суперпроекта** (`../docker-compose.yml`), данные — в `../.data`.
```bash
cd ..                          # в корень
cp .env.example .env
docker compose up --build      # Postgres + миграции + API
# API: http://localhost:8000/docs  ·  health: http://localhost:8000/health
```

## Локальный запуск (без Docker для API)
```bash
uv sync
# нужен доступный Postgres — поднять только его из корня: (cd .. && docker compose up -d postgres)
uv run alembic upgrade head
uv run python -m scripts.seed
uv run uvicorn core.app:app --reload
```

## Миграции
```bash
uv run alembic upgrade head          # применить
uv run alembic downgrade base        # откатить
uv run alembic upgrade head --sql    # offline: показать DDL без подключения
```
