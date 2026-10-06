"""Apache AGE и pgvector в базе (T-0072, A-0024, A-0026, V-0092)."""

from __future__ import annotations

import pytest
from sqlalchemy import text


@pytest.fixture
def conn(session):
    row = session.execute(
        text("select count(*) from pg_available_extensions where name in ('age', 'vector')")
    ).scalar()
    if row != 2:
        pytest.skip("в базе нет расширений age и vector: нужен образ deploy/postgres")
    return session


def test_migration_created_the_extensions_and_the_knowledge_graph(conn):
    names = {r[0] for r in conn.execute(text("select extname from pg_extension"))}
    if {"age", "vector"} - names:
        pytest.skip("миграция 0025 в этой базе не применена")
    graphs = {r[0] for r in conn.execute(text("select name from ag_catalog.ag_graph"))}
    assert "knowledge" in graphs


def test_cypher_walks_prerequisites_transitively(conn):
    conn.execute(text("CREATE EXTENSION IF NOT EXISTS age"))
    conn.execute(text('SET LOCAL search_path = ag_catalog, "$user", public'))
    conn.execute(text("SELECT create_graph('t_walk')"))
    # Cypher содержит `:Label`, text() принял бы это за параметры, поэтому — драйверу напрямую.
    raw = conn.connection()
    raw.exec_driver_sql(
        "SELECT * FROM cypher('t_walk', $$ "
        "CREATE (:C {k:'a'})-[:PREREQ]->(:C {k:'b'})-[:PREREQ]->(:C {k:'c'}) $$) AS (r agtype)"
    )
    rows = raw.exec_driver_sql(
        "SELECT * FROM cypher('t_walk', $$ MATCH (:C {k:'a'})-[:PREREQ*1..3]->(x) "
        "RETURN x.k $$) AS (k agtype)"
    ).fetchall()
    assert sorted(r[0] for r in rows) == ['"b"', '"c"']


def test_vector_search_finds_the_nearest_neighbour(conn):
    conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
    conn.execute(text("CREATE TEMP TABLE v (id int, e vector(3))"))
    conn.execute(text("INSERT INTO v VALUES (1,'[1,0,0]'),(2,'[0,1,0]'),(3,'[0,0,1]')"))
    nearest = conn.execute(text("SELECT id FROM v ORDER BY e <=> '[0.1,0.9,0]' LIMIT 1")).scalar()
    assert nearest == 2
