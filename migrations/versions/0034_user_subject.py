"""user_subject: предмет человека со статусом (T-0119)

Revision ID: 0034_user_subject
Revises: 0033_domain_embedding
Create Date: 2026-10-11
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0034_user_subject"
down_revision: str | None = "0033_domain_embedding"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_subject",
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
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("target", sa.String(), nullable=False, server_default=sa.text("'understand'")),
        sa.Column("status", sa.String(), nullable=False, server_default=sa.text("'active'")),
        sa.Column("created_at", pg.TIMESTAMP(timezone=True), server_default=sa.func.now()),
    )
    op.create_index(
        "uq_user_subject_user_domain", "user_subject", ["user_id", "domain"], unique=True
    )


def downgrade() -> None:
    op.drop_table("user_subject")
