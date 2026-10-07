"""domain_embedding: вектор области для сопоставления по близости (T-0096, A-0031)

Revision ID: 0033_domain_embedding
Revises: 0032_skill_profile
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql as pg

revision: str = "0033_domain_embedding"
down_revision: str | None = "0032_skill_profile"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DIM = 1024


def upgrade() -> None:
    op.create_table(
        "domain_embedding",
        sa.Column(
            "domain_key",
            sa.String(),
            sa.ForeignKey("domain.key", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("text_hash", sa.String(), nullable=False),
        sa.Column("embedding", Vector(DIM), nullable=False),
        sa.Column("updated_at", pg.TIMESTAMP(timezone=True), server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("domain_embedding")
