"""auth_hardening: хеш кода восстановления и версия сессий пользователя

Код восстановления лежал в БД открытым текстом, а прежний токен оставался
годным после смены пароля. Теперь хранится HMAC-хеш кода, а у пользователя
есть версия сессий, которую смена пароля увеличивает.

Действующие коды короткоживущие (15 минут), поэтому не переносятся:
открытый текст хешировать нельзя без сохранения его в БД ещё раз.

Revision ID: 0013_auth_hardening
Revises: 0012_llm_cache
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_auth_hardening"
down_revision: str | None = "0012_llm_cache"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("DELETE FROM password_reset_code")
    op.alter_column("password_reset_code", "code", new_column_name="code_hash")
    op.alter_column(
        "password_reset_code", "code_hash", type_=sa.String(64), existing_type=sa.String(8)
    )
    op.add_column(
        "user",
        sa.Column("token_version", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("user", "token_version")
    op.execute("DELETE FROM password_reset_code")
    op.alter_column(
        "password_reset_code", "code_hash", type_=sa.String(8), existing_type=sa.String(64)
    )
    op.alter_column("password_reset_code", "code_hash", new_column_name="code")
