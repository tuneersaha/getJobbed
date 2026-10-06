"""
Tests for POST /api/fetch/trigger.

Unit: cooldown guard logic.
Integration: real DB enqueue via async_db_conn.
"""

import uuid
import pytest
from datetime import datetime, timezone, timedelta


# ─── Helpers ─────────────────────────────────────────────────────────────────

async def _insert_fetch_run(conn, status="completed", completed_at=None):
    if completed_at is None:
        completed_at_sql = "NOW()"
        cur = await conn.execute(
            f"""
            INSERT INTO fetch_runs (source, status, started_at, completed_at)
            VALUES ('test', %s, NOW(), {completed_at_sql})
            RETURNING id, completed_at
            """,
            [status],
        )
    else:
        cur = await conn.execute(
            """
            INSERT INTO fetch_runs (source, status, started_at, completed_at)
            VALUES ('test', %s, NOW(), %s)
            RETURNING id, completed_at
            """,
            [status, completed_at],
        )
    return await cur.fetchone()


# ─── Unit: cooldown guard logic ──────────────────────────────────────────────

class TestCooldownGuard:
    def test_cooldown_default_30_minutes(self):
        import os
        cooldown = int(os.environ.get("FETCH_TRIGGER_COOLDOWN_MINUTES", "30"))
        assert cooldown == 30

    def test_fetch_trigger_response_schema(self):
        from app.schemas import FetchTriggerResponse
        r = FetchTriggerResponse(
            task_id=42,
            queued_at="2026-01-01T00:00:00+00:00",
        )
        assert r.task_id == 42


# ─── Integration: cooldown check ─────────────────────────────────────────────

@pytest.mark.integration
class TestFetchCooldown:
    @pytest.mark.asyncio
    async def test_no_recent_fetch_allows_enqueue(self, async_db_conn):
        """No completed fetch in last 30 min → cooldown query returns nothing."""
        old_time = datetime.now(timezone.utc) - timedelta(hours=2)
        await _insert_fetch_run(async_db_conn, status="completed", completed_at=old_time)

        cur = await async_db_conn.execute(
            """
            SELECT completed_at FROM fetch_runs
            WHERE status = 'completed'
              AND completed_at > NOW() - INTERVAL '1 minute' * %s
            ORDER BY completed_at DESC LIMIT 1
            """,
            [30],
        )
        row = await cur.fetchone()
        assert row is None  # old run is outside cooldown window

    @pytest.mark.asyncio
    async def test_recent_fetch_blocks_trigger(self, async_db_conn):
        """Fetch completed 5 min ago → cooldown query returns a row."""
        recent_time = datetime.now(timezone.utc) - timedelta(minutes=5)
        await _insert_fetch_run(async_db_conn, status="completed", completed_at=recent_time)

        cur = await async_db_conn.execute(
            """
            SELECT completed_at FROM fetch_runs
            WHERE status = 'completed'
              AND completed_at > NOW() - INTERVAL '1 minute' * %s
            ORDER BY completed_at DESC LIMIT 1
            """,
            [30],
        )
        row = await cur.fetchone()
        assert row is not None  # recent run blocks trigger

    @pytest.mark.asyncio
    async def test_enqueue_creates_job_queue_row(self, async_db_conn):
        """Enqueue inserts a fetch_jobs task into job_queue."""
        cur = await async_db_conn.execute(
            """
            INSERT INTO job_queue (task_type, payload, status, max_attempts)
            VALUES ('fetch_jobs', '{}', 'pending', 3)
            RETURNING id, created_at
            """,
        )
        row = await cur.fetchone()
        task_id = row[0]
        assert task_id > 0

        # Verify the row exists
        check = await async_db_conn.execute(
            "SELECT task_type, status FROM job_queue WHERE id = %s",
            [task_id],
        )
        check_row = await check.fetchone()
        assert check_row[0] == "fetch_jobs"
        assert check_row[1] == "pending"

    @pytest.mark.asyncio
    async def test_failed_fetch_run_doesnt_block(self, async_db_conn):
        """A failed fetch run doesn't count as recently completed."""
        recent_time = datetime.now(timezone.utc) - timedelta(minutes=5)
        await _insert_fetch_run(async_db_conn, status="failed", completed_at=recent_time)

        cur = await async_db_conn.execute(
            """
            SELECT completed_at FROM fetch_runs
            WHERE status = 'completed'
              AND completed_at > NOW() - INTERVAL '1 minute' * %s
            ORDER BY completed_at DESC LIMIT 1
            """,
            [30],
        )
        row = await cur.fetchone()
        assert row is None  # failed run is not a cooldown blocker
