"""llm_cache: кэш детерминированных ответов LLM по хэшу входа

Раньше кэшировались только задания по узлу, хотя план Ф1 числил кэш
генерации сделанным. Оценка того же ответа по той же рубрике стоит денег
каждый раз, а результат от этого не меняется.

Revision ID: 0012_llm_cache
Revises: 0011_srs_card_updated_at
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0012_llm_cache"
down_revision: str | None = "0011_srs_card_updated_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_cache",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("payload", pg.JSONB(), nullable=False),
        sa.Column(
            "created_at", pg.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("llm_cache")
