"""sync_cursor: серверная метка изменения для инкрементального pull

`GET /sync/pull` отдавал всё каждый раз. Для `?since=` нужна метка «когда строка
менялась на сервере»: у activity/response/srs_card её не было (у srs_card
`updated_at` — время клиента для LWW, ему верить для курсора нельзя). У job
`updated_at` ставит Python-код API, а не БД — часы могут расходиться. Источник времени
один: `clock_timestamp()` базы.

Revision ID: 0016_sync_cursor
Revises: 0015_user_concept_version
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0016_sync_cursor"
down_revision: str | None = "0015_user_concept_version"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLES = ("activity", "response", "srs_card", "job")


def upgrade() -> None:
    for table in TABLES:
        op.add_column(
            table,
            sa.Column(
                "server_updated_at",
                pg.TIMESTAMP(timezone=True),
                server_default=sa.text("clock_timestamp()"),
                nullable=False,
            ),
        )
        op.create_index(f"idx_{table}_user_server_updated", table, ["user_id", "server_updated_at"])


def downgrade() -> None:
    for table in TABLES:
        op.drop_index(f"idx_{table}_user_server_updated", table_name=table)
        op.drop_column(table, "server_updated_at")
