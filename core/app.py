"""FastAPI-приложение Praxis. Ф0 — health + роутеры-заглушки, без вызовов LLM."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import settings
from core.routers import auth, content, jobs, sync
from modules.knowledge import router as knowledge_router

app = FastAPI(title="Praxis API", version="0.0.0")

# CORS: нужен для web-клиента (Expo web на :8081). Bearer-токен в заголовке, не cookie,
# поэтому allow_credentials=False + wildcard допустимы.
_origins = (
    ["*"]
    if settings.cors_origins.strip() == "*"
    else [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
)
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
app.include_router(knowledge_router)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "version": app.version, "env": settings.app_env}
