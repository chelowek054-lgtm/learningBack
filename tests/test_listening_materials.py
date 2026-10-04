"""Материалы для аудирования: генерация, озвучка, курирование (T-0039, V-0061)."""

from __future__ import annotations

import pytest

from core import ai_gateway
from core.config import settings
from core.tts import MockTTS
from modules.languages import api, listening
from tests.conftest import make_user

PASSAGE = " ".join(["Maria usually takes the early train to work because the road is crowded."] * 4)


def good(**over):
    base = {
        "title": "Commute",
        "passage": PASSAGE,
        "confidence": 0.9,
        "questions": [
            {
                "prompt": "Why does Maria take the train?",
                "options": [
                    "The road is crowded",
                    "It is cheaper",
                    "She likes cars",
                    "It is faster",
                ],
                "answer": "The road is crowded",
                "explanation": "Stated in the text.",
            },
            {
                "prompt": "When does she travel?",
                "options": ["Early", "Late", "At night"],
                "answer": "Early",
            },
        ],
    }
    return {**base, **over}


class Gateway:
    def __init__(self, out):
        self.out = out

    def structured(self, *a, **k):
        return self.out


@pytest.fixture(autouse=True)
def _audio_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "audio_dir", str(tmp_path / "audio"))


class As:
    """Клиент от имени пользователя: подмена идёт на каждый запрос, а не один раз на всех."""

    def __init__(self, client, user):
        self._client, self._user = client, user

    def get(self, url, **kw):
        return self._client(self._user).get(url, **kw)

    def post(self, url, **kw):
        return self._client(self._user).post(url, **kw)


def use(monkeypatch, out):
    monkeypatch.setattr(api, "get_ai_gateway", lambda: Gateway(out))
    monkeypatch.setattr(api, "get_tts", lambda: MockTTS())


def test_bad_questions_are_dropped_and_confidence_falls(session):
    bad = good()["questions"] + [
        {"prompt": "x?", "options": ["a", "b", "c"], "answer": "z"},  # ответа нет среди вариантов
        {"prompt": "y?", "options": ["a", "a", "b"], "answer": "a"},  # варианты повторяются
    ]
    m = listening.generate(session, Gateway(good(questions=bad)), MockTTS(), "commuting")
    assert len(m.content["questions"]) == 2
    assert m.content["confidence"] == pytest.approx(0.45)
    assert m.content["status"] == "draft"
    assert listening.audio_file(m).read_bytes().startswith(b"MOCKAUDIO:")


@pytest.mark.parametrize(
    "out", [{}, good(passage="too short"), good(questions=[good()["questions"][0]])]
)
def test_unusable_model_output_is_rejected(session, out):
    with pytest.raises(listening.GenerationFailed):
        listening.generate(session, Gateway(out), MockTTS(), "commuting")


def test_learner_sees_only_approved_and_never_the_text(session, client, monkeypatch):
    use(monkeypatch, good())
    admin, learner = As(client, make_user(session, superuser=True)), As(client, make_user(session))
    made = admin.post("/languages/listening/generate", json={"topic": "commuting"})
    assert made.status_code == 201 and made.json()["passage"] == PASSAGE
    mid = made.json()["id"]

    assert learner.get("/languages/listening").json() == []
    assert learner.get(f"/languages/listening/{mid}/audio").status_code == 404

    assert admin.post(f"/languages/listening/{mid}/approve").json()["status"] == "approved"
    shown = learner.get("/languages/listening").json()
    assert [m["id"] for m in shown] == [mid] and "passage" not in shown[0]
    assert learner.get(f"/languages/listening/{mid}/audio").status_code == 200

    admin.post(f"/languages/listening/{mid}/reject")
    assert learner.get("/languages/listening").json() == []


def test_only_curator_generates_and_approves(session, client, monkeypatch):
    use(monkeypatch, good())
    learner = As(client, make_user(session))
    assert (
        learner.post("/languages/listening/generate", json={"topic": "commuting"}).status_code
        == 403
    )
    assert (
        learner.post(
            "/languages/listening/00000000-0000-0000-0000-000000000000/approve"
        ).status_code
        == 403
    )


def test_model_failure_is_422_and_provider_error_502(session, client, monkeypatch):
    admin = As(client, make_user(session, superuser=True))
    use(monkeypatch, {})
    assert (
        admin.post("/languages/listening/generate", json={"topic": "commuting"}).status_code == 422
    )

    class Down:
        def structured(self, *a, **k):
            raise ai_gateway.base.ProviderError("down")

    monkeypatch.setattr(api, "get_ai_gateway", lambda: Down())
    assert (
        admin.post("/languages/listening/generate", json={"topic": "commuting"}).status_code == 502
    )
