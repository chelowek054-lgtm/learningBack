"""Провижининг нового пользователя (WS8): стартовая колода + демо-активности,
чтобы приложение было полезно с первого входа."""

import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from core.models import Activity
from core.srs import insert_cards
from modules.languages.generators import awl_card_partials


def provision_new_user(session: Session, user_id: uuid.UUID, now: datetime) -> None:
    # Стартовая колода AWL.
    insert_cards(session, user_id, "languages", awl_card_partials(), now)

    # Демо-активности по обоим доменам.
    session.add(
        Activity(
            user_id=user_id,
            module="languages",
            type="ielts_writing_task2",
            connectivity="online",
            payload={
                "prompt": (
                    "Some people believe technology makes life more complex. "
                    "To what extent do you agree or disagree?"
                )
            },
        )
    )
    session.add(
        Activity(
            user_id=user_id,
            module="ml",
            type="concept_recall",
            connectivity="online",
            payload={
                "prompt": "Почему attention масштабируют на sqrt(d_k)?",
                "concept": "scaled dot-product attention",
            },
        )
    )
    session.add(
        Activity(
            user_id=user_id,
            module="ml",
            type="material_read",
            connectivity="offline",
            payload={
                "title": "Scaled Dot-Product Attention",
                "text": "Attention делит скоры на sqrt(d_k) для стабилизации градиентов.",
            },
        )
    )
