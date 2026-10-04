"""Полный Academic Word List в стартовой колоде (T-0045, R-0024, V-0068)."""

from __future__ import annotations

import collections
from datetime import datetime, timedelta, timezone

from core.models import SrsCard
from modules.languages import backend
from modules.languages.generators import DAYS_BETWEEN_SUBLISTS, awl_card_partials, load_awl
from tests.conftest import make_user


def test_deck_holds_the_whole_list_not_a_demo():
    rows = load_awl()
    assert len(rows) == 570 and len({w for _, w, _ in rows}) == 570
    sizes = collections.Counter(s for s, _, _ in rows)
    assert [sizes[i] for i in range(1, 11)] == [60] * 9 + [30]  # 60 семейств × 9 + 30 в десятом


def test_every_card_has_a_gloss_and_a_sublist():
    for p in awl_card_partials():
        assert p["front"]["word"].strip() and p["back"]["definition"].strip()
        assert 1 <= p["back"]["sublist"] <= 10 and p["source"] == "awl"


def test_first_sublist_is_due_now_and_the_rest_come_later(session):
    user = make_user(session)
    now = datetime(2026, 10, 4, tzinfo=timezone.utc)
    backend.provision(session, user.id, {"id": "english", "title": "English B2"}, now)
    session.flush()
    cards = session.query(SrsCard).filter_by(user_id=user.id, source="awl").all()
    assert len(cards) == 570
    due_now = [c for c in cards if c.due_at <= now]
    assert len(due_now) == 60 and all(c.back["sublist"] == 1 for c in due_now)
    last = max(cards, key=lambda c: c.due_at)
    assert last.due_at == now + timedelta(days=9 * DAYS_BETWEEN_SUBLISTS)
    assert last.fsrs_state["due"] == last.due_at.isoformat()  # состояние FSRS не расходится с датой
