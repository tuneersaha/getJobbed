"""
Tests for PATCH /api/matches/{match_id}.

Unit: status transition validation logic.
Integration: real DB transitions via async_db_conn.
"""

import uuid
import pytest


# ─── Helpers (reuse from test_jobs pattern) ───────────────────────────────────

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


async def _insert_match(conn, user_id, job_id, status="ready"):
    cur = await conn.execute(
        """
        INSERT INTO user_job_matches
            (user_id, job_id, match_score, gap_analysis, status)
        VALUES (%s, %s, 0.75, '{}', %s)
        RETURNING id
        """,
        [user_id, job_id, status],
    )
    return str((await cur.fetchone())[0])


# ─── Unit: transition validation ─────────────────────────────────────────────

class TestStatusTransitions:
    def test_applied_valid_source_statuses(self):
        from app.routers.matches import _APPLIED_FROM
        assert "pending"  in _APPLIED_FROM
        assert "tailoring" in _APPLIED_FROM
        assert "ready"    in _APPLIED_FROM

    def test_applied_invalid_source_statuses(self):
        from app.routers.matches import _APPLIED_FROM
        assert "applied"      not in _APPLIED_FROM
        assert "interviewing" not in _APPLIED_FROM
        assert "deleted"      not in _APPLIED_FROM

    def test_deleted_valid_source_statuses(self):
        from app.routers.matches import _DELETED_FROM
        assert "pending"   in _DELETED_FROM
        assert "tailoring" in _DELETED_FROM
        assert "ready"     in _DELETED_FROM
        assert "applied"   in _DELETED_FROM

    def test_deleted_invalid_source_statuses(self):
        from app.routers.matches import _DELETED_FROM
        assert "accepted"     not in _DELETED_FROM
        assert "rejected"     not in _DELETED_FROM
        assert "interviewing" not in _DELETED_FROM


# ─── Integration: applied transition ─────────────────────────────────────────

@pytest.mark.integration
class TestAppliedTransition:
    @pytest.mark.asyncio
    async def test_ready_to_applied(self, async_db_conn):
        user_id = await _insert_user(async_db_conn)
        job_id = await _insert_job(async_db_conn)
        match_id = await _insert_match(async_db_conn, user_id, job_id, status="ready")

        await async_db_conn.execute(
            """
            UPDATE user_job_matches
            SET status = 'applied', applied_at = NOW(), updated_at = NOW()
            WHERE id = %s AND user_id = %s AND status IN ('pending', 'tailoring', 'ready')
            """,
            [match_id, user_id],
        )

        cur = await async_db_conn.execute(
            "SELECT status, applied_at FROM user_job_matches WHERE id = %s",
            [match_id],
        )
        row = await cur.fetchone()
        assert row[0] == "applied"
        assert row[1] is not None

    @pytest.mark.asyncio
    async def test_applied_to_applied_noop(self, async_db_conn):
        """Already-applied match: UPDATE affects 0 rows (not in valid source set)."""
        user_id = await _insert_user(async_db_conn)
        job_id = await _insert_job(async_db_conn)
        match_id = await _insert_match(async_db_conn, user_id, job_id, status="applied")

        cur = await async_db_conn.execute(
            """
            UPDATE user_job_matches
            SET status = 'applied', applied_at = NOW(), updated_at = NOW()
            WHERE id = %s AND user_id = %s AND status IN ('pending', 'tailoring', 'ready')
            """,
            [match_id, user_id],
        )
        assert cur.rowcount == 0


# ─── Integration: deleted transition ─────────────────────────────────────────

@pytest.mark.integration
class TestDeletedTransition:
    @pytest.mark.asyncio
    async def test_ready_to_deleted_inserts_excluded(self, async_db_conn):
        user_id = await _insert_user(async_db_conn)
        job_id = await _insert_job(async_db_conn)
        match_id = await _insert_match(async_db_conn, user_id, job_id, status="ready")

        # Insert exclusion
        await async_db_conn.execute(
            """
            INSERT INTO user_excluded_jobs (user_id, job_id, reason)
            VALUES (%s, %s, 'deleted')
            ON CONFLICT (user_id, job_id) DO NOTHING
            """,
            [user_id, job_id],
        )
        # Delete match
        await async_db_conn.execute(
            "DELETE FROM user_job_matches WHERE id = %s AND user_id = %s",
            [match_id, user_id],
        )

        # Exclusion row exists
        cur = await async_db_conn.execute(
            "SELECT reason FROM user_excluded_jobs WHERE user_id = %s AND job_id = %s",
            [user_id, job_id],
        )
        row = await cur.fetchone()
        assert row is not None
        assert row[0] == "deleted"

        # Match row gone
        cur2 = await async_db_conn.execute(
            "SELECT id FROM user_job_matches WHERE id = %s",
            [match_id],
        )
        assert (await cur2.fetchone()) is None

    @pytest.mark.asyncio
    async def test_deletion_cascades_tailored_resume(self, async_db_conn):
        """Deleting a match cascades to tailored_resumes via FK ON DELETE CASCADE."""
        user_id = await _insert_user(async_db_conn)
        job_id = await _insert_job(async_db_conn)
        match_id = await _insert_match(async_db_conn, user_id, job_id, status="ready")

        # Insert tailored resume
        await async_db_conn.execute(
            """
            INSERT INTO tailored_resumes
                (match_id, latex_source, model_used, prompt_tokens, completion_tokens)
            VALUES (%s, '\\begin{document}\\end{document}', 'model', 100, 50)
            """,
            [match_id],
        )

        # Delete match — cascade should remove tailored_resumes
        await async_db_conn.execute(
            "DELETE FROM user_job_matches WHERE id = %s",
            [match_id],
        )

        cur = await async_db_conn.execute(
            "SELECT id FROM tailored_resumes WHERE match_id = %s",
            [match_id],
        )
        assert (await cur.fetchone()) is None

    @pytest.mark.asyncio
    async def test_jobs_row_survives_deletion(self, async_db_conn):
        """jobs row is NOT deleted when match is deleted (multi-user safety)."""
        user_id = await _insert_user(async_db_conn)
        job_id = await _insert_job(async_db_conn)
        match_id = await _insert_match(async_db_conn, user_id, job_id, status="ready")

        await async_db_conn.execute(
            "DELETE FROM user_job_matches WHERE id = %s",
            [match_id],
        )

        cur = await async_db_conn.execute(
            "SELECT id FROM jobs WHERE id = %s",
            [job_id],
        )
        assert (await cur.fetchone()) is not None

    @pytest.mark.asyncio
    async def test_exclusion_is_idempotent(self, async_db_conn):
        """ON CONFLICT DO NOTHING: inserting same exclusion twice doesn't error."""
        user_id = await _insert_user(async_db_conn)
        job_id = await _insert_job(async_db_conn)

        for _ in range(2):
            await async_db_conn.execute(
                """
                INSERT INTO user_excluded_jobs (user_id, job_id, reason)
                VALUES (%s, %s, 'deleted')
                ON CONFLICT (user_id, job_id) DO NOTHING
                """,
                [user_id, job_id],
            )

        cur = await async_db_conn.execute(
            "SELECT COUNT(*) FROM user_excluded_jobs WHERE user_id = %s AND job_id = %s",
            [user_id, job_id],
        )
        row = await cur.fetchone()
        assert row[0] == 1
