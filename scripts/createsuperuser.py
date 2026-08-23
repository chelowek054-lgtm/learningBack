"""Создать администратора — аналог `manage.py createsuperuser` из Django.

Интерактивно:
    uv run python -m scripts.createsuperuser
    docker compose exec api uv run python -m scripts.createsuperuser

Без ввода (CI, скрипты):
    uv run python -m scripts.createsuperuser --noinput \
        --email admin@example.com --password secret
    PRAXIS_SUPERUSER_EMAIL=... PRAXIS_SUPERUSER_PASSWORD=... \
        uv run python -m scripts.createsuperuser --noinput

Если пользователь с таким email уже есть — он повышается до администратора
(пароль при этом не меняется, если явно не передан `--password`).
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from datetime import datetime, timezone

from core.db import SessionLocal
from core.models import User
from core.provisioning import provision_new_user
from core.security import hash_password

MIN_PASSWORD_LENGTH = 6


def _fail(message: str) -> None:
    print(f"Ошибка: {message}", file=sys.stderr)
    raise SystemExit(1)


def _prompt_email(preset: str | None) -> str:
    if preset:
        return preset.strip()
    while True:
        value = input("Email: ").strip()
        if "@" in value and len(value) > 2:
            return value
        print("  нужен корректный email")


def _prompt_password(preset: str | None) -> str:
    if preset:
        if len(preset) < MIN_PASSWORD_LENGTH:
            _fail(f"пароль короче {MIN_PASSWORD_LENGTH} символов")
        return preset
    while True:
        first = getpass.getpass("Пароль: ")
        if len(first) < MIN_PASSWORD_LENGTH:
            print(f"  пароль должен быть не короче {MIN_PASSWORD_LENGTH} символов")
            continue
        if first != getpass.getpass("Пароль (ещё раз): "):
            print("  пароли не совпадают")
            continue
        return first


def main() -> None:
    parser = argparse.ArgumentParser(description="Создать администратора Praxis.")
    parser.add_argument("--email", default=os.getenv("PRAXIS_SUPERUSER_EMAIL"))
    parser.add_argument("--password", default=os.getenv("PRAXIS_SUPERUSER_PASSWORD"))
    parser.add_argument(
        "--noinput",
        action="store_true",
        help="не спрашивать ничего: email и пароль должны быть переданы",
    )
    args = parser.parse_args()

    if args.noinput and not args.email:
        _fail("--noinput требует --email (или PRAXIS_SUPERUSER_EMAIL)")

    email = _prompt_email(args.email if (args.noinput or args.email) else None)

    with SessionLocal() as session:
        existing = session.query(User).filter(User.email == email).first()

        if existing is not None:
            if args.noinput and not args.password:
                password = None
            else:
                password = args.password
            if existing.is_superuser and password is None:
                print(f"«{email}» уже администратор — ничего не менял.")
                return
            if password is not None:
                existing.password_hash = hash_password(
                    _prompt_password(password) if not args.noinput else password
                )
            existing.is_superuser = True
            session.commit()
            what = "повышен до администратора"
            if password is not None:
                what += " (пароль обновлён)"
            print(f"«{email}» {what}.")
            return

        if args.noinput and not args.password:
            _fail("--noinput требует --password (или PRAXIS_SUPERUSER_PASSWORD)")
        password = _prompt_password(args.password)

        user = User(
            email=email,
            password_hash=hash_password(password),
            is_superuser=True,
            profile={},
        )
        session.add(user)
        session.flush()
        # Тот же провижининг, что и при обычной регистрации: админ — тоже пользователь.
        provision_new_user(session, user.id, datetime.now(timezone.utc))
        session.commit()
        print(f"Администратор «{email}» создан. Вход: /admin")


if __name__ == "__main__":
    main()
