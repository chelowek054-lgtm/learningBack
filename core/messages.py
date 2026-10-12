"""Тексты сервера для людей: уведомления и письма на языке пользователя (R-0081, T-0179).

Язык берётся из профиля (`uiLang`, затем `studyLang`), без настройки остаётся русский, как было до
двух языков. Ошибки API отдают код и параметры, а не текст: выбирает текст клиент (core/errors.py).
Сообщение — строка или набор форм множественного числа; параметры подставляются по именам.
"""

from __future__ import annotations

from typing import Any

DEFAULT_LANG = "ru"
LANGS = ("ru", "en")

MESSAGES: dict[str, dict[str, Any]] = {
    "ru": {
        "unverified": "Не проверено специалистом: содержание собрано автоматически.",
        "push.area": "Область: {domain}",
        "course.ready.title": "Курс готов",
        "course.ready.body": {
            "one": "В курсе {n} шаг, можно приступать.",
            "few": "В курсе {n} шага, можно приступать.",
            "many": "В курсе {n} шагов, можно приступать.",
        },
        "course.ready.drafts": "Черновых шагов: {drafts}.",
        "course.extended.title": "Курс дополнен",
        "course.extended.body": {
            "one": "В область добавлено {n} понятие.",
            "few": "В область добавлено {n} понятия.",
            "many": "В область добавлено {n} понятий.",
        },
        "course.extended.drafts": "Из них черновых: {drafts}.",
        "concept.verified.title": "Понятие проверено",
        "concept.verified.body": "Специалист проверил: {names}. Пометка «Черновик» снята.",
        "concept.changed.title": "Понятие изменено",
        "concept.changed.what": "изменено изложение",
        "concept.changed.body": (
            "В понятиях, которые вы уже прошли, изменился смысл: {names}. "
            "{what}. Рекомендуем повторить шаг."
        ),
        "names.more": "{shown} и ещё {rest}",
        "mail.reset.subject": "Код восстановления пароля Praxis",
        "mail.reset.body": (
            "Ваш код: {code}\n\nОн действует {ttl} мин. Если вы не просили сменить пароль, "
            "просто проигнорируйте это письмо."
        ),
    },
    "en": {
        "unverified": "Not checked by a specialist: the content was assembled automatically.",
        "push.area": "Area: {domain}",
        "course.ready.title": "Your course is ready",
        "course.ready.body": {
            "one": "The course has {n} step, you can start.",
            "other": "The course has {n} steps, you can start.",
        },
        "course.ready.drafts": "Draft steps: {drafts}.",
        "course.extended.title": "Your course was extended",
        "course.extended.body": {
            "one": "{n} concept was added to the area.",
            "other": "{n} concepts were added to the area.",
        },
        "course.extended.drafts": "Of them drafts: {drafts}.",
        "concept.verified.title": "Concept checked",
        "concept.verified.body": "A specialist checked: {names}. The “Draft” mark is removed.",
        "concept.changed.title": "Concept changed",
        "concept.changed.what": "the explanation changed",
        "concept.changed.body": (
            "The meaning changed in concepts you have already completed: {names}. "
            "{what}. We recommend repeating the step."
        ),
        "names.more": "{shown} and {rest} more",
        "mail.reset.subject": "Praxis password reset code",
        "mail.reset.body": (
            "Your code: {code}\n\nIt is valid for {ttl} min. If you did not ask to change your "
            "password, just ignore this email."
        ),
    },
}


def user_lang(profile: dict[str, Any] | None) -> str:
    """Язык текстов для пользователя: язык интерфейса, затем язык изучения, иначе русский."""
    for key in ("uiLang", "studyLang"):
        value = (profile or {}).get(key)
        if value in LANGS:
            return value
    return DEFAULT_LANG


def _category(lang: str, n: int) -> str:
    if lang == "ru":
        m10, m100 = n % 10, n % 100
        if m10 == 1 and m100 != 11:
            return "one"
        if 2 <= m10 <= 4 and not 12 <= m100 <= 14:
            return "few"
        return "many"
    return "one" if n == 1 else "other"


def msg(lang: str, key: str, n: int | None = None, **params: Any) -> str:
    """Текст по ключу; для набора форм нужен `n`. Нет перевода — русский вариант, нет ключа — сам ключ."""
    table = MESSAGES.get(lang) or MESSAGES[DEFAULT_LANG]
    text = table.get(key, MESSAGES[DEFAULT_LANG].get(key, key))
    if isinstance(text, dict):
        text = text.get(_category(lang, n or 0)) or text.get("other") or next(iter(text.values()))
    return text.format(n=n, **params)
