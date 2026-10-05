"""Доставка кода восстановления письмом (T-0002, R-0006)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from core import mail
from api.app import app
from core.config import settings
from core.deps import get_session
from core.ratelimit import reset_request_limiter
from tests.conftest import make_user


class Outbox:
    def __init__(self):
        self.sent: list[tuple[str, str, str]] = []
        self.fail = False

    def send(self, to, subject, body):
        if self.fail:
            raise OSError("smtp down")
        self.sent.append((to, subject, body))


@pytest.fixture
def outbox(monkeypatch):
    box = Outbox()
    monkeypatch.setattr(mail, "get_mailer", lambda: box)
    reset_request_limiter.reset()
    yield box
    reset_request_limiter.reset()


@pytest.fixture
def anon(session):
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _request(anon, email):
    return anon.post("/auth/password-reset/request", json={"email": email})


def test_code_is_emailed_to_the_user_with_its_lifetime(session, anon, outbox):
    user = make_user(session)
    session.flush()
    assert _request(anon, user.email).status_code == 202
    ((to, subject, body),) = outbox.sent
    assert to == user.email and "восстановления" in subject
    assert f"{settings.password_reset_code_ttl_minutes} мин" in body
    code = body.split("Ваш код: ")[1].split("\n")[0]
    assert code.isdigit() and len(code) == 8


def test_unknown_email_gets_the_same_answer_and_no_mail(anon, outbox):
    r = _request(anon, "nobody@example.com")
    assert r.status_code == 202 and outbox.sent == []


def test_smtp_failure_does_not_change_the_answer(session, anon, outbox):
    user = make_user(session)
    session.flush()
    outbox.fail = True
    ok = _request(anon, user.email)
    unknown = _request(anon, "nobody@example.com")
    assert ok.status_code == unknown.status_code == 202 and ok.json() == unknown.json()


def test_send_safely_reports_failure_without_raising(outbox):
    outbox.fail = True
    assert mail.send_safely("a@example.com", "s", "b") is False
    outbox.fail = False
    assert mail.send_safely("a@example.com", "s", "b") is True


def test_smtp_is_used_only_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "smtp_host", "")
    assert isinstance(mail.get_mailer(), mail.ConsoleMailer)
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    assert isinstance(mail.get_mailer(), mail.SmtpMailer)


def test_smtp_mailer_builds_the_message_and_logs_in(monkeypatch):
    calls: dict = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            calls["addr"] = (host, port)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self):
            calls["tls"] = True

        def login(self, user, password):
            calls["login"] = (user, password)

        def send_message(self, msg):
            calls["msg"] = msg

    monkeypatch.setattr(mail.smtplib, "SMTP", FakeSMTP)
    for k, v in {
        "smtp_host": "smtp.example.com",
        "smtp_port": 2525,
        "smtp_user": "bot",
        "smtp_password": "pw",
        "smtp_from": "noreply@example.com",
    }.items():
        monkeypatch.setattr(settings, k, v)
    mail.SmtpMailer().send("a@example.com", "Тема", "Текст")
    assert calls["addr"] == ("smtp.example.com", 2525) and calls["tls"] is True
    assert calls["login"] == ("bot", "pw")
    assert calls["msg"]["To"] == "a@example.com" and calls["msg"]["From"] == "noreply@example.com"
