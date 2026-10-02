"""module_state: состояние подключённых модулей (версия, включён, подключён)

Жизненный цикл модуля (C-0001): отключение и обновление — решения на работающей
системе, их нельзя держать в конфиге. Данные модуля при отключении не трогаются.

Revision ID: 0018_module_state
Revises: 0017_client_error
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0018_module_state"
down_revision: str | None = "0017_client_error"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "module_state",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("previous_version", sa.String(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("installed", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at", pg.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", pg.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("module_state")
