"""Версия API и проверка совместимости клиента (T-0033, R-0020).

API живёт под префиксом `/v1`: несовместимое изменение выходит как `/v2` рядом, а не
ломает установленные приложения. Клиент сообщает свою версию заголовком
`X-Client-Version`; если она ниже минимально поддерживаемой, сервер отвечает 426 с
понятным кодом, и приложение просит обновиться вместо непонятной ошибки.

Заголовка нет — запрос пропускается: так работают web-превью и сборки, выпущенные до
введения проверки (их отсекает префикс, а не версия).
"""

from __future__ import annotations

import json
import re

from core.config import settings

API_VERSION = "1"
# Эти пути проверяются всегда доступными: клиент должен суметь узнать версию и увидеть сообщение.
EXEMPT_PREFIXES = (
    "/health",
    "/admin",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/version",
    "/v1/version",
)


def parse_version(text: str) -> tuple[int, int, int] | None:
    """`1.2.3`, `1.2`, `1.2.3-beta.4` → (1, 2, 3); непонятное → None."""
    m = re.match(r"^\s*v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", text or "")
    if not m:
        return None
    return int(m.group(1)), int(m.group(2) or 0), int(m.group(3) or 0)


def is_outdated(client_version: str, minimum: str) -> bool:
    """Клиент старше минимума. Непонятная версия клиента не считается устаревшей."""
    client, floor = parse_version(client_version), parse_version(minimum)
    if client is None or floor is None:
        return False
    return client < floor


def version_info() -> dict:
    return {
        "apiVersion": API_VERSION,
        "serverVersion": settings.app_version,
        "minClientVersion": settings.min_client_version,
    }


class ClientVersionMiddleware:
    """ASGI: помечает ответы версией API и отсекает устаревших клиентов кодом 426."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        headers = {k.lower(): v for k, v in scope.get("headers", [])}
        client_version = headers.get(b"x-client-version", b"").decode("latin-1")

        exempt = path.startswith(EXEMPT_PREFIXES) or scope.get("method") == "OPTIONS"
        if (
            client_version
            and not exempt
            and is_outdated(client_version, settings.min_client_version)
        ):
            body = json.dumps(
                {
                    "code": "client_outdated",
                    "detail": "Версия приложения устарела: обновите его, чтобы продолжить.",
                    "minClientVersion": settings.min_client_version,
                },
                ensure_ascii=False,
            ).encode()
            await send(
                {
                    "type": "http.response.start",
                    "status": 426,
                    "headers": [
                        (b"content-type", b"application/json; charset=utf-8"),
                        (b"content-length", str(len(body)).encode()),
                        (b"x-api-version", API_VERSION.encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        async def send_with_version(message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", [])
                message["headers"] = [*message["headers"], (b"x-api-version", API_VERSION.encode())]
            await send(message)

        await self.app(scope, receive, send_with_version)
