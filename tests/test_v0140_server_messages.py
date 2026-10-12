"""Сервер говорит на языке пользователя: коды ошибок, уведомления, письма (V-0140, R-0081, T-0179)."""

from __future__ import annotations

from core.mail import reset_code_message
from core.messages import MESSAGES, msg, user_lang
from modules.knowledge import notifications
from modules.knowledge.cow import effective_graph
from modules.knowledge.mastery import MasteryState, save_state
from tests.conftest import make_user
from tests.kg_helpers import concept


def _params(value) -> set[str]:
    import re

    text = " ".join(value.values()) if isinstance(value, dict) else value
    return set(re.findall(r"\{(\w+)\}", text))


def test_catalogs_have_the_same_keys_and_params():
    assert set(MESSAGES["ru"]) == set(MESSAGES["en"])
    for key, ru in MESSAGES["ru"].items():
        assert _params(ru) == _params(MESSAGES["en"][key]), key


def test_plural_forms_for_1_2_5_11_21():
    ru = [msg("ru", "course.ready.body", n) for n in (1, 2, 5, 11, 21)]
    assert [x.split()[2:4] for x in ru] == [
        ["1", "шаг,"],
        ["2", "шага,"],
        ["5", "шагов,"],
        ["11", "шагов,"],
        ["21", "шаг,"],
    ]
    en = [msg("en", "course.ready.body", n) for n in (1, 2, 5, 11, 21)]
    assert en[0].startswith("The course has 1 step,") and en[1].startswith(
        "The course has 2 steps,"
    )


def test_user_language_defaults_to_russian_and_follows_the_profile():
    assert user_lang({}) == "ru" and user_lang(None) == "ru"
    assert user_lang({"uiLang": "en"}) == "en"
    assert user_lang({"studyLang": "en"}) == "en"
    assert user_lang({"uiLang": "de"}) == "ru"


def test_course_notification_is_written_in_the_users_language(session):
    ru, en = make_user(session), make_user(session)
    en.profile = {"uiLang": "en"}
    a = notifications.course_ready(session, ru.id, "algebra", 3, 0)
    b = notifications.course_ready(session, en.id, "algebra", 3, 0)
    assert a.title == "Курс готов" and "3 шага" in a.body
    assert b.title == "Your course is ready" and "3 steps" in b.body


def test_reset_mail_follows_the_language():
    subject, body = reset_code_message("12345678", 15, "en")
    assert "reset code" in subject and "12345678" in body and "15 min" in body
    subject, body = reset_code_message("12345678", 15)
    assert "Код восстановления" in subject and "15 мин" in body


def test_api_error_carries_a_code_and_keeps_a_text(client, session):
    r = client(make_user(session)).put("/auth/me/lang", json={"uiLang": "xx"})
    body = r.json()
    assert r.status_code == 422 and body["code"] == "lang_unsupported"
    assert body["params"] == {"lang": "xx"} and isinstance(body["detail"], str)


def test_graph_shows_mastery_of_a_shared_concept_in_the_other_area(session):
    from modules.knowledge.models import ConceptArea

    c = concept(session, "Shared", domain="calculus", status="approved")
    session.add(ConceptArea(concept_id=c.id, domain="calculus"))
    session.add(ConceptArea(concept_id=c.id, domain="physics"))
    session.flush()
    user = make_user(session)
    save_state(
        session, user.id, "calculus", c.id, MasteryState(alpha=9.0, beta=1.0, observations=9)
    )
    node = next(
        n for n in effective_graph(session, user.id, "physics")["nodes"] if n["id"] == str(c.id)
    )
    assert node["mastery"]
