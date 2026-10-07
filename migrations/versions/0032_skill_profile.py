"""skill_profile: профиль навыка человека по цели (T-0088, R-0048)

Revision ID: 0032_skill_profile
Revises: 0031_concept_stage
Create Date: 2026-10-07
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0032_skill_profile"
down_revision: str | None = "0031_concept_stage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "skill_profile",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("domain", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default=sa.text("'building'")),
        sa.Column("profile", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("created_at", pg.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", pg.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column("confirmed_at", pg.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index(
        "uq_skill_profile_user_domain", "skill_profile", ["user_id", "domain"], unique=True
    )


def downgrade() -> None:
    op.drop_table("skill_profile")
