"""concept_merge: векторы понятий, решения по парам, очередь противоречий (T-0078)

Revision ID: 0027_concept_merge
Revises: 0026_provenance
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql as pg

revision: str = "0027_concept_merge"
down_revision: str | None = "0026_provenance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DIM = 1024


def upgrade() -> None:
    op.create_table(
        "concept_embedding",
        sa.Column(
            "concept_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("concept.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("text_hash", sa.String(), nullable=False),
        sa.Column("embedding", Vector(DIM), nullable=False),
        sa.Column("updated_at", pg.TIMESTAMP(timezone=True), server_default=sa.func.now()),
    )
    op.execute(
        "CREATE INDEX idx_concept_embedding_hnsw ON concept_embedding "
        "USING hnsw (embedding vector_cosine_ops)"
    )
    op.create_table(
        "merge_decision",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("pair_key", sa.String(), nullable=False, unique=True),
        sa.Column("verdict", sa.String(), nullable=False),
        sa.Column("general", sa.String(), nullable=False, server_default=sa.text("''")),
        sa.Column("reason", sa.String(), nullable=False, server_default=sa.text("''")),
        sa.Column("created_at", pg.TIMESTAMP(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "concept_conflict",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "a_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("concept.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "b_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("concept.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(), nullable=False, server_default=sa.text("''")),
        sa.Column("status", sa.String(), nullable=False, server_default=sa.text("'open'")),
        sa.Column("created_at", pg.TIMESTAMP(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("uq_concept_conflict_pair", "concept_conflict", ["a_id", "b_id"], unique=True)


def downgrade() -> None:
    op.drop_table("concept_conflict")
    op.drop_table("merge_decision")
    op.drop_table("concept_embedding")
