"""утверждения, источники утверждений, проблемы, словарь терминов, области понятий, учебные задания (T-0149)

Revision ID: 0035_claims
Revises: 0034_user_subject
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0035_claims"
down_revision: str | None = "0034_user_subject"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ID = dict(primary_key=True, server_default=sa.text("gen_random_uuid()"))
_TS = dict(server_default=sa.func.now())


def upgrade() -> None:
    op.add_column("source_document", sa.Column("lang", sa.String(), nullable=True))
    op.add_column("source_document", sa.Column("trust_tier", sa.String(), nullable=True))
    op.add_column(
        "source_document",
        sa.Column(
            "trust_reasons", pg.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
    )
    op.add_column(
        "source_document",
        sa.Column("quarantined", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )

    op.create_table(
        "claim",
        sa.Column("id", pg.UUID(as_uuid=True), **_ID),
        sa.Column(
            "concept_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("concept.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(), nullable=False, server_default=sa.text("'property'")),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_i18n", pg.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("status", sa.String(), nullable=False, server_default=sa.text("'proposed'")),
        sa.Column("applicability", sa.String(), nullable=True),
        sa.Column("volatility", sa.String(), nullable=False, server_default=sa.text("'stable'")),
        sa.Column("as_of", pg.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("review_after", pg.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("replaced_by", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", pg.TIMESTAMP(timezone=True), **_TS),
    )
    op.create_index("idx_claim_concept", "claim", ["concept_id", "status"])

    op.create_table(
        "claim_source",
        sa.Column("id", pg.UUID(as_uuid=True), **_ID),
        sa.Column(
            "claim_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("claim.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "fragment_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("source_fragment.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("char_start", sa.Integer(), nullable=True),
        sa.Column("char_end", sa.Integer(), nullable=True),
        sa.Column("verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_index("uq_claim_source", "claim_source", ["claim_id", "fragment_id"], unique=True)

    op.create_table(
        "claim_conflict",
        sa.Column("id", pg.UUID(as_uuid=True), **_ID),
        sa.Column(
            "a_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("claim.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "b_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("claim.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(), nullable=False, server_default=sa.text("''")),
        sa.Column("status", sa.String(), nullable=False, server_default=sa.text("'open'")),
        sa.Column("resolution", sa.String(), nullable=True),
        sa.Column("resolved_by", pg.UUID(as_uuid=True), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("created_at", pg.TIMESTAMP(timezone=True), **_TS),
    )
    op.create_index("idx_claim_conflict_status", "claim_conflict", ["status"])

    op.create_table(
        "concept_term",
        sa.Column("id", pg.UUID(as_uuid=True), **_ID),
        sa.Column(
            "concept_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("concept.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("lang", sa.String(), nullable=False),
        sa.Column("term", sa.String(), nullable=False),
        sa.Column("primary", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_index("uq_concept_term", "concept_term", ["concept_id", "lang", "term"], unique=True)
    op.create_index("idx_concept_term_lookup", "concept_term", ["lang", "term"])

    op.create_table(
        "concept_area",
        sa.Column("id", pg.UUID(as_uuid=True), **_ID),
        sa.Column(
            "concept_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("concept.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("domain", sa.String(), nullable=False),
        sa.Column("stage", sa.String(), nullable=True),
        sa.Column("stage_order", sa.Integer(), nullable=True),
        sa.Column("level", sa.String(), nullable=True),
        sa.Column("optional", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_index("uq_concept_area", "concept_area", ["concept_id", "domain"], unique=True)

    op.create_table(
        "learning_item",
        sa.Column("id", pg.UUID(as_uuid=True), **_ID),
        sa.Column(
            "concept_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("concept.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("concept_version", sa.Integer(), nullable=False),
        sa.Column("lang", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("payload", pg.JSONB(), nullable=False),
        sa.Column("answer_key", pg.JSONB(), nullable=False),
        sa.Column("claim_ids", pg.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("created_at", pg.TIMESTAMP(timezone=True), **_TS),
    )
    op.create_index(
        "idx_learning_item_concept", "learning_item", ["concept_id", "lang", "concept_version"]
    )


def downgrade() -> None:
    for table in (
        "learning_item",
        "concept_area",
        "concept_term",
        "claim_conflict",
        "claim_source",
        "claim",
    ):
        op.drop_table(table)
    for col in ("quarantined", "trust_reasons", "trust_tier", "lang"):
        op.drop_column("source_document", col)
