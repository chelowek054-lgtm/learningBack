"""Отправка писем (T-0002, R-0006): код восстановления пароля уходит на почту.

Провайдер почты не выбран и не нужен коду: подойдёт любой SMTP-сервер (почтовый сервис,
свой Postfix) — задаётся переменными SMTP_*. Без SMTP_HOST работает консольный отправитель:
вне production он пишет письмо в лог сервера, в production не пишет ничего секретного и
громко предупреждает, что доставки нет. Сбой отправки не должен менять ответ API: иначе по
ответу можно было бы узнать, есть ли такой email в системе.
"""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from typing import Protocol

from core.config import settings

log = logging.getLogger("praxis.mail")


class Mailer(Protocol):
    def send(self, to: str, subject: str, body: str) -> None: ...


class ConsoleMailer:
    def send(self, to: str, subject: str, body: str) -> None:
        if settings.is_deployed:
            log.error("Письмо для %s не отправлено: SMTP_HOST не задан", to)
        else:
            log.warning("Письмо для %s · %s\n%s", to, subject, body)


class SmtpMailer:
    def send(self, to: str, subject: str, body: str) -> None:
        msg = EmailMessage()
        msg["From"] = settings.smtp_from or settings.smtp_user
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(body)
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as smtp:
            if settings.smtp_starttls:
                smtp.starttls()
            if settings.smtp_user:
                smtp.login(settings.smtp_user, settings.smtp_password)
            smtp.send_message(msg)


def get_mailer() -> Mailer:
    return SmtpMailer() if settings.smtp_host else ConsoleMailer()


def send_safely(to: str, subject: str, body: str) -> bool:
    """Отправить, не роняя вызывающего: ошибка уходит в лог без адреса получателя в тексте."""
    try:
        get_mailer().send(to, subject, body)
        return True
    except Exception:  # noqa: BLE001 — любой сбой почты не должен менять ответ API
        log.exception("Не удалось отправить письмо")
        return False


def reset_code_message(code: str, ttl_minutes: int) -> tuple[str, str]:
    return (
        "Код восстановления пароля Praxis",
        f"Ваш код: {code}\n\nОн действует {ttl_minutes} мин. Если вы не просили сменить пароль, "
        "просто проигнорируйте это письмо.",
    )
