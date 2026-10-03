"""domain_graph: граф областей с реестром ключей и связями «нужно знать до»

Уровень примитивности не хранится: он вычисляется как глубина области в графе (A-0022).

Revision ID: 0020_domain_graph
Revises: 0019_goal_intake
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0020_domain_graph"
down_revision: str | None = "0019_goal_intake"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "domain",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("foundation", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at", pg.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_table(
        "domain_alias",
        sa.Column("alias", sa.String(), primary_key=True),
        sa.Column(
            "domain_key",
            sa.String(),
            sa.ForeignKey("domain.key", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    op.create_table(
        "domain_edge",
        sa.Column(
            "domain_key",
            sa.String(),
            sa.ForeignKey("domain.key", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "prereq_key",
            sa.String(),
            sa.ForeignKey("domain.key", ondelete="CASCADE"),
            primary_key=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("domain_edge")
    op.drop_table("domain_alias")
    op.drop_table("domain")
