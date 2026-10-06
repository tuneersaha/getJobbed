"""
Postgres connection pool via psycopg3.

Pool lifecycle:
  startup: pool = await create_pool(); set_pool(pool)
  shutdown: await get_pool().close()

Route handlers and workers: async with get_conn() as conn: ...

os.environ["DATABASE_URL"] — KeyError at startup if missing (fail fast by design).
"""

import os
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import psycopg_pool
import psycopg

# Module-level pool singleton — set during FastAPI lifespan startup
_pool: psycopg_pool.AsyncConnectionPool | None = None


async def create_pool(min_size: int = 2, max_size: int = 10) -> psycopg_pool.AsyncConnectionPool:
    """Open and return a new connection pool. Call once at application startup."""
    db_url = os.environ["DATABASE_URL"]
    pool = psycopg_pool.AsyncConnectionPool(
        conninfo=db_url,
        min_size=min_size,
        max_size=max_size,
        open=False,
        # Supabase Transaction Pooler (PgBouncer transaction mode) does not support
        # server-side prepared statements. prepare_threshold=0 disables them.
        kwargs={"prepare_threshold": 0},
    )
    await pool.open()
    return pool


def set_pool(pool: psycopg_pool.AsyncConnectionPool) -> None:
    """Store the pool singleton. Called in FastAPI lifespan after create_pool()."""
    global _pool
    _pool = pool


def get_pool() -> psycopg_pool.AsyncConnectionPool:
    """Return the module-level pool. Raises RuntimeError if not initialized."""
    if _pool is None:
        raise RuntimeError("DB pool not initialized — call set_pool() during startup")
    return _pool


@asynccontextmanager
async def get_conn(
    pool: psycopg_pool.AsyncConnectionPool | None = None,
) -> AsyncGenerator[psycopg.AsyncConnection, None]:
    """
    Async context manager that borrows a connection from the pool.

    Uses the module-level singleton if no pool is provided.
    Rolls back and re-raises on any exception, then returns connection to pool.
    """
    p = pool if pool is not None else get_pool()
    async with p.connection() as conn:
        try:
            yield conn
        except Exception:
            await conn.rollback()
            raise
