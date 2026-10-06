"""
Test fixtures for GetJobbed backend.

Integration tests (marked with @pytest.mark.integration) require a real
Postgres instance. Set DATABASE_URL_TEST env var, or it defaults to
DATABASE_URL with the DB name replaced by 'getjobbed_test'.

Each integration test runs in its own connection with transactions rolled
back at teardown — no explicit cleanup needed, tests isolated from each other.
"""

import os
import pytest
import psycopg
import psycopg_pool


# ─── Test database URL ────────────────────────────────────────────────────────

def _test_db_url() -> str:
    if "DATABASE_URL_TEST" in os.environ:
        return os.environ["DATABASE_URL_TEST"]
    base = os.environ.get(
        "DATABASE_URL",
        "postgresql://postgres:postgres@localhost:5432/getjobbed",
    )
    # Replace DB name with getjobbed_test
    return base.rsplit("/", 1)[0] + "/getjobbed_test"


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def test_db_url():
    return _test_db_url()


@pytest.fixture
def db_conn(test_db_url):
    """
    Yields a synchronous psycopg3 connection inside a transaction that is
    rolled back after each test. For use in sync tests.
    """
    with psycopg.connect(test_db_url) as conn:
        conn.autocommit = False
        yield conn
        conn.rollback()


@pytest.fixture
async def async_db_conn(test_db_url):
    """
    Yields an async psycopg3 connection inside a transaction that is rolled
    back after each test. For use in async tests.
    """
    async with await psycopg.AsyncConnection.connect(test_db_url) as conn:
        await conn.set_autocommit(False)
        yield conn
        await conn.rollback()
