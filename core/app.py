"""FastAPI-приложение Praxis. Ф0 — health + роутеры-заглушки, без вызовов LLM."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.admin import setup_admin
from core import modules
from core.config import settings
from core.db import SessionLocal
from core.usage import UserContextMiddleware
from core.versioning import ClientVersionMiddleware, version_info
from core.routers import auth, content, jobs, sync
from core.routers import usage as usage_router

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """При старте добавить недостающие рубрики модулей (AC-04.8).

    Без БД API всё равно стартует: /health не должен от неё зависеть.
    """
    try:
        with SessionLocal() as session:
            added = modules.sync_rubrics(session)
            session.commit()
        if added:
            log.info("Добавлено рубрик: %s", added)
    except Exception:  # noqa: BLE001
        log.warning("Не удалось синхронизировать рубрики при старте", exc_info=True)
    yield


app = FastAPI(title="Praxis API", version=settings.app_version, lifespan=lifespan)

# CORS: нужен для web-клиента (Expo web на :8081). Bearer-токен в заголовке, не cookie,
# поэтому allow_credentials=False + wildcard допустимы.
_origins = (
    ["*"]
    if settings.cors_origins.strip() == "*"
    else [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
)
app.add_middleware(UserContextMiddleware)
# Версию клиента проверяем внутри CORS: ответ 426 должен дойти до браузера читаемым.
app.add_middleware(ClientVersionMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# API живёт под /v1 (T-0033). Прежние пути без префикса остаются для уже установленных
# клиентов, но в схему не попадают: новый код ходит только на /v1.
_routers = [
    sync.router,
    jobs.router,
    content.router,
    auth.router,
    usage_router.router,
    *modules.routers(),
]
for _router in _routers:
    app.include_router(_router, prefix="/v1")
    app.include_router(_router, include_in_schema=False)

# Админка на /admin (вход — только is_superuser; см. scripts/createsuperuser.py).
setup_admin(app)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "version": app.version, "env": settings.app_env}


@app.get("/v1/version", tags=["meta"])
@app.get("/version", include_in_schema=False)
def version() -> dict:
    """Версия API и минимальная версия клиента: приложение сверяется с ней при старте."""
    return version_info()
