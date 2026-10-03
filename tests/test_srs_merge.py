"""Слияние карточек повторения с нескольких устройств (T-0029, R-0019, V-0055)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from core.models import SrsCard
from core.srs import incoming_wins, review_key
from tests.conftest import make_user


def state(last_review=None, reps=0, lapses=0, scheduled=1):
    s = {
        "due": "2026-10-10T00:00:00+00:00",
        "stability": 1,
        "difficulty": 5,
        "elapsed_days": 0,
        "scheduled_days": scheduled,
        "reps": reps,
        "lapses": lapses,
        "state": 2,
    }
    if last_review:
        s["last_review"] = last_review
    return s


def push(api, cid, fsrs, due="2026-10-10T00:00:00+00:00", updated=None):
    card = {
        "id": str(cid),
        "module": "x",
        "front": {"q": 1},
        "back": {"a": 1},
        "source": "generated",
        "fsrsState": fsrs,
        "dueAt": due,
    }
    if updated:
        card["updatedAt"] = updated
    return api.post("/sync/push", json={"srsCards": [card]})


def stored(session, cid):
    session.expire_all()
    return session.get(SrsCard, cid)


# ---- правило ----


def test_later_review_wins_regardless_of_write_order():
    older = state("2026-10-01T10:00:00+00:00", reps=3)
    newer = state("2026-10-02T10:00:00+00:00", reps=4)

    assert incoming_wins(newer, older) and not incoming_wins(older, newer)


def test_reviewed_card_beats_a_new_one_and_never_the_other_way():
    assert incoming_wins(state("2026-10-01T10:00:00+00:00", reps=1), state())
    assert not incoming_wins(state(), state("2026-10-01T10:00:00+00:00", reps=1))


def test_same_review_time_falls_back_to_reps_then_lapses():
    t = "2026-10-01T10:00:00+00:00"

    assert incoming_wins(state(t, reps=5), state(t, reps=4))
    assert incoming_wins(state(t, reps=5, lapses=2), state(t, reps=5, lapses=1))


def test_identical_state_is_not_taken_again():
    s = state("2026-10-01T10:00:00+00:00", reps=3)

    assert not incoming_wins(s, dict(s))


def test_garbage_in_review_time_does_not_crash_and_loses():
    assert review_key({"last_review": "вчера"})[0] == datetime.min.replace(tzinfo=timezone.utc)
    assert not incoming_wins({"last_review": "вчера"}, state("2026-10-01T10:00:00+00:00", reps=1))


def test_z_suffix_and_naive_times_are_comparable():
    assert incoming_wins(state("2026-10-02T10:00:00Z"), state("2026-10-01T10:00:00"))


# ---- сервер ----


def test_two_devices_converge_without_rolling_back_intervals(session, client):
    """Устройство B повторило позже, но синхронизировалось раньше; A приходит позже со старым ревью."""
    api = client(make_user(session))
    cid = uuid.uuid4()
    push(api, cid, state(), updated="2026-10-01T00:00:00+00:00")
    push(api, cid, state("2026-10-03T09:00:00+00:00", reps=2, scheduled=7))
    r = push(
        api,
        cid,
        state("2026-10-02T09:00:00+00:00", reps=1, scheduled=2),
        updated="2026-10-09T00:00:00+00:00",
    )

    assert r.status_code == 200 and str(cid) in r.json()["ackIds"]
    card = stored(session, cid)
    assert card.fsrs_state["reps"] == 2 and card.fsrs_state["scheduled_days"] == 7


def test_older_write_with_a_later_review_does_win(session, client):
    api = client(make_user(session))
    cid = uuid.uuid4()
    push(api, cid, state("2026-10-01T09:00:00+00:00", reps=1), updated="2026-10-09T00:00:00+00:00")

    push(api, cid, state("2026-10-05T09:00:00+00:00", reps=2), updated="2026-10-02T00:00:00+00:00")

    assert stored(session, cid).fsrs_state["reps"] == 2


def test_repeating_the_same_push_changes_nothing(session, client):
    api = client(make_user(session))
    cid = uuid.uuid4()
    s = state("2026-10-01T09:00:00+00:00", reps=1)
    push(api, cid, s)
    before = stored(session, cid).fsrs_state

    push(api, cid, dict(s))

    assert stored(session, cid).fsrs_state == before


def test_losing_version_is_acked_and_the_winner_comes_back_on_pull(session, client):
    api = client(make_user(session))
    cid = uuid.uuid4()
    push(api, cid, state("2026-10-03T09:00:00+00:00", reps=2))
    push(api, cid, state("2026-10-01T09:00:00+00:00", reps=1))

    pulled = api.get("/sync/pull").json()["srsCards"]

    assert [c["fsrsState"]["reps"] for c in pulled if c["id"] == str(cid)] == [2]


def test_responses_stay_immutable_and_conflict_free(session, client):
    api = client(make_user(session))
    aid, rid = uuid.uuid4(), uuid.uuid4()
    body = {
        "activities": [
            {"id": str(aid), "module": "x", "type": "t", "connectivity": "online", "payload": {}}
        ],
        "responses": [
            {
                "id": str(rid),
                "activityId": str(aid),
                "userAnswer": {"a": 1},
                "localCreatedAt": "2026-10-03T10:00:00+00:00",
            }
        ],
    }
    assert api.post("/sync/push", json=body).status_code == 200
    again = api.post("/sync/push", json=body)

    assert again.status_code == 200
    assert len(api.get("/sync/pull").json()["responses"]) == 1
