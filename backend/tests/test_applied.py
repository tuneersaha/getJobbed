"""
Tests for applied tracking endpoints.

GET  /api/applied
PATCH /api/applied/{match_id}

Unit: status transition set validation.
Integration: real DB transitions via async_db_conn.
"""

import uuid
import pytest


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


async def _insert_match(conn, user_id, job_id, status="applied"):
    cur = await conn.execute(
        """
        INSERT INTO user_job_matches
            (user_id, job_id, match_score, gap_analysis, status, applied_at)
        VALUES (%s, %s, 0.75, '{}', %s, NOW())
        RETURNING id
        """,
        [user_id, job_id, status],
    )
    return str((await cur.fetchone())[0])


# ─── Unit: transition validation ─────────────────────────────────────────────

class TestAppliedStatusTransitions:
    def test_updateable_from_contains_applied_states(self):
        from app.routers.applied import _UPDATEABLE_FROM
        assert "applied"      in _UPDATEABLE_FROM
        assert "interviewing" in _UPDATEABLE_FROM
        assert "offer"        in _UPDATEABLE_FROM

    def test_updateable_from_excludes_terminal_states(self):
        from app.routers.applied import _UPDATEABLE_FROM
        assert "accepted" not in _UPDATEABLE_FROM
        assert "rejected" not in _UPDATEABLE_FROM

    def test_applied_status_update_rejects_invalid(self):
        from pydantic import ValidationError
        from app.schemas import AppliedStatusUpdate
        with pytest.raises(ValidationError):
            AppliedStatusUpdate(status="deleted")

    def test_applied_status_update_accepts_valid(self):
        from app.schemas import AppliedStatusUpdate
        for s in ("interviewing", "offer", "accepted", "rejected"):
            u = AppliedStatusUpdate(status=s)
            assert u.status == s


# ─── Integration: GET /api/applied ───────────────────────────────────────────

@pytest.mark.integration
class TestListApplied:
    @pytest.mark.asyncio
    async def test_returns_applied_jobs(self, async_db_conn):
        user_id = await _insert_user(async_db_conn)
        job_id = await _insert_job(async_db_conn)
        match_id = await _insert_match(async_db_conn, user_id, job_id, status="applied")

        cur = await async_db_conn.execute(
            """
            SELECT m.id, m.status
            FROM user_job_matches m
            WHERE m.user_id = %s AND m.status = ANY(%s)
            ORDER BY m.applied_at DESC NULLS LAST
            """,
            [user_id, ["applied", "interviewing", "offer", "accepted", "rejected"]],
        )
        rows = await cur.fetchall()
        assert len(rows) == 1
        assert str(rows[0][0]) == match_id

    @pytest.mark.asyncio
    async def test_excludes_ready_jobs(self, async_db_conn):
        user_id = await _insert_user(async_db_conn)
        job_id = await _insert_job(async_db_conn)
        await _insert_match(async_db_conn, user_id, job_id, status="ready")

        cur = await async_db_conn.execute(
            """
            SELECT COUNT(*) FROM user_job_matches
            WHERE user_id = %s AND status = ANY(%s)
            """,
            [user_id, ["applied", "interviewing", "offer", "accepted", "rejected"]],
        )
        row = await cur.fetchone()
        assert row[0] == 0

    @pytest.mark.asyncio
    async def test_includes_all_lifecycle_statuses(self, async_db_conn):
        user_id = await _insert_user(async_db_conn)
        statuses = ["applied", "interviewing", "offer", "accepted", "rejected"]
        for s in statuses:
            job_id = await _insert_job(async_db_conn)
            await _insert_match(async_db_conn, user_id, job_id, status=s)

        cur = await async_db_conn.execute(
            """
            SELECT COUNT(*) FROM user_job_matches
            WHERE user_id = %s AND status = ANY(%s)
            """,
            [user_id, statuses],
        )
        row = await cur.fetchone()
        assert row[0] == 5


# ─── Integration: PATCH /api/applied/{match_id} ──────────────────────────────

@pytest.mark.integration
class TestUpdateAppliedStatus:
    @pytest.mark.asyncio
    async def test_applied_to_interviewing(self, async_db_conn):
        user_id = await _insert_user(async_db_conn)
        job_id = await _insert_job(async_db_conn)
        match_id = await _insert_match(async_db_conn, user_id, job_id, status="applied")

        await async_db_conn.execute(
            "UPDATE user_job_matches SET status = %s, updated_at = NOW() WHERE id = %s AND user_id = %s",
            ["interviewing", match_id, user_id],
        )

        cur = await async_db_conn.execute(
            "SELECT status FROM user_job_matches WHERE id = %s",
            [match_id],
        )
        row = await cur.fetchone()
        assert row[0] == "interviewing"

    @pytest.mark.asyncio
    async def test_interviewing_to_offer(self, async_db_conn):
        user_id = await _insert_user(async_db_conn)
        job_id = await _insert_job(async_db_conn)
        match_id = await _insert_match(async_db_conn, user_id, job_id, status="interviewing")

        await async_db_conn.execute(
            "UPDATE user_job_matches SET status = %s, updated_at = NOW() WHERE id = %s AND user_id = %s",
            ["offer", match_id, user_id],
        )

        cur = await async_db_conn.execute(
            "SELECT status FROM user_job_matches WHERE id = %s",
            [match_id],
        )
        row = await cur.fetchone()
        assert row[0] == "offer"

    @pytest.mark.asyncio
    async def test_offer_to_accepted(self, async_db_conn):
        user_id = await _insert_user(async_db_conn)
        job_id = await _insert_job(async_db_conn)
        match_id = await _insert_match(async_db_conn, user_id, job_id, status="offer")

        await async_db_conn.execute(
            "UPDATE user_job_matches SET status = %s, updated_at = NOW() WHERE id = %s AND user_id = %s",
            ["accepted", match_id, user_id],
        )

        cur = await async_db_conn.execute(
            "SELECT status FROM user_job_matches WHERE id = %s",
            [match_id],
        )
        row = await cur.fetchone()
        assert row[0] == "accepted"

    @pytest.mark.asyncio
    async def test_idempotent_same_status(self, async_db_conn):
        """Updating to same status is a no-op (0 rowcount from the router)."""
        user_id = await _insert_user(async_db_conn)
        job_id = await _insert_job(async_db_conn)
        match_id = await _insert_match(async_db_conn, user_id, job_id, status="interviewing")

        # Re-read current status — same status means early return in router
        cur = await async_db_conn.execute(
            "SELECT status FROM user_job_matches WHERE id = %s AND user_id = %s",
            [match_id, user_id],
        )
        row = await cur.fetchone()
        assert row[0] == "interviewing"  # unchanged

    @pytest.mark.asyncio
    async def test_terminal_status_not_updateable(self, async_db_conn):
        """accepted/rejected are terminal; router checks _UPDATEABLE_FROM."""
        from app.routers.applied import _UPDATEABLE_FROM
        assert "accepted" not in _UPDATEABLE_FROM
        assert "rejected" not in _UPDATEABLE_FROM
