"""Реестр модулей backend (SPEC-01, ADR-0019) и провижининг по предмету (FR-SRS-05)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from core import modules
from core.app import app
from core.db import get_session
from core.models import Activity, Rubric, SrsCard
from tests.conftest import make_user

NOW = datetime(2026, 9, 30, tzinfo=timezone.utc)


def _set_subject(client, user, title, sid=None):
    profile = {"subject": {"id": sid or title.lower().replace(" ", "-"), "title": title}}
    r = client(user).put("/auth/me/profile", json={"profile": profile})
    assert r.status_code == 200


# ---- реестр ----


def test_installed_modules_are_loaded():
    assert [m.id for m in modules.load_modules()] == ["languages", "ml", "knowledge"]


def test_duplicate_module_is_rejected():
    with pytest.raises(ValueError, match="уже подключён"):
        modules.load_modules("modules.ml,modules.ml")
    modules.load_modules.__globals__["_modules"] = None  # сбросить кэш после проверки


def test_grade_job_types_map_to_card_modules():
    assert modules.grade_job_modules() == {
        "grade_writing": "languages",
        "grade_concept": "ml",
        "grade_code": "ml",
    }


def test_module_routers_are_mounted():
    assert "/graph/{domain}" in app.openapi()["paths"]


def test_core_does_not_know_modules():
    """NFR-03: ядро backend не импортирует модули и не называет их в коде."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parent.parent / "core"
    bad = []
    for f in root.rglob("*.py"):
        text = f.read_text(encoding="utf-8")
        if re.search(r"^\s*(from|import) modules", text, re.M):
            bad.append(f"{f.name}: импорт модулей")
        if re.search(r"""["'](languages|ml|knowledge)["']""", text):
            bad.append(f"{f.name}: имя модуля")
    assert bad == []


# ---- рубрики ----


def test_sync_rubrics_adds_missing_and_is_idempotent(session):
    first = modules.sync_rubrics(session)
    assert first >= 2
    assert {r.id for r in session.query(Rubric)} >= {"ielts_writing_task2", "concept_check"}
    assert modules.sync_rubrics(session) == 0


def test_sync_rubrics_never_overwrites_existing_version(session):
    modules.sync_rubrics(session)
    rubric = session.get(Rubric, ("concept_check", 1))
    rubric.prompt = "правка куратора"
    session.flush()
    modules.sync_rubrics(session)
    session.refresh(rubric)
    assert rubric.prompt == "правка куратора"


# ---- провижининг по предмету ----


def test_language_subject_gets_awl_and_essay(client, session):
    user = make_user(session)
    _set_subject(client, user, "IELTS Academic")
    assert session.query(SrsCard).filter_by(user_id=user.id, source="awl").count() == 10
    essay = session.query(Activity).filter_by(user_id=user.id, type="ielts_writing_task2")
    assert essay.count() == 1
    # ML-контент языковому предмету не нужен.
    assert session.query(Activity).filter_by(user_id=user.id, module="ml").count() == 0


def test_ml_subject_gets_ml_demo_but_no_awl(client, session):
    user = make_user(session)
    _set_subject(client, user, "Машинное обучение", sid="ml")
    assert session.query(Activity).filter_by(user_id=user.id, module="ml").count() == 2
    assert session.query(SrsCard).filter_by(user_id=user.id).count() == 0


def test_unrelated_subject_gets_no_starter_content(client, session):
    """Регресс: «Теория музыки» получала чужую колоду AWL."""
    user = make_user(session)
    _set_subject(client, user, "Теория музыки")
    assert session.query(SrsCard).filter_by(user_id=user.id).count() == 0
    assert session.query(Activity).filter_by(user_id=user.id).count() == 0


def test_provisioning_is_idempotent(client, session):
    user = make_user(session)
    _set_subject(client, user, "IELTS Academic")
    _set_subject(client, user, "IELTS Academic")
    assert session.query(SrsCard).filter_by(user_id=user.id, source="awl").count() == 10
    assert (
        session.query(Activity).filter_by(user_id=user.id).count() == 2
    )  # письмо Task 2 и описание данных Task 1


def test_profile_without_subject_provisions_nothing(client, session):
    user = make_user(session)
    client(user).put("/auth/me/profile", json={"profile": {"note": "x"}})
    assert session.query(SrsCard).filter_by(user_id=user.id).count() == 0


def test_lifespan_survives_without_database(monkeypatch):
    """/health не зависит от БД: сбой синхронизации рубрик не роняет старт."""

    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr("core.app.SessionLocal", boom)
    app.dependency_overrides[get_session] = lambda: None
    try:
        with TestClient(app) as c:
            assert c.get("/health").json()["status"] == "ok"
    finally:
        app.dependency_overrides.clear()
