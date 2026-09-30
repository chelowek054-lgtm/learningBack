"""srs_card.updated_at: LWW для прогресса повторений

Без времени изменения сервер не может решить, чья версия карточки новее, а
значит не может принимать прогресс FSRS с клиента: любой push либо затирал бы
более свежее состояние, либо игнорировался. Существующие строки получают
`created_at` как момент последнего изменения.

Revision ID: 0011_srs_card_updated_at
Revises: 0010_job_retry_after
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0011_srs_card_updated_at"
down_revision: str | None = "0010_job_retry_after"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "srs_card",
        sa.Column(
            "updated_at", pg.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.execute("UPDATE srs_card SET updated_at = created_at")


def downgrade() -> None:
    op.drop_column("srs_card", "updated_at")
