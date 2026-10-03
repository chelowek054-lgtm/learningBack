"""Удаление аккаунта и согласие с политикой при регистрации (T-0028, R-0018, V-0029)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import text

from core import modules, userdata
from core.config import settings
from core.models import Activity, DataAccessLog, LlmUsage, Response, SrsCard, User
from core.security import hash_password
from tests.conftest import make_user


def populate(session, user):
    act = Activity(user_id=user.id, module="x", type="t", connectivity="online", payload={})
    session.add(act)
    session.flush()
    session.add(
        Response(
            activity_id=act.id,
            user_id=user.id,
            user_answer={},
            local_created_at=datetime.now(timezone.utc),
        )
    )
    session.add(
        SrsCard(
            user_id=user.id,
            module="x",
            front={},
            back={},
            source="g",
            fsrs_state={},
            due_at=datetime.now(timezone.utc),
        )
    )
    session.add(
        LlmUsage(user_id=user.id, purpose="p", model="m", prompt_tokens=10, completion_tokens=5)
    )
    session.add(
        DataAccessLog(user_id=user.id, module_id="m", data_type="t", mode="read", allowed=True)
    )
    session.flush()


def rows_referencing(session, user_id):
    """Все строки во всех таблицах, где есть ссылка на человека."""
    from core.db import Base

    total = 0
    for table in Base.metadata.sorted_tables:
        for fk in table.foreign_keys:
            if fk.column.table.name == "user":
                total += session.execute(
                    text(f'select count(*) from "{table.name}" where "{fk.parent.name}" = :u'),
                    {"u": user_id},
                ).scalar_one()
    return total


def test_deleting_the_account_leaves_no_rows_with_the_person(session):
    user = make_user(session)
    populate(session, user)
    uid = user.id

    userdata.delete_account(session, uid, userdata.types(modules.load_modules()))

    assert session.get(User, uid) is None
    assert rows_referencing(session, uid) == 0


def test_only_anonymized_statistics_remain(session):
    user = make_user(session)
    populate(session, user)

    result = userdata.delete_account(session, user.id, userdata.types(modules.load_modules()))

    assert result["anonymized"].get("llm_usage") == 1
    row = session.query(LlmUsage).filter(LlmUsage.user_id.is_(None)).first()
    assert row is not None and row.prompt_tokens == 10  # счётчик остался, человек — нет


def test_other_people_are_untouched(session):
    gone, stays = make_user(session), make_user(session)
    populate(session, gone)
    populate(session, stays)

    userdata.delete_account(session, gone.id, userdata.types(modules.load_modules()))

    assert session.query(Activity).filter_by(user_id=stays.id).count() == 1
    assert session.get(User, stays.id) is not None


def test_api_requires_confirmation_and_the_password(session, client):
    user = make_user(session)
    user.password_hash = hash_password("secret1")
    session.flush()
    api = client(user)

    assert api.post("/me/data/delete-account", json={}).status_code == 422
    assert api.post("/me/data/delete-account", json={"confirm": True}).status_code == 401
    assert (
        api.post("/me/data/delete-account", json={"confirm": True, "password": "wrong"}).status_code
        == 401
    )
    assert session.get(User, user.id) is not None

    r = api.post("/me/data/delete-account", json={"confirm": True, "password": "secret1"})

    assert r.status_code == 200 and session.get(User, user.id) is None


# ---- согласие с политикой ----


def test_registration_requires_consent_to_the_policy(client, session):
    api = client(make_user(session))

    r = api.post("/auth/register", json={"email": "no-consent@example.com", "password": "secret1"})

    assert r.status_code == 422
    assert session.query(User).filter_by(email="no-consent@example.com").count() == 0


def test_registration_records_the_policy_version(client, session):
    api = client(make_user(session))

    r = api.post(
        "/auth/register",
        json={"email": "consent@example.com", "password": "secret1", "acceptPolicy": True},
    )

    assert r.status_code == 201
    user = session.query(User).filter_by(email="consent@example.com").one()
    assert user.policy_version == settings.policy_version and user.policy_accepted_at is not None
