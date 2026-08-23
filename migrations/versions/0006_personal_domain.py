"""knowledge: domain на персональных узлах и рёбрах

Без домена `effective_graph` подмешивал свои узлы пользователя в граф ЛЮБОЙ
области: `user_concept`/`user_edge` выбирались только по user_id. Пока домен
один, это незаметно — на втором графы бы слиплись.

Бэкфилл трёхшаговый, т.к. у существующих строк домена взять неоткуда:
  1. унаследованные узлы  → домен их канонического base_concept;
  2. свои узлы (base=null) → домен канонического узла, от которого растёт ребро;
  3. остаток              → DEFAULT_DOMAIN (в проде на момент миграции есть
     только 'ml', так что шаг 3 срабатывает лишь для сирот).

Revision ID: 0006_personal_domain
Revises: 0005_superuser
Create Date: 2026-08-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_personal_domain"
down_revision: str | None = "0005_superuser"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_DOMAIN = "ml"


def upgrade() -> None:
    op.add_column("user_concept", sa.Column("domain", sa.String(), nullable=True))
    op.add_column("user_edge", sa.Column("domain", sa.String(), nullable=True))

    # 1. оверрайды канона — домен берём у базового узла
    op.execute(
        """
        UPDATE user_concept uc
           SET domain = c.domain
          FROM concept c
         WHERE uc.base_concept_id = c.id
        """
    )
    # 2. свои узлы — домен канонического узла, от которого их вырастили
    op.execute(
        """
        UPDATE user_concept uc
           SET domain = c.domain
          FROM user_edge ue
          JOIN concept c ON c.id = ue.from_id
         WHERE uc.domain IS NULL
           AND ue.to_id = uc.id
        """
    )
    # 3. рёбра — домен узла-источника (канонического или уже размеченного своего)
    op.execute(
        """
        UPDATE user_edge ue
           SET domain = c.domain
          FROM concept c
         WHERE c.id = ue.from_id
        """
    )
    op.execute(
        """
        UPDATE user_edge ue
           SET domain = uc.domain
          FROM user_concept uc
         WHERE ue.domain IS NULL
           AND uc.id = ue.from_id
           AND uc.domain IS NOT NULL
        """
    )
    # 4. сироты
    op.execute(f"UPDATE user_concept SET domain = '{DEFAULT_DOMAIN}' WHERE domain IS NULL")
    op.execute(f"UPDATE user_edge SET domain = '{DEFAULT_DOMAIN}' WHERE domain IS NULL")

    op.alter_column("user_concept", "domain", nullable=False)
    op.alter_column("user_edge", "domain", nullable=False)

    op.create_index("idx_user_concept_user_domain", "user_concept", ["user_id", "domain"])
    op.create_index("idx_user_edge_user_domain", "user_edge", ["user_id", "domain"])


def downgrade() -> None:
    op.drop_index("idx_user_edge_user_domain", table_name="user_edge")
    op.drop_index("idx_user_concept_user_domain", table_name="user_concept")
    op.drop_column("user_edge", "domain")
    op.drop_column("user_concept", "domain")
