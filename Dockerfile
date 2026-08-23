# Praxis backend. uv-образ для установки зависимостей.
FROM python:3.12-slim

# uv ставим из PyPI, а не из ghcr.io/astral-sh/uv: реестр GitHub из демона Docker
# в этой сети недоступен (TLS handshake timeout), тогда как PyPI доступен —
# через него и так тянутся зависимости проекта.
RUN pip install --no-cache-dir "uv==0.11.*"

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Слой зависимостей (кэшируется, пока не меняются pyproject/uv.lock).
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen

# Код приложения.
COPY . .

EXPOSE 8000
CMD ["uv", "run", "--no-dev", "uvicorn", "core.app:app", "--host", "0.0.0.0", "--port", "8000"]
