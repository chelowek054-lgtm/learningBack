"""Безопасность: хеширование паролей (argon2) и JWT (свой auth, WS1)."""

import hashlib
import hmac
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


def hash_reset_code(code: str) -> str:
    """HMAC-SHA256 кода с серверным секретом.

    Код короткий (10^8), поэтому голый хеш перебирается мгновенно; ключ делает
    перебор по утёкшей таблице бессмысленным без секрета.
    """
    return hmac.new(settings.jwt_secret.encode(), code.encode(), hashlib.sha256).hexdigest()


def reset_code_expires_at(now: datetime) -> datetime:
    return now + timedelta(minutes=settings.password_reset_code_ttl_minutes)


def create_access_token(subject: str, token_version: int = 0) -> str:
    """subject = user.id (str); token_version — версия сессий пользователя."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": subject,
        "tv": token_version,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.access_token_expire_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> tuple[str, int] | None:
    """Вернуть (user.id, версия сессий) или None, если токен невалиден/просрочен.

    Токен без версии (выпущен до её появления) считается версией 0.
    """
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        return None
    sub = payload.get("sub")
    version = payload.get("tv", 0)
    if not isinstance(sub, str) or not isinstance(version, int):
        return None
    return sub, version
