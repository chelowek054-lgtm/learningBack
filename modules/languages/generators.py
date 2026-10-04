"""Генераторы контента модуля «Языки». WS4.

AWL — стартовая колода Academic Word List: все 570 заглавных слов (Coxhead, 2000) по десяти
подсписками. Список слов — по официальному документу университета Виктории в Веллингтоне
(Headwords of the Academic Word List); русские пояснения кратки и составлены для этой колоды,
их стоит вычитать. error→card — через общий core.srs.errors_to_card_partials.
"""

from pathlib import Path
from typing import Any

_DATA = Path(__file__).parent / "data" / "awl.tsv"
# Подсписок n становится к повторению через (n - 1) × столько дней: 570 новых карточек разом —
# это отказ от колоды в первый же день, а подсписки идут от самых частотных слов.
DAYS_BETWEEN_SUBLISTS = 3


def load_awl() -> list[tuple[int, str, str]]:
    """(подсписок, слово, пояснение) в порядке подсписков."""
    rows: list[tuple[int, str, str]] = []
    for line in _DATA.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        sublist, word, gloss = line.split("\t")
        rows.append((int(sublist), word, gloss))
    return rows


def awl_card_partials() -> list[dict[str, Any]]:
    """Заготовки карточек AWL (front/back/source): первый подсписок сразу, остальные позже."""
    return [
        {
            "front": {"word": w},
            "back": {"definition": d, "sublist": s},
            "source": "awl",
            "due_in_days": (s - 1) * DAYS_BETWEEN_SUBLISTS,
        }
        for s, w, d in load_awl()
    ]
