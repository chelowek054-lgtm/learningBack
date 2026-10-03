"""concept_link: предпосылки между понятиями разных областей со ступенью освоения

Revision ID: 0021_concept_link
Revises: 0020_domain_graph
Create Date: 2026-10-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0021_concept_link"
down_revision: str | None = "0020_domain_graph"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "concept_link",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("from_id", pg.UUID(as_uuid=True), sa.ForeignKey("concept.id"), nullable=False),
        sa.Column("to_id", pg.UUID(as_uuid=True), sa.ForeignKey("concept.id"), nullable=False),
        sa.Column("bloom", sa.String(), nullable=False),
        sa.Column(
            "created_at", pg.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("uq_concept_link_pair", "concept_link", ["from_id", "to_id"], unique=True)
    op.create_index("idx_concept_link_to", "concept_link", ["to_id"])


def downgrade() -> None:
    op.drop_index("idx_concept_link_to", table_name="concept_link")
    op.drop_index("uq_concept_link_pair", table_name="concept_link")
    op.drop_table("concept_link")
