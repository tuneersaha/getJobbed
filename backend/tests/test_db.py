"""
Tests for app/db.py — psycopg3 connection pool.

Per testing-with-discernment:
- These are integration tests (real Postgres).
- We test pool behavior: startup, SELECT 1, connection reuse.
- We do NOT test psycopg3 internals (that's psycopg3's test suite's job).

TDD: tests written BEFORE app/db.py exists. They will fail with ImportError
until db.py is implemented.
"""

import pytest
import pytest_asyncio

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_pool_can_execute_query(test_db_url, monkeypatch):
    """Pool starts, yields a connection, and SELECT 1 returns a row."""
    monkeypatch.setenv("DATABASE_URL", test_db_url)

    from app.db import create_pool, get_conn

    pool = await create_pool()
    try:
        async with get_conn(pool) as conn:
            cur = await conn.execute("SELECT 1 AS n")
            row = await cur.fetchone()
        assert row[0] == 1
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_pool_reuses_connections(test_db_url, monkeypatch):
    """Two consecutive get_conn calls return without error — pool does not exhaust."""
    monkeypatch.setenv("DATABASE_URL", test_db_url)

    from app.db import create_pool, get_conn

    pool = await create_pool()
    try:
        async with get_conn(pool) as conn1:
            cur1 = await conn1.execute("SELECT 1 AS n")
            row1 = await cur1.fetchone()
        async with get_conn(pool) as conn2:
            cur2 = await conn2.execute("SELECT 2 AS n")
            row2 = await cur2.fetchone()
        assert row1[0] == 1
        assert row2[0] == 2
    finally:
        await pool.close()


@pytest.mark.asyncio
async def test_get_conn_rolls_back_on_exception(test_db_url, monkeypatch):
    """If an exception is raised inside get_conn block, transaction is rolled back."""
    monkeypatch.setenv("DATABASE_URL", test_db_url)

    from app.db import create_pool, get_conn

    pool = await create_pool()
    try:
        with pytest.raises(ValueError):
            async with get_conn(pool) as conn:
                await conn.execute("SELECT 1")
                raise ValueError("intentional error")
        # Pool still works after a rolled-back connection
        async with get_conn(pool) as conn:
            cur = await conn.execute("SELECT 42 AS n")
            row = await cur.fetchone()
        assert row[0] == 42
    finally:
        await pool.close()
