"""Устный ответ: запись → расшифровка → оценка по таймингам → ошибки в карточки (T-0040…T-0043)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from core import modules
from core.ai_mock import MockAIGateway
from core.config import settings
from core.jobs import process_job
from core.models import Activity, Job, Response, SrsCard
from core.stt import MockSTT, SpeechError, Transcript, Word
from modules.languages import speech
from tests.conftest import make_user

SPEECH = "I recieve many letters because I live in a quiet town near the sea"


@pytest.fixture(autouse=True)
def _voice_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "voice_dir", str(tmp_path / "voice"))


def _response(session, user):
    activity = Activity(
        user_id=user.id,
        module="languages",
        type="speaking_response",
        connectivity="online",
        payload={"prompt": "Describe your town.", "rubricId": "ielts_speaking"},
    )
    session.add(activity)
    session.flush()
    r = Response(
        activity_id=activity.id,
        user_id=user.id,
        user_answer={"audio": "pending"},
        local_created_at=datetime.now(timezone.utc),
    )
    session.add(r)
    session.flush()
    return r


def _transcribe_job(session, user, response, audio: bytes):
    audio_id = speech.store_voice(user.id, audio)
    job = Job(
        user_id=user.id,
        type="transcribe",
        status="pending",
        input_ref={"audioId": audio_id, "responseId": str(response.id), "mime": "audio/m4a"},
    )
    session.add(job)
    session.flush()
    return job, audio_id


def test_metrics_read_tempo_and_pauses_from_word_timings():
    words = [Word("a", 0, 0.5), Word("b", 0.6, 1.0), Word("c", 3.0, 3.4), Word("d", 3.5, 4.0)]
    m = speech.metrics(Transcript("a b c d", words, 4.0))
    assert m == {
        "words": 4,
        "durationSec": 4.0,
        "wordsPerMinute": 60,
        "longPauses": 1,
        "longestPauseSec": 2.0,
    }


@pytest.mark.parametrize("audio", [b"", b"\x00\x01noise", b"MOCKSPEECH:hi"])
def test_silent_or_empty_recording_is_an_understandable_error_not_an_empty_score(audio):
    with pytest.raises(SpeechError) as e:
        MockSTT().transcribe(audio, "")
    assert "запис" in str(e.value).lower()


def test_too_long_recording_is_rejected():
    long = Transcript("x " * 5, [Word("x", i * 50.0, i * 50.0 + 1) for i in range(5)], 250.0)
    from core.stt import check_transcript

    with pytest.raises(SpeechError, match="длиннее"):
        check_transcript(long)


def test_whole_chain_ends_with_grade_and_error_cards_and_no_voice_left(session):
    modules.sync_rubrics(session)
    user = make_user(session)
    response = _response(session, user)
    job, audio_id = _transcribe_job(session, user, response, b"MOCKSPEECH:" + SPEECH.encode())
    gateway = MockAIGateway()

    process_job(session, job, gateway)
    session.flush()
    assert job.status == "done" and job.result["voiceDeleted"] is True
    assert not speech.voice_path(user.id, audio_id).exists()  # голос удалён после расшифровки
    assert response.user_answer["transcript"] == SPEECH
    assert "слов/мин" in response.user_answer["asText"]

    grade_job = session.query(Job).filter_by(user_id=user.id, type="grade_speaking").one()
    process_job(session, grade_job, gateway)
    session.flush()
    assert grade_job.status == "done"
    assert response.grade["rubricId"] == "ielts_speaking"
    assert "приблизительный" in response.grade["caveat"]  # пометка о пределах оценки
    names = [c["name"] for c in response.grade["criteria"]]
    assert names[-1] == "Pronunciation"
    cards = session.query(SrsCard).filter_by(user_id=user.id, source="error_log").all()
    assert len(cards) == 1 and cards[0].front["prompt"].endswith("recieve»")


def test_failed_recording_fails_the_job_with_the_reason_and_still_deletes_nothing_wrong(session):
    user = make_user(session)
    response = _response(session, user)
    job, _ = _transcribe_job(session, user, response, b"\x00noise")
    process_job(session, job, MockAIGateway())
    assert job.status == "failed" and "запис" in job.result["error"].lower()
    assert session.query(Job).filter_by(type="grade_speaking").count() == 0


def test_foreign_recording_or_response_is_refused(session):
    owner, other = make_user(session), make_user(session)
    response = _response(session, owner)
    job, audio_id = _transcribe_job(session, other, response, b"MOCKSPEECH:" + SPEECH.encode())
    process_job(session, job, MockAIGateway())
    assert job.status == "failed"  # чужой ответ
    assert speech.voice_path(other.id, audio_id).exists()  # и запись не тронута

    bad = Job(
        user_id=owner.id,
        type="transcribe",
        status="pending",
        input_ref={"audioId": "../../etc/passwd", "responseId": str(response.id)},
    )
    session.add(bad)
    session.flush()
    process_job(session, bad, MockAIGateway())
    assert bad.status == "failed" and "audioId" in bad.result["error"]


def test_upload_stores_the_file_for_the_owner_and_rejects_empty(session, client):
    user = make_user(session)
    c = client(user)
    r = c.post(
        "/languages/speaking/audio",
        files={"file": ("a.m4a", b"MOCKSPEECH:one two three", "audio/m4a")},
    )
    assert r.status_code == 201
    assert speech.voice_path(user.id, r.json()["audioId"]).read_bytes().endswith(b"three")
    assert (
        c.post("/languages/speaking/audio", files={"file": ("a.m4a", b"", "audio/m4a")}).status_code
        == 422
    )
    assert uuid.UUID(r.json()["audioId"])


def test_language_subject_gets_a_speaking_task_once(session):
    from modules.languages import backend

    user = make_user(session)
    for _ in range(2):
        backend.provision(
            session, user.id, {"id": "english", "title": "English B2"}, datetime.now(timezone.utc)
        )
        session.flush()
    rows = session.query(Activity).filter_by(user_id=user.id, type="speaking_response").all()
    assert len(rows) == 1 and rows[0].payload["rubricId"] == "ielts_speaking"
    assert rows[0].connectivity == "offline"
