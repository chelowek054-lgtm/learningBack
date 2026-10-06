"""Постановка цели как диалог: уточнение, пересказ, подтверждение (T-0061, R-0033, V-0085)."""

from __future__ import annotations

import pytest

from modules.knowledge import ai, goal_intake, router, subdomains
from modules.knowledge.goal_intake import GoalIntakeError
from modules.knowledge.models import Concept, GoalIntake
from tests.conftest import make_user

# ---- вопросы ----


def test_questions_are_two_to_four_unique_and_trimmed():
    raw = ["  Для чего?  ", "для чего?", "С какого уровня?", "Подтемы?", "Ещё?", "И ещё?"]

    questions = goal_intake.clean_questions(raw)

    assert [q["text"] for q in questions] == ["Для чего?", "С какого уровня?", "Подтемы?", "Ещё?"]
    assert [q["id"] for q in questions] == ["q1", "q2", "q3", "q4"]


def test_too_few_questions_are_topped_up_to_the_minimum():
    questions = goal_intake.clean_questions(["Только один?"])

    assert len(questions) == goal_intake.MIN_QUESTIONS
    assert questions[0]["text"] == "Только один?"


def test_garbage_from_the_model_still_gives_questions():
    assert len(goal_intake.clean_questions(None)) >= goal_intake.MIN_QUESTIONS
    assert len(goal_intake.clean_questions({"x": 1})) >= goal_intake.MIN_QUESTIONS


def test_empty_goal_gets_no_questions():
    with pytest.raises(GoalIntakeError) as e:
        goal_intake.propose_questions("   ")
    assert e.value.code == "empty_goal"


# ---- пересказ ----


def test_skipped_questions_do_not_reach_the_summary():
    answers = [
        {"question": "Для чего?", "answer": "для работы"},
        {"question": "Уровень?", "answer": "  "},
        {"question": "Подтемы?", "answer": None},
        {"question": "Ещё?", "answer": "нейросети"},
    ]

    s = goal_intake.summarize("машинное обучение", answers)

    assert s["area"] == "машинное обучение"
    assert s["goal"] == "для работы" and s["wishes"] == ["нейросети"]


def test_summary_without_any_answers_is_still_possible():
    s = goal_intake.summarize("машинное обучение", [])

    assert s == {
        "area": "машинное обучение",
        "goal": "машинное обучение",
        "level": goal_intake.DEFAULT_LEVEL,
        "wishes": [],
        "knows": "",
        "constraints": {},
        "assumed": ["goal", "level", "knows", "constraints"],
    }


def test_summary_is_cleaned_like_a_model_answer():
    s = goal_intake.clean_summary(
        {"area": " А ", "goal": "", "level": "wizard", "wishes": ["x", "x", " ", "y"] + ["z"] * 20},
        "запасной",
    )

    assert s["area"] == "А" and s["goal"] == "А"
    assert s["level"] == goal_intake.DEFAULT_LEVEL
    assert s["wishes"][:2] == ["x", "y"] and len(s["wishes"]) <= goal_intake.MAX_WISHES


def test_confirmed_goal_becomes_one_line_for_the_builder():
    text = goal_intake.as_goal_text(
        {"area": "ML", "goal": "для работы", "level": "apply", "wishes": ["сети", "деревья"]}
    )

    assert text == "ML. цель: для работы. важно: сети; деревья"


# ---- API: диалог ----


def test_dialog_endpoints_do_not_write_anything(session, client):
    api = client(make_user(session))

    q = api.post("/graph/goal/clarify", json={"text": "машинное обучение"})
    s = api.post(
        "/graph/goal/summarize",
        json={
            "text": "машинное обучение",
            "answers": [{"question": "Для чего?", "answer": "работа"}],
        },
    )

    assert q.status_code == 200 and 2 <= len(q.json()["questions"]) <= 4
    assert s.status_code == 200 and s.json()["area"] == "машинное обучение"
    assert session.query(GoalIntake).count() == 0 and session.query(Concept).count() == 0


def test_empty_text_is_rejected(session, client):
    api = client(make_user(session))

    assert api.post("/graph/goal/clarify", json={"text": ""}).status_code == 422
    assert api.post("/graph/goal/summarize", json={"text": "", "answers": []}).status_code == 422


def test_status_is_unconfirmed_until_the_person_confirms(session, client):
    api = client(make_user(session))

    assert api.get("/graph/goal/intake/ml").json() == {"confirmed": False, "summary": None}

    api.post(
        "/graph/goal/confirm",
        json={"domain": "ml", "area": "ML", "goal": "работа", "level": "apply"},
    )

    got = api.get("/graph/goal/intake/ml").json()
    assert got["confirmed"] is True and got["summary"]["area"] == "ML"


def test_confirmation_keeps_only_the_summary_not_the_dialog(session, client):
    api = client(make_user(session))

    api.post(
        "/graph/goal/confirm",
        json={"domain": "ml", "area": "ML", "goal": "работа", "level": "apply", "wishes": ["сети"]},
    )

    row = session.query(GoalIntake).one()
    assert set(row.summary) == {
        "area",
        "goal",
        "level",
        "wishes",
        "knows",
        "constraints",
        "assumed",
    }


def test_reconfirming_replaces_the_previous_summary(session, client):
    user = make_user(session)
    api = client(user)
    api.post("/graph/goal/confirm", json={"domain": "ml", "area": "ML", "level": "understand"})

    api.post("/graph/goal/confirm", json={"domain": "ml", "area": "ML глубже", "level": "create"})

    rows = session.query(GoalIntake).filter_by(user_id=user.id).all()
    assert len(rows) == 1 and rows[0].summary["area"] == "ML глубже"


def test_confirmation_belongs_to_the_person(session, client):
    client(make_user(session)).post("/graph/goal/confirm", json={"domain": "ml", "area": "ML"})

    other = client(make_user(session))

    assert other.get("/graph/goal/intake/ml").json()["confirmed"] is False


# ---- блокировка построения до подтверждения ----


@pytest.fixture
def no_model_spend(monkeypatch):
    """Любой вызов построения до подтверждения — ошибка теста: модель тратиться не должна."""

    def boom(*a, **k):
        raise AssertionError("модель вызвана до подтверждения цели")

    monkeypatch.setattr(subdomains, "propose_split", boom)
    monkeypatch.setattr(subdomains, "build_subdomain", boom)
    monkeypatch.setattr(router, "build_graph", boom)


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/graph/goal/split", {"domain": "ml", "topic": "ML"}),
        ("/graph/goal/build", {"domain": "ml", "subdomains": [{"key": "a", "title": "A"}]}),
        ("/graph/canon/build", {"domain": "ml", "topic": "ML"}),
    ],
)
def test_nothing_is_built_before_confirmation(session, client, no_model_spend, path, body):
    r = client(make_user(session)).post(path, json=body)

    assert r.status_code == 409
    assert "подтвердите" in r.json()["detail"]
    assert session.query(Concept).count() == 0


def test_confirmation_of_another_area_does_not_unlock_this_one(session, client, no_model_spend):
    api = client(make_user(session))
    api.post("/graph/goal/confirm", json={"domain": "физика", "area": "Физика"})

    assert api.post("/graph/goal/split", json={"domain": "ml", "topic": "ML"}).status_code == 409


def test_after_confirmation_the_whole_chain_works(session, client):
    api = client(make_user(session))
    api.post("/graph/goal/confirm", json={"domain": "ml", "area": "ML"})

    split = api.post("/graph/goal/split", json={"domain": "ml", "topic": "ML"})
    built = api.post(
        "/graph/goal/build", json={"domain": "ml", "subdomains": split.json()["subdomains"]}
    )

    assert split.status_code == 200 and built.status_code == 200
    assert session.query(Concept).filter_by(domain="ml").count() > 0


def test_confirmed_summary_is_the_input_of_the_build(session, client, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(
        subdomains, "propose_split", lambda d, goal, limit=8: seen.append(goal) or []
    )
    api = client(make_user(session))
    api.post(
        "/graph/goal/confirm",
        json={"domain": "ml", "area": "ML", "goal": "для работы", "wishes": ["сети"]},
    )

    api.post("/graph/goal/split", json={"domain": "ml", "topic": "просто ML"})

    assert seen == ["ML. цель: для работы. важно: сети"]  # а не голая строка из запроса


def test_canon_build_also_receives_the_confirmed_goal(session, client, monkeypatch):
    seen: list[str] = []

    def fake(domain, topic, max_nodes=8):
        seen.append(topic)
        return ai._fixture_graph(topic)

    monkeypatch.setattr(router, "build_graph", fake)
    api = client(make_user(session))
    api.post("/graph/goal/confirm", json={"domain": "ml", "area": "ML", "goal": "работа"})

    assert api.post("/graph/canon/build", json={"domain": "ml", "topic": "x"}).status_code == 200
    assert seen == ["ML. цель: работа"]


def test_curator_is_not_blocked_by_the_dialog(session, client):
    r = client(make_user(session, superuser=True)).post(
        "/graph/goal/split", json={"domain": "ml", "topic": "ML"}
    )

    assert r.status_code == 200


# ---- пять полей цели (T-0074, R-0041) ----


def test_five_fields_are_kept_and_nothing_is_assumed_when_all_are_stated():
    s = goal_intake.clean_summary(
        {
            "area": "Английский",
            "goal": "сдать IELTS",
            "level": "apply",
            "knows": "читаю свободно, пишу плохо",
            "constraints": {"deadline": "через 3 месяца", "hoursPerWeek": 6, "format": "короткие"},
        },
        "английский",
    )

    assert s["knows"] == "читаю свободно, пишу плохо"
    assert s["constraints"] == {
        "deadline": "через 3 месяца",
        "hoursPerWeek": 6,
        "format": "короткие",
    }
    assert s["assumed"] == []


def test_unstated_fields_are_listed_as_assumed_not_invented():
    s = goal_intake.clean_summary({"area": "Python", "goal": "работа"}, "python")

    assert s["knows"] == "" and s["constraints"] == {}
    assert s["assumed"] == ["level", "knows", "constraints"]  # цель названа, остальное — нет


def test_constraints_are_cleaned():
    cleaned = goal_intake.clean_constraints(
        {"deadline": "  ", "hoursPerWeek": 500, "format": "видео", "extra": 1}
    )
    assert cleaned == {"format": "видео"}  # лишнее, пустое и нереальные часы не хранятся
    assert goal_intake.clean_constraints({"hoursPerWeek": 7.5}) == {"hoursPerWeek": 7.5}
    assert goal_intake.clean_constraints({"hoursPerWeek": True}) == {}
    assert goal_intake.clean_constraints("не словарь") == {}


def test_goal_text_carries_what_is_known_and_the_limits_to_the_build():
    text = goal_intake.as_goal_text(
        {
            "area": "Английский",
            "goal": "сдать IELTS",
            "wishes": ["письмо"],
            "knows": "читаю свободно",
            "constraints": {"deadline": "3 месяца", "hoursPerWeek": 6},
        }
    )
    assert "уже знает: читаю свободно" in text
    assert "срок 3 месяца" in text and "6 ч в неделю" in text and "важно: письмо" in text


def test_old_summaries_without_new_fields_still_load_and_confirm(session, client):
    api = client(make_user(session))
    r = api.post("/graph/goal/confirm", json={"domain": "ml", "area": "ML", "goal": "работа"})
    assert r.status_code == 200
    assert r.json()["summary"]["assumed"] == ["level", "knows", "constraints"]


def test_confirm_accepts_the_five_fields(session, client):
    api = client(make_user(session))
    body = {
        "domain": "ml",
        "area": "ML",
        "goal": "работа",
        "level": "create",
        "knows": "линейная алгебра",
        "constraints": {"hoursPerWeek": 4},
    }
    summary = api.post("/graph/goal/confirm", json=body).json()["summary"]
    assert summary["knows"] == "линейная алгебра" and summary["constraints"] == {"hoursPerWeek": 4}
    assert summary["assumed"] == []
