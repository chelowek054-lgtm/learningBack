"""client_error: необработанные ошибки клиента для мониторинга

Ошибки приложения на устройстве не попадают ни в логи сервера, ни в БД; без этого
о сбое узнают от пользователей. Таблица принимает отчёты клиента (`POST /v1/client-errors`).

Revision ID: 0017_client_error
Revises: 0016_sync_cursor
Create Date: 2026-10-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0017_client_error"
down_revision: str | None = "0016_sync_cursor"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "client_error",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("app_version", sa.String(), nullable=True),
        sa.Column("message", sa.String(), nullable=False),
        sa.Column("stack", sa.String(), nullable=True),
        sa.Column("fatal", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("context", pg.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column(
            "created_at", pg.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("idx_client_error_created", "client_error", ["created_at"])


def downgrade() -> None:
    op.drop_index("idx_client_error_created", table_name="client_error")
    op.drop_table("client_error")
