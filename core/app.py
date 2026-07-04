"""FastAPI-приложение Praxis. Ф0 — health + роутеры-заглушки, без вызовов LLM."""

from fastapi import FastAPI

from core.config import settings
from core.routers import auth, content, jobs, sync

app = FastAPI(title="Praxis API", version="0.0.0")

app.include_router(sync.router)
app.include_router(jobs.router)
app.include_router(content.router)
app.include_router(auth.router)


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok", "version": app.version, "env": settings.app_env}
