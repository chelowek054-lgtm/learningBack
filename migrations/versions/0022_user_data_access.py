"""user_data_access: разрешения модулей на данные человека и журнал доступа

Revision ID: 0022_user_data_access
Revises: 0021_concept_link
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0022_user_data_access"
down_revision: str | None = "0021_concept_link"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "data_permission",
        sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("user.id"), primary_key=True),
        sa.Column("module_id", sa.String(), primary_key=True),
        sa.Column("data_type", sa.String(), primary_key=True),
        sa.Column("mode", sa.String(), primary_key=True),
        sa.Column("granted", sa.Boolean(), nullable=False),
        sa.Column(
            "updated_at", pg.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "data_access_log",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("module_id", sa.String(), nullable=False),
        sa.Column("data_type", sa.String(), nullable=False),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("purpose", sa.String(), server_default=sa.text("''"), nullable=False),
        sa.Column("allowed", sa.Boolean(), nullable=False),
        sa.Column("reason", sa.String(), server_default=sa.text("''"), nullable=False),
        sa.Column("at", pg.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_data_access_user_at", "data_access_log", ["user_id", "at"])


def downgrade() -> None:
    op.drop_index("idx_data_access_user_at", table_name="data_access_log")
    op.drop_table("data_access_log")
    op.drop_table("data_permission")
