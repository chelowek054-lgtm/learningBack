"""push_device: устройства для push-уведомлений (T-0086)

Revision ID: 0030_push_device
Revises: 0029_notification
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0030_push_device"
down_revision: str | None = "0029_notification"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "push_device",
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
        sa.Column("token", sa.String(), nullable=False, unique=True),
        sa.Column("platform", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", pg.TIMESTAMP(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_push_device_user", "push_device", ["user_id"])


def downgrade() -> None:
    op.drop_table("push_device")
