"""concept_key: устойчивый ключ канонического узла

build/refresh сопоставляли узлы по заголовку, поэтому переименование узла
моделью создавало дубль. Ключ (snake_case) модель возвращает вместе с узлом;
сопоставление теперь идёт по (domain, key). У существующих узлов ключа нет —
он проставляется при первой перестройке по совпадению заголовка.

Revision ID: 0014_concept_key
Revises: 0013_auth_hardening
Create Date: 2026-10-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0014_concept_key"
down_revision: str | None = "0013_auth_hardening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("concept", sa.Column("key", sa.String(), nullable=True))
    op.create_index(
        "uq_concept_domain_key",
        "concept",
        ["domain", "key"],
        unique=True,
        postgresql_where=sa.text("key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_concept_domain_key", table_name="concept")
    op.drop_column("concept", "key")
