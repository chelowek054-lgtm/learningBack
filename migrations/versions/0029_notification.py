"""notification: уведомления о курсе (T-0083)

Revision ID: 0029_notification
Revises: 0028_domain_specialist
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0029_notification"
down_revision: str | None = "0028_domain_specialist"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notification",
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
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("domain", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("body", sa.String(), nullable=False),
        sa.Column("data", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", pg.TIMESTAMP(timezone=True), server_default=sa.func.now()),
        sa.Column("read_at", pg.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index("idx_notification_user_unread", "notification", ["user_id", "read_at"])


def downgrade() -> None:
    op.drop_table("notification")
