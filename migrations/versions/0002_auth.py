"""auth: user.password_hash + unique(email)

Revision ID: 0002_auth
Revises: 0001_init
Create Date: 2026-07-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_auth"
down_revision: str | None = "0001_init"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("user", sa.Column("password_hash", sa.String(), nullable=True))
    op.create_unique_constraint("uq_user_email", "user", ["email"])


def downgrade() -> None:
    op.drop_constraint("uq_user_email", "user", type_="unique")
    op.drop_column("user", "password_hash")
