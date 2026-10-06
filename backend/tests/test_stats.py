"""
Tests for GET /api/stats.

Unit: StatsResponse schema.
Integration: aggregate queries via async_db_conn.
"""

import uuid
import pytest
from datetime import datetime, timezone, timedelta


# ─── Helpers ─────────────────────────────────────────────────────────────────

async def _insert_user(conn):
    cur = await conn.execute(
        "INSERT INTO users (google_sub, email) VALUES (%s, %s) RETURNING id",
        [f"sub-{uuid.uuid4()}", f"{uuid.uuid4()}@test.com"],
    )
    return str((await cur.fetchone())[0])


async def _insert_job(conn):
    cur = await conn.execute(
        """
        INSERT INTO jobs
            (external_id, source, company_name, title, description, apply_url)
        VALUES (%s, 'greenhouse', 'Co', 'DE', 'desc', 'https://apply.example.com')
        RETURNING id
        """,
        [str(uuid.uuid4())],
    )
    return str((await cur.fetchone())[0])


async def _insert_match(conn, user_id, job_id, status="ready", tailoring_failed=False):
    cur = await conn.execute(
        """
        INSERT INTO user_job_matches
            (user_id, job_id, match_score, gap_analysis, status, tailoring_failed_at)
        VALUES (%s, %s, 0.75, '{}', %s, %s)
        RETURNING id
        """,
        [user_id, job_id, status, datetime.now(timezone.utc) if tailoring_failed else None],
    )
    return str((await cur.fetchone())[0])


async def _insert_tailored_resume(conn, match_id, prompt_tokens=1000, completion_tokens=500):
    await conn.execute(
        """
        INSERT INTO tailored_resumes
            (match_id, latex_source, model_used, prompt_tokens, completion_tokens)
        VALUES (%s, '\\begin{document}\\end{document}', 'test-model', %s, %s)
        """,
        [match_id, prompt_tokens, completion_tokens],
    )


# ─── Unit: StatsResponse schema ───────────────────────────────────────────────

class TestStatsSchema:
    def test_stats_response_all_fields(self):
        from app.schemas import StatsResponse
        s = StatsResponse(
            last_fetch_at=None,
            jobs_found_today=0,
            queue_depth=0,
            tailoring_in_progress=0,
            tailoring_failed=0,
            total_matched=0,
            total_applied=0,
            cumulative_prompt_tokens=0,
            cumulative_completion_tokens=0,
        )
        assert s.total_matched == 0
        assert s.last_fetch_at is None


# ─── Integration: stats queries ───────────────────────────────────────────────

@pytest.mark.integration
class TestStatsQueries:
    @pytest.mark.asyncio
    async def test_total_matched_excludes_deleted(self, async_db_conn):
        user_id = await _insert_user(async_db_conn)
        job1 = await _insert_job(async_db_conn)
        job2 = await _insert_job(async_db_conn)
        await _insert_match(async_db_conn, user_id, job1, status="ready")
        await _insert_match(async_db_conn, user_id, job2, status="deleted")

        cur = await async_db_conn.execute(
            """
            SELECT COUNT(*) FROM user_job_matches
            WHERE user_id = %s AND status != 'deleted'
            """,
            [user_id],
        )
        row = await cur.fetchone()
        assert row[0] == 1

    @pytest.mark.asyncio
    async def test_total_applied_counts_lifecycle_statuses(self, async_db_conn):
        user_id = await _insert_user(async_db_conn)
        for s in ["applied", "interviewing", "offer", "accepted", "rejected"]:
            job_id = await _insert_job(async_db_conn)
            await _insert_match(async_db_conn, user_id, job_id, status=s)
        # Add one "ready" — should not count
        job_id = await _insert_job(async_db_conn)
        await _insert_match(async_db_conn, user_id, job_id, status="ready")

        cur = await async_db_conn.execute(
            """
            SELECT COUNT(*) FROM user_job_matches
            WHERE user_id = %s
              AND status IN ('applied', 'interviewing', 'offer', 'accepted', 'rejected')
            """,
            [user_id],
        )
        row = await cur.fetchone()
        assert row[0] == 5

    @pytest.mark.asyncio
    async def test_tailoring_failed_count(self, async_db_conn):
        user_id = await _insert_user(async_db_conn)
        job1 = await _insert_job(async_db_conn)
        job2 = await _insert_job(async_db_conn)
        await _insert_match(async_db_conn, user_id, job1, tailoring_failed=True)
        await _insert_match(async_db_conn, user_id, job2, tailoring_failed=False)

        cur = await async_db_conn.execute(
            """
            SELECT COUNT(*) FROM user_job_matches
            WHERE user_id = %s AND tailoring_failed_at IS NOT NULL
            """,
            [user_id],
        )
        row = await cur.fetchone()
        assert row[0] == 1

    @pytest.mark.asyncio
    async def test_cumulative_tokens_sum(self, async_db_conn):
        user_id = await _insert_user(async_db_conn)
        job1 = await _insert_job(async_db_conn)
        job2 = await _insert_job(async_db_conn)
        match1 = await _insert_match(async_db_conn, user_id, job1)
        match2 = await _insert_match(async_db_conn, user_id, job2)
        await _insert_tailored_resume(async_db_conn, match1, prompt_tokens=1000, completion_tokens=500)
        await _insert_tailored_resume(async_db_conn, match2, prompt_tokens=2000, completion_tokens=800)

        cur = await async_db_conn.execute(
            """
            SELECT COALESCE(SUM(tr.prompt_tokens), 0),
                   COALESCE(SUM(tr.completion_tokens), 0)
            FROM tailored_resumes tr
            JOIN user_job_matches m ON m.id = tr.match_id
            WHERE m.user_id = %s
            """,
            [user_id],
        )
        row = await cur.fetchone()
        assert int(row[0]) == 3000
        assert int(row[1]) == 1300

    @pytest.mark.asyncio
    async def test_queue_depth_counts_pending(self, async_db_conn):
        # Insert a pending task
        await async_db_conn.execute(
            "INSERT INTO job_queue (task_type, payload, status) VALUES ('fetch_jobs', '{}', 'pending')",
        )

        cur = await async_db_conn.execute(
            "SELECT COUNT(*) FROM job_queue WHERE status = 'pending'",
        )
        row = await cur.fetchone()
        assert row[0] >= 1  # at least the one we inserted

    @pytest.mark.asyncio
    async def test_last_fetch_at_most_recent(self, async_db_conn):
        older = datetime.now(timezone.utc) - timedelta(hours=2)
        newer = datetime.now(timezone.utc) - timedelta(hours=1)

        await async_db_conn.execute(
            "INSERT INTO fetch_runs (source, status, completed_at) VALUES ('test', 'completed', %s)",
            [older],
        )
        await async_db_conn.execute(
            "INSERT INTO fetch_runs (source, status, completed_at) VALUES ('test', 'completed', %s)",
            [newer],
        )

        cur = await async_db_conn.execute(
            """
            SELECT completed_at FROM fetch_runs
            WHERE status = 'completed'
            ORDER BY completed_at DESC LIMIT 1
            """,
        )
        row = await cur.fetchone()
        # Most recent should be within 10 seconds of newer
        assert abs((row[0] - newer).total_seconds()) < 10

    @pytest.mark.asyncio
    async def test_stats_scoped_to_user(self, async_db_conn):
        """Stats should only count the requesting user's matches."""
        user1 = await _insert_user(async_db_conn)
        user2 = await _insert_user(async_db_conn)

        for _ in range(3):
            job_id = await _insert_job(async_db_conn)
            await _insert_match(async_db_conn, user2, job_id, status="ready")

        cur = await async_db_conn.execute(
            """
            SELECT COUNT(*) FROM user_job_matches
            WHERE user_id = %s AND status != 'deleted'
            """,
            [user1],
        )
        row = await cur.fetchone()
        assert row[0] == 0  # user1 sees nothing from user2
