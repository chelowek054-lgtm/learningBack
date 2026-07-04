"""init: 7 таблиц Praxis (user, activity, response, srs_card, job, material, rubric)

Revision ID: 0001_init
Revises:
Create Date: 2026-07-04
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0001_init"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = pg.UUID(as_uuid=True)
_TS = pg.TIMESTAMP(timezone=True)
_GEN_UUID = sa.text("gen_random_uuid()")


def upgrade() -> None:
    op.create_table(
        "user",
        sa.Column("id", _UUID, primary_key=True, server_default=_GEN_UUID),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
        sa.Column("profile", pg.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False),
    )

    op.create_table(
        "activity",
        sa.Column("id", _UUID, primary_key=True, server_default=_GEN_UUID),
        sa.Column("user_id", _UUID, sa.ForeignKey("user.id"), nullable=False),
        sa.Column("module", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("connectivity", sa.String(), nullable=False),
        sa.Column("payload", pg.JSONB(), nullable=False),
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
        sa.Column("due_at", _TS, nullable=True),
    )
    op.create_index("idx_activity_user_module_type", "activity", ["user_id", "module", "type"])

    op.create_table(
        "response",
        sa.Column("id", _UUID, primary_key=True, server_default=_GEN_UUID),
        sa.Column("activity_id", _UUID, sa.ForeignKey("activity.id"), nullable=False),
        sa.Column("user_id", _UUID, sa.ForeignKey("user.id"), nullable=False),
        sa.Column("user_answer", pg.JSONB(), nullable=False),
        sa.Column("grade", pg.JSONB(), nullable=True),
        sa.Column("local_created_at", _TS, nullable=False),
        sa.Column("synced", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.create_index("idx_response_user_synced", "response", ["user_id", "synced"])

    op.create_table(
        "srs_card",
        sa.Column("id", _UUID, primary_key=True, server_default=_GEN_UUID),
        sa.Column("user_id", _UUID, sa.ForeignKey("user.id"), nullable=False),
        sa.Column("module", sa.String(), nullable=False),
        sa.Column("front", pg.JSONB(), nullable=False),
        sa.Column("back", pg.JSONB(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("fsrs_state", pg.JSONB(), nullable=False),
        sa.Column("due_at", _TS, nullable=False),
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_srs_user_due", "srs_card", ["user_id", "due_at"])

    op.create_table(
        "job",
        sa.Column("id", _UUID, primary_key=True, server_default=_GEN_UUID),
        sa.Column("user_id", _UUID, sa.ForeignKey("user.id"), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("input_ref", pg.JSONB(), nullable=False),
        sa.Column("result", pg.JSONB(), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", _TS, server_default=sa.func.now(), nullable=False),
    )
    op.create_index("idx_job_user_status", "job", ["user_id", "status"])

    op.create_table(
        "material",
        sa.Column("id", _UUID, primary_key=True, server_default=_GEN_UUID),
        sa.Column("user_id", _UUID, sa.ForeignKey("user.id"), nullable=True),
        sa.Column("module", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("content", pg.JSONB(), nullable=False),
        sa.Column("created_at", _TS, server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "rubric",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("module", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("schema", pg.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("id", "version"),
    )


def downgrade() -> None:
    op.drop_table("rubric")
    op.drop_table("material")
    op.drop_index("idx_job_user_status", table_name="job")
    op.drop_table("job")
    op.drop_index("idx_srs_user_due", table_name="srs_card")
    op.drop_table("srs_card")
    op.drop_index("idx_response_user_synced", table_name="response")
    op.drop_table("response")
    op.drop_index("idx_activity_user_module_type", table_name="activity")
    op.drop_table("activity")
    op.drop_table("user")
