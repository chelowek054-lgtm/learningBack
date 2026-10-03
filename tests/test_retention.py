"""Удаление данных по срокам хранения (T-0028, R-0018, V-0028)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core import modules, userdata
from core.models import Activity, Job
from tests.conftest import make_user

NOW = datetime(2026, 10, 3, tzinfo=timezone.utc)


def job(session, user, status, age_days):
    j = Job(user_id=user.id, type="t", status=status, input_ref={})
    session.add(j)
    session.flush()
    j.created_at = NOW - timedelta(days=age_days)
    session.flush()
    return j


def registry():
    return userdata.types(modules.load_modules())


def test_expired_data_is_deleted_and_fresh_data_stays(session):
    user = make_user(session)
    old, fresh = job(session, user, "done", 40), job(session, user, "done", 5)

    purged = userdata.purge_expired(session, registry(), NOW)

    assert purged["job"] == 1
    ids = {j.id for j in session.query(Job).filter_by(user_id=user.id)}
    assert ids == {fresh.id} and old.id not in ids


def test_unfinished_work_is_not_deleted_even_when_old(session):
    user = make_user(session)
    pending = job(session, user, "pending", 400)
    failed = job(session, user, "failed", 400)

    userdata.purge_expired(session, registry(), NOW)

    ids = {j.id for j in session.query(Job).filter_by(user_id=user.id)}
    assert pending.id in ids and failed.id not in ids


def test_data_without_a_retention_period_is_never_purged(session):
    user = make_user(session)
    session.add(Activity(user_id=user.id, module="x", type="t", connectivity="online", payload={}))
    session.flush()

    purged = userdata.purge_expired(session, registry(), NOW + timedelta(days=100000))

    assert "activity" not in purged
    assert session.query(Activity).filter_by(user_id=user.id).count() == 1


def test_second_run_finds_nothing(session):
    user = make_user(session)
    job(session, user, "done", 90)

    userdata.purge_expired(session, registry(), NOW)

    assert userdata.purge_expired(session, registry(), NOW)["job"] == 0


def test_purge_touches_every_person_not_one(session):
    a, b = make_user(session), make_user(session)
    job(session, a, "done", 90)
    job(session, b, "done", 90)

    assert userdata.purge_expired(session, registry(), NOW)["job"] == 2


def test_admin_runs_retention_through_the_api(session, client):
    user = make_user(session)
    job(session, user, "done", 90)
    admin = client(make_user(session, superuser=True))

    r = admin.post("/retention/run")

    assert r.status_code == 200 and r.json()["purged"]["job"] >= 1
    assert client(make_user(session)).post("/retention/run").status_code == 403
