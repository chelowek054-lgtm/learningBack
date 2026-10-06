"""domain_specialist: специалисты по областям (T-0081)

Revision ID: 0028_domain_specialist
Revises: 0027_concept_merge
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0028_domain_specialist"
down_revision: str | None = "0027_concept_merge"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "domain_specialist",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("domain", sa.String(), nullable=False),
        sa.Column("granted_by", pg.UUID(as_uuid=True), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("created_at", pg.TIMESTAMP(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("uq_domain_specialist", "domain_specialist", ["user_id", "domain"], unique=True)


def downgrade() -> None:
    op.drop_table("domain_specialist")
