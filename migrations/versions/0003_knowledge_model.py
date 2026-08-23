"""knowledge model: concept, concept_edge, user_concept, user_edge, assessment, course

Revision ID: 0003_knowledge_model
Revises: 0002_auth
Create Date: 2026-07-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0003_knowledge_model"
down_revision: str | None = "0002_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = pg.UUID(as_uuid=True)
_TS = pg.TIMESTAMP(timezone=True)
_GEN = sa.text("gen_random_uuid()")


def upgrade() -> None:
    op.create_table(
        "concept",
        sa.Column("id", _UUID, primary_key=True, server_default=_GEN),
        sa.Column("domain", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("tier", sa.String(), server_default=sa.text("'derived'"), nullable=False),
        sa.Column("centrality", sa.Float(), server_default=sa.text("0"), nullable=False),
        sa.Column("content", pg.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("bloom_levels", pg.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("difficulty", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("source", sa.String(), server_default=sa.text("'llm'"), nullable=False),
        sa.Column("confidence", sa.Float(), server_default=sa.text("0"), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("status", sa.String(), server_default=sa.text("'draft'"), nullable=False),
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_concept_domain_tier", "concept", ["domain", "tier"])

    op.create_table(
        "concept_edge",
        sa.Column("id", _UUID, primary_key=True, server_default=_GEN),
        sa.Column("from_id", _UUID, sa.ForeignKey("concept.id"), nullable=False),
        sa.Column("to_id", _UUID, sa.ForeignKey("concept.id"), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
    )
    op.create_index("idx_concept_edge_from", "concept_edge", ["from_id"])

    op.create_table(
        "user_concept",
        sa.Column("id", _UUID, primary_key=True, server_default=_GEN),
        sa.Column("user_id", _UUID, sa.ForeignKey("user.id"), nullable=False),
        sa.Column("base_concept_id", _UUID, sa.ForeignKey("concept.id"), nullable=True),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("content_override", pg.JSONB(), nullable=True),
        sa.Column("mastery", pg.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("status", sa.String(), server_default=sa.text("'locked'"), nullable=False),
        sa.Column("origin", sa.String(), server_default=sa.text("'inherited'"), nullable=False),
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", _TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_user_concept_user", "user_concept", ["user_id", "base_concept_id"])

    op.create_table(
        "user_edge",
        sa.Column("id", _UUID, primary_key=True, server_default=_GEN),
        sa.Column("user_id", _UUID, sa.ForeignKey("user.id"), nullable=False),
        sa.Column("from_id", _UUID, nullable=False),
        sa.Column("to_id", _UUID, nullable=False),
        sa.Column("type", sa.String(), nullable=False),
    )
    op.create_index("idx_user_edge_user", "user_edge", ["user_id"])

    op.create_table(
        "assessment",
        sa.Column("id", _UUID, primary_key=True, server_default=_GEN),
        sa.Column("concept_id", _UUID, nullable=False),
        sa.Column("concept_version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("bloom", sa.String(), nullable=False),
        sa.Column("payload", pg.JSONB(), nullable=False),
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_assessment_concept", "assessment", ["concept_id", "concept_version"])

    op.create_table(
        "course",
        sa.Column("id", _UUID, primary_key=True, server_default=_GEN),
        sa.Column("user_id", _UUID, sa.ForeignKey("user.id"), nullable=False),
        sa.Column("domain", sa.String(), nullable=False),
        sa.Column("target", pg.JSONB(), nullable=False),
        sa.Column("path", pg.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("progress", pg.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("course")
    op.drop_index("idx_assessment_concept", table_name="assessment")
    op.drop_table("assessment")
    op.drop_index("idx_user_edge_user", table_name="user_edge")
    op.drop_table("user_edge")
    op.drop_index("idx_user_concept_user", table_name="user_concept")
    op.drop_table("user_concept")
    op.drop_index("idx_concept_edge_from", table_name="concept_edge")
    op.drop_table("concept_edge")
    op.drop_index("idx_concept_domain_tier", table_name="concept")
    op.drop_table("concept")
