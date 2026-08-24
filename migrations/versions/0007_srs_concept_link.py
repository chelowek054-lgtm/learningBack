"""srs: привязка карточки к узлу графа

Без неё удержание живёт отдельно от модели знаний: карточка не знает, какой
концепции она принадлежит, а значит слабый узел нельзя вернуть в повторение
адресно и нельзя связать error-log с графом (KG5-05).

Колонка nullable: карточки Фазы 1 (AWL, ошибки из эссе) к графу не привязаны и
такими остаются.

Revision ID: 0007_srs_concept_link
Revises: 0006_personal_domain
Create Date: 2026-08-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0007_srs_concept_link"
down_revision: str | None = "0006_personal_domain"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("srs_card", sa.Column("concept_id", pg.UUID(as_uuid=True), nullable=True))
    op.create_index("idx_srs_card_concept", "srs_card", ["user_id", "concept_id"])


def downgrade() -> None:
    op.drop_index("idx_srs_card_concept", table_name="srs_card")
    op.drop_column("srs_card", "concept_id")
