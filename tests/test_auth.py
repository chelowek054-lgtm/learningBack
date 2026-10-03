"""Auth (SPEC-02): регистрация, вход, токен, восстановление пароля, роли.

Запросы идут БЕЗ подмены `get_current_user`, как у настоящего клиента: подменяется
только сессия БД, чтобы всё откатывалось вместе с тестом.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from core.app import app
from core.config import settings
from core.db import get_session
from core.models import PasswordResetCode, SrsCard, User
from core.ratelimit import reset_request_limiter
from core.security import hash_reset_code
from tests.conftest import make_user


@pytest.fixture(autouse=True)
def issued_codes(monkeypatch):
    """Код хранится хешем, поэтому тест подставляет известные коды и берёт их отсюда."""
    reset_request_limiter.reset()
    issued: list[str] = []

    def fake() -> str:
        issued.append(f"{10000000 + len(issued) + 1}")
        return issued[-1]

    monkeypatch.setattr("core.routers.auth.generate_reset_code", fake)
    yield issued
    reset_request_limiter.reset()


@pytest.fixture
def anon(session):
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _register(anon, email="a@example.com", password="secret1"):
    return anon.post(
        "/auth/register", json={"email": email, "password": password, "acceptPolicy": True}
    )


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---- AC-02.1 регистрация ----


def test_register_returns_token_and_creates_no_content(anon, session):
    r = _register(anon)
    assert r.status_code == 201
    assert r.json()["token_type"] == "bearer"
    user = session.query(User).filter_by(email="a@example.com").one()
    assert user.password_hash and user.password_hash != "secret1"
    # Предмета ещё нет, поэтому стартового контента тоже нет (FR-SRS-05).
    assert session.query(SrsCard).filter_by(user_id=user.id).count() == 0


def test_register_duplicate_email_conflicts(anon):
    assert _register(anon).status_code == 201
    assert _register(anon).status_code == 409


def test_register_rejects_short_password(anon):
    assert _register(anon, password="12345").status_code == 422


# ---- AC-02.2 вход ----


def test_login_ok_and_me(anon):
    _register(anon)
    token = anon.post("/auth/login", json={"email": "a@example.com", "password": "secret1"}).json()[
        "access_token"
    ]
    me = anon.get("/auth/me", headers=_auth(token))
    assert me.status_code == 200
    assert me.json()["email"] == "a@example.com"
    assert me.json()["is_superuser"] is False


def test_login_does_not_reveal_which_part_is_wrong(anon):
    _register(anon)
    wrong_pass = anon.post("/auth/login", json={"email": "a@example.com", "password": "nope123"})
    no_user = anon.post("/auth/login", json={"email": "zzz@example.com", "password": "nope123"})
    assert wrong_pass.status_code == no_user.status_code == 401
    assert wrong_pass.json() == no_user.json()


# ---- AC-02.3 защита роутов ----


@pytest.mark.parametrize(
    "method,path",
    [
        ("get", "/auth/me"),
        ("get", "/sync/pull"),
        ("get", "/jobs"),
        ("get", "/content/materials"),
        ("get", "/graph/ml"),
    ],
)
def test_protected_routes_reject_anonymous(anon, method, path):
    assert getattr(anon, method)(path).status_code in (401, 403)


def test_garbage_token_is_rejected(anon):
    assert anon.get("/auth/me", headers=_auth("not-a-jwt")).status_code == 401


# ---- профиль ----


def test_profile_is_saved(anon):
    token = _register(anon).json()["access_token"]
    body = {"profile": {"subject": {"id": "ml", "title": "ML", "target": "apply"}}}
    r = anon.put("/auth/me/profile", json=body, headers=_auth(token))
    assert r.status_code == 200
    assert anon.get("/auth/me", headers=_auth(token)).json()["profile"] == body["profile"]


# ---- AC-02.4..5 восстановление пароля ----


def _latest_code(session, email) -> PasswordResetCode:
    user = session.query(User).filter_by(email=email).one()
    return (
        session.query(PasswordResetCode)
        .filter_by(user_id=user.id)
        .order_by(PasswordResetCode.created_at.desc())
        .first()
    )


def test_reset_request_is_uniform_for_unknown_email(anon):
    _register(anon)
    known = anon.post("/auth/password-reset/request", json={"email": "a@example.com"})
    unknown = anon.post("/auth/password-reset/request", json={"email": "zzz@example.com"})
    assert known.status_code == unknown.status_code == 202
    assert known.json() == unknown.json()


def test_reset_full_flow_changes_password_and_burns_code(anon, session, issued_codes):
    _register(anon)
    anon.post("/auth/password-reset/request", json={"email": "a@example.com"})
    code = _latest_code(session, "a@example.com")
    # В БД лежит хеш, а не сам код (T-0003).
    assert code.code_hash == hash_reset_code(issued_codes[0])
    assert issued_codes[0] not in code.code_hash

    confirm = {"email": "a@example.com", "code": issued_codes[0], "new_password": "brandnew1"}
    assert anon.post("/auth/password-reset/confirm", json=confirm).status_code == 204

    assert (
        anon.post("/auth/login", json={"email": "a@example.com", "password": "secret1"}).status_code
        == 401
    )
    assert (
        anon.post(
            "/auth/login", json={"email": "a@example.com", "password": "brandnew1"}
        ).status_code
        == 200
    )
    # Код одноразовый.
    assert anon.post("/auth/password-reset/confirm", json=confirm).status_code == 400


def test_reset_wrong_code_counts_attempts_then_locks(anon, session, issued_codes):
    _register(anon)
    anon.post("/auth/password-reset/request", json={"email": "a@example.com"})
    bad = {"email": "a@example.com", "code": "00000000", "new_password": "brandnew1"}

    for _ in range(settings.password_reset_max_attempts):
        assert anon.post("/auth/password-reset/confirm", json=bad).status_code == 400
    # Лимит исчерпан: даже верный код больше не принимается.
    good = {"email": "a@example.com", "code": issued_codes[0], "new_password": "brandnew1"}
    assert anon.post("/auth/password-reset/confirm", json=good).status_code == 429


def test_reset_expired_code_is_rejected(anon, session, issued_codes):
    _register(anon)
    anon.post("/auth/password-reset/request", json={"email": "a@example.com"})
    code = _latest_code(session, "a@example.com")
    code.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    session.flush()
    body = {"email": "a@example.com", "code": issued_codes[0], "new_password": "brandnew1"}
    assert anon.post("/auth/password-reset/confirm", json=body).status_code == 400


def test_new_reset_request_invalidates_previous_code(anon, issued_codes):
    _register(anon)
    anon.post("/auth/password-reset/request", json={"email": "a@example.com"})
    anon.post("/auth/password-reset/request", json={"email": "a@example.com"})
    first, second = issued_codes
    body = {"email": "a@example.com", "code": first, "new_password": "brandnew1"}
    assert anon.post("/auth/password-reset/confirm", json=body).status_code == 400
    body["code"] = second
    assert anon.post("/auth/password-reset/confirm", json=body).status_code == 204


def test_password_change_revokes_old_tokens(anon, issued_codes):
    """T-0003: старый токен → 401 после смены пароля; новый вход работает."""
    old = _register(anon).json()["access_token"]
    assert anon.get("/auth/me", headers=_auth(old)).status_code == 200

    anon.post("/auth/password-reset/request", json={"email": "a@example.com"})
    body = {"email": "a@example.com", "code": issued_codes[0], "new_password": "brandnew1"}
    assert anon.post("/auth/password-reset/confirm", json=body).status_code == 204

    assert anon.get("/auth/me", headers=_auth(old)).status_code == 401
    new = anon.post("/auth/login", json={"email": "a@example.com", "password": "brandnew1"})
    assert anon.get("/auth/me", headers=_auth(new.json()["access_token"])).status_code == 200


def test_token_without_version_claim_is_still_valid_until_password_change(anon, session):
    """Токены, выпущенные до появления версии, остаются годными (версия 0)."""
    import jwt

    _register(anon)
    user = session.query(User).filter_by(email="a@example.com").one()
    legacy = jwt.encode(
        {"sub": str(user.id), "exp": 4102444800}, settings.jwt_secret, algorithm="HS256"
    )
    assert anon.get("/auth/me", headers=_auth(legacy)).status_code == 200


def test_reset_request_is_rate_limited_per_email(anon):
    _register(anon)
    body = {"email": "a@example.com"}
    for _ in range(settings.password_reset_request_limit):
        assert anon.post("/auth/password-reset/request", json=body).status_code == 202
    assert anon.post("/auth/password-reset/request", json=body).status_code == 429


def test_reset_rate_limit_does_not_reveal_unknown_email(anon):
    """Для несуществующего адреса лимит срабатывает так же, как для настоящего."""
    body = {"email": "ghost@example.com"}
    for _ in range(settings.password_reset_request_limit):
        assert anon.post("/auth/password-reset/request", json=body).status_code == 202
    assert anon.post("/auth/password-reset/request", json=body).status_code == 429


def test_reset_rate_limit_per_ip_across_emails(anon):
    for n in range(settings.password_reset_request_limit):
        r = anon.post("/auth/password-reset/request", json={"email": f"u{n}@example.com"})
        assert r.status_code == 202
    r = anon.post("/auth/password-reset/request", json={"email": "other@example.com"})
    assert r.status_code == 429


def test_reset_code_must_be_eight_digits(anon):
    body = {"email": "a@example.com", "code": "12ab", "new_password": "brandnew1"}
    assert anon.post("/auth/password-reset/confirm", json=body).status_code == 422


# ---- роли ----


def test_superuser_flag_is_never_set_by_registration(anon, session):
    _register(anon)
    assert session.query(User).filter_by(email="a@example.com").one().is_superuser is False


def test_superuser_dependency_blocks_regular_user(client, session):
    regular = make_user(session)
    admin = make_user(session, superuser=True)
    body = {"domain": "ml", "title": "T", "tier": "derived", "content": {}}
    assert client(regular).post("/graph/canon/nodes", json=body).status_code == 403
    assert client(admin).post("/graph/canon/nodes", json=body).status_code == 201
