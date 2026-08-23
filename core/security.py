"""Безопасность: хеширование паролей (argon2) и JWT (свой auth, WS1)."""

import secrets
from datetime import datetime, timedelta, timezone

import jwt
from pwdlib import PasswordHash

from core.config import settings

_password_hash = PasswordHash.recommended()

RESET_CODE_LENGTH = 8


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return _password_hash.verify(password, hashed)


def generate_reset_code() -> str:
    """8-значный числовой код. Строкой — ведущие нули значимы."""
    return "".join(secrets.choice("0123456789") for _ in range(RESET_CODE_LENGTH))


def reset_code_expires_at(now: datetime) -> datetime:
    return now + timedelta(minutes=settings.password_reset_code_ttl_minutes)


def create_access_token(subject: str) -> str:
    """subject = user.id (str)."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.access_token_expire_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> str | None:
    """Вернуть subject (user.id) или None, если токен невалиден/просрочен."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        return None
    sub = payload.get("sub")
    return sub if isinstance(sub, str) else None
