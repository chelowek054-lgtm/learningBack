"""Аутентификация — собственный JWT (WS1)."""

from fastapi import APIRouter, HTTPException, status

from core.deps import CurrentUser, SessionDep
from core.models import User
from core.schemas import LoginIn, ProfileIn, RegisterIn, TokenOut, UserOut
from core.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenOut, status_code=status.HTTP_201_CREATED)
def register(body: RegisterIn, session: SessionDep) -> TokenOut:
    exists = session.query(User).filter(User.email == body.email).first()
    if exists is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Email уже зарегистрирован")
    user = User(email=body.email, password_hash=hash_password(body.password), profile={})
    session.add(user)
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


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> User:
    return user


@router.put("/me/profile", response_model=UserOut)
def update_profile(body: ProfileIn, user: CurrentUser, session: SessionDep) -> User:
    user.profile = body.profile
    session.commit()
    session.refresh(user)
    return user
