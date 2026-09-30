"""учёт расхода токенов LLM

Раньше токены не считались нигде, хотя план Ф1 числил учёт сделанным.
Запись на каждый ответ провайдера: кто, зачем (инструмент/рубрика), какая
модель, сколько токенов. `user_id` nullable: вызов может идти не от
пользователя (скрипты, сид).

Revision ID: 0009_llm_usage
Revises: 0008_clear_vendor_model_pins
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0009_llm_usage"
down_revision: str | None = "0008_clear_vendor_model_pins"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_usage",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("user.id"), nullable=True),
        sa.Column("purpose", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("completion_tokens", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at", pg.TIMESTAMP(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )
    op.create_index("idx_llm_usage_user_created", "llm_usage", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("idx_llm_usage_user_created", table_name="llm_usage")
    op.drop_table("llm_usage")
