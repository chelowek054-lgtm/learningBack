"""Sync, очередь задач и error-log → SRS (SPEC-03, SPEC-05)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from core.models import Activity, Job, Response, Rubric, SrsCard
from core.srs import errors_to_card_partials, initial_fsrs_state, insert_cards
from modules.languages.rubrics import RUBRICS as LANG_RUBRICS
from modules.ml.rubrics import RUBRICS as ML_RUBRICS
from tests.conftest import make_user

NOW = "2026-09-30T10:00:00+00:00"


@pytest.fixture
def rubrics(session):
    for r in [*LANG_RUBRICS, *ML_RUBRICS]:
        session.add(Rubric(**r))
    session.flush()


def _activity(aid=None, **over):
    body = {
        "id": str(aid or uuid.uuid4()),
        "module": "languages",
        "type": "ielts_writing_task2",
        "connectivity": "online",
        "payload": {"prompt": "Discuss."},
    }
    return {**body, **over}


def _essay_push(text="I recieve it"):
    aid, rid, jid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    return (
        {
            "activities": [_activity(aid)],
            "responses": [
                {
                    "id": str(rid),
                    "activityId": str(aid),
                    "userAnswer": text,
                    "localCreatedAt": NOW,
                }
            ],
            "jobs": [
                {
                    "id": str(jid),
                    "type": "grade_writing",
                    "inputRef": {"responseId": str(rid), "rubricId": "ielts_writing_task2"},
                }
            ],
        },
        aid,
        rid,
        jid,
    )


# ---- AC-03.2 идемпотентность ----


def test_push_is_idempotent(client, session):
    user = make_user(session)
    c = client(user)
    body = {"activities": [_activity()]}
    first = c.post("/sync/push", json=body)
    second = c.post("/sync/push", json=body)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert session.query(Activity).filter_by(user_id=user.id).count() == 1


def test_push_takes_user_from_token_not_from_body(client, session):
    user, other = make_user(session), make_user(session)
    body = {"activities": [_activity(userId=str(other.id))]}
    client(user).post("/sync/push", json=body)
    assert session.query(Activity).filter_by(user_id=other.id).count() == 0
    assert session.query(Activity).filter_by(user_id=user.id).count() == 1


def test_push_cannot_take_over_another_users_record(client, session):
    owner, thief = make_user(session), make_user(session)
    shared_id = uuid.uuid4()
    client(owner).post(
        "/sync/push", json={"activities": [_activity(shared_id, payload={"prompt": "mine"})]}
    )
    r = client(thief).post(
        "/sync/push", json={"activities": [_activity(shared_id, payload={"prompt": "stolen"})]}
    )
    assert str(shared_id) not in r.json()["ackIds"]
    row = session.get(Activity, shared_id)
    assert row.user_id == owner.id
    assert row.payload == {"prompt": "mine"}


# ---- AC-03.3 оценка job и error-log ----


def test_push_grades_job_and_builds_error_log(client, session, rubrics):
    user = make_user(session)
    body, aid, rid, jid = _essay_push("I recieve it")
    c = client(user)
    assert c.post("/sync/push", json=body).status_code == 200

    job = session.get(Job, jid)
    assert job.status == "done" and job.attempts == 1
    assert job.result["cardsCreated"] == 1

    response = session.get(Response, rid)
    assert response.grade["rubricId"] == "ielts_writing_task2"
    assert response.grade["rubricVersion"] == 1
    assert response.synced is True

    cards = session.query(SrsCard).filter_by(user_id=user.id, source="error_log").all()
    assert len(cards) == 1
    assert cards[0].back["correction"] == "receive"
    assert cards[0].fsrs_state["state"] == 0


def test_repeated_push_does_not_grade_twice(client, session, rubrics):
    user = make_user(session)
    body, _, _, jid = _essay_push("I recieve it")
    c = client(user)
    c.post("/sync/push", json=body)
    c.post("/sync/push", json=body)
    assert session.get(Job, jid).attempts == 1
    assert session.query(SrsCard).filter_by(user_id=user.id, source="error_log").count() == 1


def test_job_with_unknown_rubric_fails_with_reason(client, session, rubrics):
    user = make_user(session)
    body, _, _, jid = _essay_push()
    body["jobs"][0]["inputRef"]["rubricId"] = "no_such_rubric"
    client(user).post("/sync/push", json=body)
    job = session.get(Job, jid)
    assert job.status == "failed"
    assert "no_such_rubric" in job.result["error"]


def test_job_with_unknown_type_fails(client, session, rubrics):
    user = make_user(session)
    jid = uuid.uuid4()
    body = {"jobs": [{"id": str(jid), "type": "teleport", "inputRef": {}}]}
    client(user).post("/sync/push", json=body)
    assert session.get(Job, jid).status == "failed"


def test_jobs_list_and_manual_process_are_per_user(client, session, rubrics):
    a, b = make_user(session), make_user(session)
    body, _, _, jid = _essay_push()
    client(a).post("/sync/push", json=body)
    assert [j["id"] for j in client(a).get("/jobs").json()] == [str(jid)]
    assert client(b).get("/jobs").json() == []


# ---- pull ----


def test_pull_returns_only_own_data_and_done_jobs(client, session, rubrics):
    a, b = make_user(session), make_user(session)
    body, aid, rid, jid = _essay_push("I recieve it")
    client(a).post("/sync/push", json=body)

    pulled = client(a).get("/sync/pull").json()
    assert [x["id"] for x in pulled["activities"]] == [str(aid)]
    assert [x["id"] for x in pulled["jobs"]] == [str(jid)]
    assert pulled["responses"][0]["grade"]["rubricId"] == "ielts_writing_task2"
    assert any(c["source"] == "error_log" for c in pulled["srsCards"])

    empty = client(b).get("/sync/pull").json()
    assert all(empty[k] == [] for k in ("activities", "responses", "jobs"))


def test_push_srs_card_progress_is_stored(client, session):
    user = make_user(session)
    cid = uuid.uuid4()
    card = {
        "id": str(cid),
        "module": "languages",
        "front": {"word": "x"},
        "back": {"definition": "y"},
        "source": "awl",
        "fsrsState": {"reps": 3, "state": 2},
        "dueAt": NOW,
    }
    client(user).post("/sync/push", json={"srsCards": [card]})
    row = session.get(SrsCard, cid)
    assert row.fsrs_state == {"reps": 3, "state": 2}
    # Повторный push с новым прогрессом обновляет ту же карточку.
    client(user).post(
        "/sync/push", json={"srsCards": [{**card, "fsrsState": {"reps": 4, "state": 2}}]}
    )
    session.refresh(row)
    assert row.fsrs_state["reps"] == 4
    assert session.query(SrsCard).filter_by(user_id=user.id).count() == 1


# ---- core/srs ----


def test_errors_to_card_partials_shapes_cards():
    partials = errors_to_card_partials(
        [{"kind": "grammar", "excerpt": "he go", "correction": "he goes", "explanation": "s"}]
    )
    assert partials == [
        {
            "front": {"prompt": "Исправь: «he go»", "kind": "grammar"},
            "back": {"correction": "he goes", "explanation": "s"},
            "source": "error_log",
        }
    ]


def test_errors_to_card_partials_tolerates_missing_fields():
    assert errors_to_card_partials([{}])[0]["back"] == {"correction": "", "explanation": ""}
    assert errors_to_card_partials([]) == []


def test_initial_fsrs_state_is_new_card():
    now = datetime(2026, 9, 30, tzinfo=timezone.utc)
    state = initial_fsrs_state(now)
    assert state["state"] == 0 and state["reps"] == 0 and state["due"] == now.isoformat()


def test_insert_cards_sets_due_now_and_links_concept(session):
    user = make_user(session)
    now = datetime(2026, 9, 30, tzinfo=timezone.utc)
    cid = uuid.uuid4()
    n = insert_cards(
        session,
        user.id,
        "ml",
        [{"front": {"q": 1}, "back": {"a": 1}, "source": "generated", "concept_id": cid}],
        now,
    )
    session.flush()
    card = session.query(SrsCard).filter_by(user_id=user.id).one()
    assert n == 1 and card.due_at == now and card.concept_id == cid


# ---- LWW прогресса повторений (FR-SYNC-08) ----


def _card(cid, reps, updated_at=None):
    body = {
        "id": str(cid),
        "module": "languages",
        "front": {"word": "x"},
        "back": {"definition": "y"},
        "source": "awl",
        "fsrsState": {"reps": reps},
        "dueAt": NOW,
    }
    if updated_at:
        body["updatedAt"] = updated_at
    return body


def test_newer_card_version_wins(client, session):
    user = make_user(session)
    cid = uuid.uuid4()
    c = client(user)
    c.post("/sync/push", json={"srsCards": [_card(cid, 1, "2026-09-30T10:00:00+00:00")]})
    c.post("/sync/push", json={"srsCards": [_card(cid, 2, "2026-09-30T11:00:00+00:00")]})
    assert session.get(SrsCard, cid).fsrs_state["reps"] == 2


def test_older_card_version_is_ignored_but_acked(client, session):
    user = make_user(session)
    cid = uuid.uuid4()
    c = client(user)
    c.post("/sync/push", json={"srsCards": [_card(cid, 5, "2026-09-30T11:00:00+00:00")]})
    r = c.post("/sync/push", json={"srsCards": [_card(cid, 1, "2026-09-30T09:00:00+00:00")]})
    assert str(cid) in r.json()["ackIds"]  # клиенту повторять нечего
    assert session.get(SrsCard, cid).fsrs_state["reps"] == 5


def test_pull_returns_card_updated_at(client, session):
    user = make_user(session)
    cid = uuid.uuid4()
    client(user).post("/sync/push", json={"srsCards": [_card(cid, 1, "2026-09-30T10:00:00+00:00")]})
    [card] = client(user).get("/sync/pull").json()["srsCards"]
    assert card["updatedAt"].startswith("2026-09-30T10:00:00")


# ---- инкрементальный pull (T-0048) ----


def _since_now(session):
    """Время БД, а не хоста: часы контейнера Postgres могут расходиться с часами машины."""
    from sqlalchemy import text

    return session.execute(text("select clock_timestamp()")).scalar().isoformat()


def test_pull_returns_user_id_and_cursor(client, session):
    user = make_user(session)

    body = client(user).get("/sync/pull").json()

    assert body["userId"] == str(user.id)
    assert datetime.fromisoformat(body["cursor"]) <= datetime.now(timezone.utc)


def test_pull_since_returns_only_what_changed_after(client, session, rubrics):
    user = make_user(session)
    api = client(user)
    first, *_ = _essay_push("first")
    api.post("/sync/push", json=first)

    since = _since_now(session)
    second, aid2, rid2, _jid2 = _essay_push("second")
    api.post("/sync/push", json=second)

    pulled = api.get("/sync/pull", params={"since": since}).json()

    assert [a["id"] for a in pulled["activities"]] == [str(aid2)]
    assert [r["id"] for r in pulled["responses"]] == [str(rid2)]
    assert len(pulled["jobs"]) == 1
    assert all(c["source"] == "error_log" for c in pulled["srsCards"])
    # Без since — по-прежнему всё.
    assert len(api.get("/sync/pull").json()["activities"]) == 2


def test_pull_since_in_the_future_is_empty(client, session, rubrics):
    api = client(make_user(session))
    api.post("/sync/push", json=_essay_push()[0])

    body = api.get("/sync/pull", params={"since": "2999-01-01T00:00:00+00:00"}).json()

    assert body["activities"] == body["responses"] == body["jobs"] == body["srsCards"] == []


def test_pull_since_picks_up_grade_written_after_response_was_pushed(client, session, rubrics):
    """Ответ уже был у клиента без оценки; оценка пришла позже — строка меняется и снова видна."""
    user = make_user(session)
    api = client(user)
    payload, _aid, rid, _jid = _essay_push()
    api.post("/sync/push", json={**payload, "jobs": []})
    cursor = _since_now(session)

    response = session.get(Response, rid)
    response.grade = {"score": 7}
    session.flush()

    pulled = api.get("/sync/pull", params={"since": cursor}).json()

    assert [r["id"] for r in pulled["responses"]] == [str(rid)]
    assert pulled["responses"][0]["grade"] == {"score": 7}


def test_pull_since_card_change_is_visible_even_with_older_client_time(client, session):
    """Курсор строится по серверной метке, а не по client updated_at (LWW)."""
    user = make_user(session)
    api = client(user)
    cid = uuid.uuid4()
    card = {
        "id": str(cid),
        "module": "languages",
        "front": {"word": "w"},
        "back": {},
        "source": "awl",
        "fsrsState": {"reps": 1},
        "dueAt": NOW,
        "updatedAt": "2020-01-01T00:00:00+00:00",  # часы устройства отстают
    }
    since = _since_now(session)
    api.post("/sync/push", json={"srsCards": [card]})

    pulled = api.get("/sync/pull", params={"since": since}).json()

    assert [c["id"] for c in pulled["srsCards"]] == [str(cid)]
