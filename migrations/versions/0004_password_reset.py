"""password reset: таблица временных кодов, привязанных к пользователю

Revision ID: 0004_password_reset
Revises: 0003_knowledge_model
Create Date: 2026-08-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0004_password_reset"
down_revision: str | None = "0003_knowledge_model"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = pg.UUID(as_uuid=True)
_TS = pg.TIMESTAMP(timezone=True)
_GEN = sa.text("gen_random_uuid()")


def upgrade() -> None:
    op.create_table(
        "password_reset_code",
        sa.Column("id", _UUID, primary_key=True, server_default=_GEN),
        sa.Column("user_id", _UUID, sa.ForeignKey("user.id"), nullable=False),
        # 8 цифр строкой: ведущие нули значимы, арифметика над кодом не нужна.
        sa.Column("code", sa.String(length=8), nullable=False),
        sa.Column("expires_at", _TS, nullable=False),
        sa.Column("used_at", _TS, nullable=True),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_password_reset_user", "password_reset_code", ["user_id", "expires_at"])


def downgrade() -> None:
    op.drop_index("idx_password_reset_user", table_name="password_reset_code")
    op.drop_table("password_reset_code")
