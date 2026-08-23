"""Аутентификация — собственный JWT (WS1)."""

import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status

from core.config import settings
from core.deps import CurrentUser, SessionDep
from core.models import PasswordResetCode, User
from core.schemas import (
    LoginIn,
    PasswordResetConfirmIn,
    PasswordResetRequestIn,
    ProfileIn,
    RegisterIn,
    TokenOut,
    UserOut,
)
from core.provisioning import provision_new_user
from core.security import (
    create_access_token,
    generate_reset_code,
    hash_password,
    reset_code_expires_at,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
def register(body: RegisterIn, session: SessionDep) -> TokenOut:
    exists = session.query(User).filter(User.email == body.email).first()
    if exists is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email уже зарегистрирован")
    user = User(email=body.email, password_hash=hash_password(body.password), profile={})
    session.add(user)
    session.flush()
    # Провижининг: AWL-колода + демо-активности (полезно с первого входа).
    provision_new_user(session, user.id, datetime.now(timezone.utc))
    session.commit()
    session.refresh(user)
    return TokenOut(access_token=create_access_token(str(user.id)))


@router.post("/login", response_model=TokenOut)
def login(body: LoginIn, session: SessionDep) -> TokenOut:
    user = session.query(User).filter(User.email == body.email).first()
    if user is None or user.password_hash is None or not verify_password(
        body.password, user.password_hash
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Неверный email или пароль")
    return TokenOut(access_token=create_access_token(str(user.id)))


@router.post("/password-reset/request", status_code=status.HTTP_202_ACCEPTED)
def password_reset_request(body: PasswordResetRequestIn, session: SessionDep) -> dict:
    """Выпустить временный код восстановления.

    Доставки (почта/SMS) ещё нет — код кладётся в `password_reset_code` и читается
    из БД (pgAdmin). Ответ одинаков независимо от существования email, чтобы не
    давать перебирать зарегистрированные адреса.
    """
    now = datetime.now(timezone.utc)
    user = session.query(User).filter(User.email == body.email).first()
    if user is not None:
        # Прошлые невыданные коды гасим: действующим остаётся только последний.
        session.query(PasswordResetCode).filter(
            PasswordResetCode.user_id == user.id,
            PasswordResetCode.used_at.is_(None),
        ).update({PasswordResetCode.used_at: now}, synchronize_session=False)
        session.add(
            PasswordResetCode(
                user_id=user.id,
                code=generate_reset_code(),
                expires_at=reset_code_expires_at(now),
            )
        )
        session.commit()
    return {"status": "accepted", "ttl_minutes": settings.password_reset_code_ttl_minutes}


@router.post("/password-reset/confirm", status_code=status.HTTP_204_NO_CONTENT)
def password_reset_confirm(body: PasswordResetConfirmIn, session: SessionDep) -> None:
    now = datetime.now(timezone.utc)
    user = session.query(User).filter(User.email == body.email).first()
    entry = (
        session.query(PasswordResetCode)
        .filter(
            PasswordResetCode.user_id == user.id,
            PasswordResetCode.used_at.is_(None),
            PasswordResetCode.expires_at > now,
        )
        .order_by(PasswordResetCode.created_at.desc())
        .first()
        if user is not None
        else None
    )
    if entry is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Код недействителен или просрочен")
    if entry.attempts >= settings.password_reset_max_attempts:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Слишком много попыток, запросите новый код")
    if not secrets.compare_digest(entry.code, body.code):
        entry.attempts += 1
        session.commit()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Код недействителен или просрочен")

    user.password_hash = hash_password(body.new_password)
    entry.used_at = now
    session.commit()


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> User:
    return user


@router.put("/me/profile", response_model=UserOut)
def update_profile(body: ProfileIn, user: CurrentUser, session: SessionDep) -> User:
    user.profile = body.profile
    session.commit()
    session.refresh(user)
    return user
