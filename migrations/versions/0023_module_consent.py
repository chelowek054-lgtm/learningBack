"""module_consent: согласие на запросы и сеть модуля

Расширение разрешений при обновлении модуля требует нового согласия (R-0030).

Revision ID: 0023_module_consent
Revises: 0022_user_data_access
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0023_module_consent"
down_revision: str | None = "0022_user_data_access"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("module_state", sa.Column("approved", pg.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("module_state", "approved")
