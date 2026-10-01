"""user_concept_version: версия персонального узла

resolve_node всегда отдавал свои узлы с version=1, а правка версию не меняла,
поэтому задания по ним нельзя было ни кэшировать, ни обесценивать. Теперь
версия растёт с каждой правкой заголовка или теории — как у канона.

Revision ID: 0015_user_concept_version
Revises: 0014_concept_key
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0015_user_concept_version"
down_revision: str | None = "0014_concept_key"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_concept",
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("user_concept", "version")
