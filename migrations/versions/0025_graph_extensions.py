"""graph_extensions: расширения Apache AGE и pgvector, схема графа знаний (T-0072)

Revision ID: 0025_graph_extensions
Revises: 0024_policy_consent
Create Date: 2026-10-06

Существующие таблицы не меняются: concept, concept_edge, domain_edge остаются источником
правды (A-0024), AGE и pgvector добавляются рядом для обхода графа и поиска кандидатов.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0025_graph_extensions"
down_revision: str | None = "0024_policy_consent"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GRAPH = "knowledge"


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS age")
    # AGE ищет свои классы операторов в ag_catalog: без него create_graph падает.
    op.execute('SET LOCAL search_path = ag_catalog, "$user", public')
    # create_graph падает, если граф уже есть: проверяем сами, чтобы миграция была повторяема.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM ag_catalog.ag_graph WHERE name = '{GRAPH}') THEN
                PERFORM ag_catalog.create_graph('{GRAPH}');
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute('SET LOCAL search_path = ag_catalog, "$user", public')
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM ag_catalog.ag_graph WHERE name = '{GRAPH}') THEN
                PERFORM ag_catalog.drop_graph('{GRAPH}', true);
            END IF;
        END
        $$
        """
    )
    op.execute("DROP EXTENSION IF EXISTS age")
    op.execute("DROP EXTENSION IF EXISTS vector")
