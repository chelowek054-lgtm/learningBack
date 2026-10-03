"""policy_consent: версия политики данных, принятая при регистрации

Revision ID: 0024_policy_consent
Revises: 0023_module_consent
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0024_policy_consent"
down_revision: str | None = "0023_module_consent"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("user", sa.Column("policy_version", sa.String(), nullable=True))
    op.add_column(
        "user", sa.Column("policy_accepted_at", pg.TIMESTAMP(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("user", "policy_accepted_at")
    op.drop_column("user", "policy_version")
