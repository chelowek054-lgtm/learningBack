"""goal_intake: подтверждённый итог диалога постановки цели

Граф области не строится, пока человек не подтвердил пересказ цели (R-0033). Хранится
только итог, не переписка.

Revision ID: 0019_goal_intake
Revises: 0018_module_state
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0019_goal_intake"
down_revision: str | None = "0018_module_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "goal_intake",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("domain", sa.String(), nullable=False),
        sa.Column("summary", pg.JSONB(), nullable=False),
        sa.Column("confirmed_at", pg.TIMESTAMP(timezone=True), nullable=False),
        sa.Column(
            "created_at", pg.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("uq_goal_intake_user_domain", "goal_intake", ["user_id", "domain"], unique=True)


def downgrade() -> None:
    op.drop_index("uq_goal_intake_user_domain", table_name="goal_intake")
    op.drop_table("goal_intake")
