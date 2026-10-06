"""Push-уведомления о курсе вне приложения (T-0086, R-0044)."""

from __future__ import annotations

import httpx
import pytest

from core import push
from core.config import settings
from core.models import Job, PushDevice
from modules.knowledge import notifications, provenance
from modules.knowledge.models import Notification
from tests.conftest import make_user
from tests.test_notifications import DOMAIN, As, concept, learner_with_course

TOKEN = "ExponentPushToken[abcdefghijklmnop]"
TOKEN2 = "ExponentPushToken[zyxwvutsrqponmlk]"


class FakeSender:
    def __init__(self, fail: Exception | None = None, results=None):
        self.calls: list[list[dict]] = []
        self.fail = fail
        self.results = results

    def send(self, messages):
        self.calls.append(messages)
        if self.fail:
            raise self.fail
        return self.results or [{"status": "ok"} for _ in messages]


@pytest.fixture
def sender():
    s = FakeSender()
    push.set_sender(s)
    yield s
    push.set_sender(None)


def device(session, user, token=TOKEN, enabled=True):
    d = PushDevice(user_id=user.id, token=token, platform="android", enabled=enabled)
    session.add(d)
    session.flush()
    return d


# ---- устройства ----


def test_registration_validates_the_token_and_platform(session):
    user = make_user(session)
    with pytest.raises(push.PushError) as e:
        push.register(session, user.id, "not-a-token", "android")
    assert e.value.code == "bad_token"
    with pytest.raises(push.PushError) as e:
        push.register(session, user.id, TOKEN, "symbian")
    assert e.value.code == "bad_platform"
    assert push.register(session, user.id, TOKEN, "ios").enabled is True


def test_token_moves_to_the_current_user_and_is_not_duplicated(session):
    a, b = make_user(session), make_user(session)
    push.register(session, a.id, TOKEN, "android")
    push.register(session, b.id, TOKEN, "android")  # тот же телефон, другой аккаунт
    rows = session.query(PushDevice).all()
    assert len(rows) == 1 and rows[0].user_id == b.id


def test_unregister_removes_only_own_device(session):
    a, b = make_user(session), make_user(session)
    device(session, a)
    assert push.unregister(session, b.id, TOKEN) is False
    assert push.unregister(session, a.id, TOKEN) is True
    assert session.query(PushDevice).count() == 0


def test_disabling_turns_off_every_device(session):
    user = make_user(session)
    device(session, user)
    device(session, user, TOKEN2)
    assert push.set_enabled(session, user.id, False) == 2
    assert push.state(session, user.id)["enabled"] is False
    push.set_enabled(session, user.id, True)
    assert push.state(session, user.id)["enabled"] is True


# ---- отправка ----


def test_new_course_notice_is_pushed_with_a_generic_text(session, sender):
    user = make_user(session)
    device(session, user)

    note = notifications.course_ready(session, user.id, DOMAIN, steps=3, drafts=3)

    (batch,) = sender.calls
    msg = batch[0]
    assert (
        msg["to"] == TOKEN and msg["title"] == "Курс готов" and msg["body"] == f"Область: {DOMAIN}"
    )
    # ни названий понятий, ни пометки о черновиках, ни источников в push нет
    assert "Черновых" not in msg["body"] and msg["data"]["notificationId"] == str(note.id)
    job = session.query(Job).filter_by(type=push.JOB_TYPE).one()
    assert job.status == "done" and job.result == {"sent": 1, "removed": 0}


def test_without_consent_nothing_is_sent(session, sender):
    user = make_user(session)  # устройств нет
    notifications.course_ready(session, user.id, DOMAIN, 1, 0)
    assert sender.calls == [] and session.query(Job).filter_by(type=push.JOB_TYPE).count() == 0


def test_disabled_push_is_not_sent(session, sender):
    user = make_user(session)
    device(session, user, enabled=False)
    notifications.course_ready(session, user.id, DOMAIN, 1, 0)
    assert sender.calls == []


def test_provider_off_sends_nothing(session, monkeypatch):
    monkeypatch.setattr(settings, "push_provider", "off")
    user = make_user(session)
    device(session, user)
    notifications.course_ready(session, user.id, DOMAIN, 1, 0)
    assert session.query(Job).filter_by(type=push.JOB_TYPE).count() == 0


def test_accumulating_into_an_unread_notice_does_not_push_again(session, sender):
    user, _ = learner_with_course(session)
    device(session, user)

    notifications.course_extended(session, DOMAIN, 2, 2)
    notifications.course_extended(session, DOMAIN, 3, 1)

    assert len(sender.calls) == 1  # второе дополнение прибавилось к непрочитанному и молчит


def test_verification_push_names_neither_concept_nor_source(session, sender):
    user, _ = learner_with_course(session)
    device(session, user)
    c = concept(session, "Secret title")
    # курс уже собран без этого понятия, поэтому проверяем то, что в курсе
    from modules.knowledge.models import Concept

    group = session.query(Concept).filter_by(title="Group").one()
    provenance.review_concept(session, group, None, "approve")

    msgs = [m for batch in sender.calls for m in batch]
    assert msgs and all("Group" not in m["body"] and "Group" not in m["title"] for m in msgs)
    assert c.id is not None


def test_dead_token_is_removed_and_the_good_one_kept(session):
    s = FakeSender(
        results=[
            {"status": "error", "message": "x", "details": {"error": "DeviceNotRegistered"}},
            {"status": "ok"},
        ]
    )
    push.set_sender(s)
    try:
        user = make_user(session)
        device(session, user, TOKEN)
        device(session, user, TOKEN2)
        notifications.course_ready(session, user.id, DOMAIN, 1, 0)
    finally:
        push.set_sender(None)
    assert [d.token for d in session.query(PushDevice).all()] == [TOKEN2]


def test_provider_failure_does_not_break_the_notice_and_the_job_waits_for_retry(session):
    push.set_sender(FakeSender(fail=httpx.ConnectError("нет сети")))
    try:
        user = make_user(session)
        device(session, user)
        note = notifications.course_ready(session, user.id, DOMAIN, 1, 0)
    finally:
        push.set_sender(None)
    assert note is not None and session.query(Notification).count() == 1  # курс и уведомление целы
    job = session.query(Job).filter_by(type=push.JOB_TYPE).one()
    assert job.status == "pending" and job.attempts == 1 and job.retry_after is not None


def test_already_read_notice_is_not_pushed(session, sender):
    from core.jobs import process_job

    user = make_user(session)
    note = notifications.course_ready(session, user.id, DOMAIN, 1, 0)  # устройств ещё нет
    device(session, user)
    notifications.mark_read(session, user.id)
    job = Job(
        user_id=user.id,
        type=push.JOB_TYPE,
        status="pending",
        input_ref={"notificationId": str(note.id)},
    )
    session.add(job)
    session.flush()

    process_job(session, job, None)

    assert job.status == "done" and job.result["skipped"] == "already_read" and sender.calls == []


def test_missing_notice_fails_the_job_for_good(session, sender):
    from core.jobs import process_job

    user = make_user(session)
    job = Job(
        user_id=user.id,
        type=push.JOB_TYPE,
        status="pending",
        input_ref={"notificationId": "00000000-0000-0000-0000-000000000000"},
    )
    session.add(job)
    session.flush()
    process_job(session, job, None)
    assert job.status == "failed"


def test_expo_sender_posts_a_batch_and_checks_the_reply(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["body"] = str(request.url), request.content
        return httpx.Response(200, json={"data": [{"status": "ok", "id": "1"}]})

    out = push.ExpoSender(httpx.MockTransport(handler)).send(
        [{"to": TOKEN, "title": "t", "body": "b"}]
    )

    assert out == [{"status": "ok", "id": "1"}] and seen["url"] == push.EXPO_URL
    bad = httpx.MockTransport(lambda r: httpx.Response(200, json={"data": []}))
    with pytest.raises(RuntimeError):
        push.ExpoSender(bad).send([{"to": TOKEN}])
    down = httpx.MockTransport(lambda r: httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        push.ExpoSender(down).send([{"to": TOKEN}])


# ---- API и удаление аккаунта ----


def test_api_register_toggle_and_remove(session, client):
    api = As(client, make_user(session))

    assert api.post("/push/devices", json={"token": "bad", "platform": "ios"}).status_code in (422,)
    ok = api.post("/push/devices", json={"token": TOKEN, "platform": "android"})
    assert ok.status_code == 201 and ok.json()["devices"] == 1 and ok.json()["enabled"] is True

    off = api.post("/push/enabled", json={"enabled": False}).json()
    assert off["enabled"] is False and off["devices"] == 1
    assert api.get("/push").json()["devices"] == 1

    assert api.post("/push/devices/remove", json={"token": TOKEN}).json() == {"removed": True}
    assert api.get("/push").json()["devices"] == 0


def test_account_deletion_removes_devices(session):
    from core import userdata

    user = make_user(session)
    device(session, user)
    userdata.delete_account(session, user.id, {t.id: t for t in userdata.core_types()})
    assert session.query(PushDevice).count() == 0
