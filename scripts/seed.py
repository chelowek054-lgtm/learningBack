"""Seed демо-контента для разработки: рубрики, демо-пользователь, AWL, ML-материал, активности.

Рубрики и так добавляются при старте API (core.modules.sync_rubrics); здесь они
дублируются, чтобы seed работал и без запущенного API. Идемпотентно. Запуск: uv run python -m scripts.seed
"""

from datetime import datetime, timezone

from core.db import SessionLocal
from core.models import Activity, Material, User
from core.modules import sync_rubrics
from core.srs import insert_cards
from modules.languages.generators import awl_card_partials


def seed() -> None:
    now = datetime.now(timezone.utc)
    with SessionLocal() as s:
        user = s.query(User).first()
        if user is None:
            user = User(email="dev@example.com", profile={"role": "dev"})
            s.add(user)
            s.flush()

        sync_rubrics(s)

        # Стартовая колода AWL (если у пользователя ещё нет awl-карточек).
        from core.models import SrsCard

        has_awl = s.query(SrsCard).filter_by(user_id=user.id, source="awl").first()
        if has_awl is None:
            insert_cards(s, user.id, "languages", awl_card_partials(), now)

        # ML-материал.
        if s.query(Material).filter_by(source="seed").first() is None:
            s.add(
                Material(
                    user_id=user.id,
                    module="ml",
                    source="seed",
                    title="Scaled Dot-Product Attention",
                    content={
                        "text": "Attention делит скоры на sqrt(d_k) для стабилизации градиентов.",
                        "concepts": ["scaled dot-product attention", "softmax stability"],
                    },
                )
            )

        # Демо-активности (чтобы было что оценивать в MVP-флоу).
        if s.query(Activity).filter_by(user_id=user.id).first() is None:
            s.add(
                Activity(
                    user_id=user.id,
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
            s.add(
                Activity(
                    user_id=user.id,
                    module="ml",
                    type="concept_recall",
                    connectivity="online",
                    payload={
                        "prompt": "Почему attention масштабируют на sqrt(d_k)?",
                        "concept": "scaled dot-product attention",
                    },
                )
            )

        s.commit()
    print("seed: ok")


if __name__ == "__main__":
    seed()
