"""provenance: источники, фрагменты, связи знаний с источниками, статус связей, журнал проверки (T-0076)

Revision ID: 0026_provenance
Revises: 0025_graph_extensions
Create Date: 2026-10-06
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0026_provenance"
down_revision: str | None = "0025_graph_extensions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "source_document",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("object_key", sa.String(), nullable=True),
        sa.Column("content_hash", sa.String(), nullable=False, unique=True),
        sa.Column("origin_url", sa.String(), nullable=True),
        sa.Column("license", sa.String(), nullable=True),
        sa.Column("domain", sa.String(), nullable=True),
        sa.Column("level", sa.String(), nullable=True),
        sa.Column("meta", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("added_by", pg.UUID(as_uuid=True), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("created_at", pg.TIMESTAMP(timezone=True), server_default=sa.func.now()),
    )
    op.create_table(
        "source_fragment",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "document_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("source_document.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("heading", sa.String(), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
    )
    op.create_index("idx_source_fragment_doc", "source_fragment", ["document_id", "ordinal"])
    op.create_table(
        "concept_source",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "concept_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("concept.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "fragment_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("source_fragment.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(), nullable=False, server_default=sa.text("'definition'")),
    )
    op.create_index(
        "uq_concept_source", "concept_source", ["concept_id", "fragment_id", "role"], unique=True
    )
    op.create_table(
        "edge_source",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "edge_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("concept_edge.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "fragment_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("source_fragment.id", ondelete="CASCADE"),
            nullable=False,
        ),
    )
    op.create_index("uq_edge_source", "edge_source", ["edge_id", "fragment_id"], unique=True)
    op.create_table(
        "review_log",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("target_type", sa.String(), nullable=False),
        sa.Column("target_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("reviewer_id", pg.UUID(as_uuid=True), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("created_at", pg.TIMESTAMP(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("idx_review_log_target", "review_log", ["target_type", "target_id"])

    # Статус связи. Существующие связи подтверждены, если подтверждены оба их конца: иначе
    # старый граф превратился бы весь в «черновик», хотя куратор его уже проверил.
    op.add_column(
        "concept_edge",
        sa.Column("status", sa.String(), nullable=False, server_default=sa.text("'draft'")),
    )
    op.execute(
        """
        UPDATE concept_edge e SET status = 'approved'
        WHERE EXISTS (SELECT 1 FROM concept c WHERE c.id = e.from_id AND c.status = 'approved')
          AND EXISTS (SELECT 1 FROM concept c WHERE c.id = e.to_id AND c.status = 'approved')
        """
    )


def downgrade() -> None:
    op.drop_column("concept_edge", "status")
    op.drop_table("review_log")
    op.drop_table("edge_source")
    op.drop_table("concept_source")
    op.drop_table("source_fragment")
    op.drop_table("source_document")
