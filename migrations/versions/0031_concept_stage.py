"""concept: этап, уровень сложности и необязательность понятия (T-0087, A-0030)

Revision ID: 0031_concept_stage
Revises: 0030_push_device
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031_concept_stage"
down_revision: str | None = "0030_push_device"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("concept", sa.Column("stage", sa.String(), nullable=True))
    op.add_column("concept", sa.Column("stage_order", sa.Integer(), nullable=True))
    op.add_column("concept", sa.Column("level", sa.String(), nullable=True))
    op.add_column(
        "concept",
        sa.Column("optional", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )


def downgrade() -> None:
    for column in ("optional", "level", "stage_order", "stage"):
        op.drop_column("concept", column)
