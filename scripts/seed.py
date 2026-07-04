"""Seed демо-контента (WS4): рубрики, стартовая колода AWL, ML-материал, демо-активности.

Идемпотентно. Запуск: uv run python -m scripts.seed
"""

from datetime import datetime, timezone

from core.db import SessionLocal
from core.models import Activity, Material, Rubric, User
from core.srs import insert_cards
from modules.languages.generators import awl_card_partials
from modules.languages.rubrics import RUBRICS as LANG_RUBRICS
from modules.ml.rubrics import RUBRICS as ML_RUBRICS


def _seed_rubrics(s) -> None:
    # Upsert: обновляем поля существующей рубрики (перекрывает старые заглушки Ф0).
    for r in [*LANG_RUBRICS, *ML_RUBRICS]:
        obj = s.query(Rubric).filter_by(id=r["id"], version=r["version"]).first()
        if obj is None:
            s.add(Rubric(**r))
        else:
            obj.module, obj.model, obj.prompt, obj.schema = (
                r["module"],
                r["model"],
                r["prompt"],
                r["schema"],
            )


def seed() -> None:
    now = datetime.now(timezone.utc)
    with SessionLocal() as s:
        user = s.query(User).first()
        if user is None:
            user = User(email="dev@example.com", profile={"role": "dev"})
            s.add(user)
            s.flush()

        _seed_rubrics(s)

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
