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


app = FastAPI(title="Praxis API", version="0.0.0", lifespan=lifespan)

# CORS: нужен для web-клиента (Expo web на :8081). Bearer-токен в заголовке, не cookie,
# поэтому allow_credentials=False + wildcard допустимы.
_origins = (
    ["*"]
    if settings.cors_origins.strip() == "*"
    else [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
)
app.add_middleware(UserContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(sync.router)
app.include_router(jobs.router)
app.include_router(content.router)
app.include_router(auth.router)
app.include_router(usage_router.router)
for module_router in modules.routers():
    app.include_router(module_router)

# Админка на /admin (вход — только is_superuser; см. scripts/createsuperuser.py).
setup_admin(app)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "version": app.version, "env": settings.app_env}
