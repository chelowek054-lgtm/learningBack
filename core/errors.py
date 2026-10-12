"""Ошибки API с кодом и параметрами: текст выбирает клиент на языке человека (R-0081, T-0179).

`detail` остаётся строкой (по-русски) для прежних клиентов и логов; новые клиенты берут `code` и `params`.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException


class ApiError(HTTPException):
    def __init__(
        self, status_code: int, code: str, message: str, params: dict[str, Any] | None = None
    ) -> None:
        super().__init__(
            status_code, detail={"message": message, "code": code, "params": params or {}}
        )
