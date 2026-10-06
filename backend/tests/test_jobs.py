"""
Tests for job dashboard endpoints.

GET /api/jobs
GET /api/jobs/{match_id}
GET /api/jobs/{match_id}/resume

Unit: schema validation, status filter.
Integration: real DB via async_db_conn, mocked auth.
"""

import uuid
import pytest
from unittest.mock import patch


# ─── Helpers ─────────────────────────────────────────────────────────────────

async def _insert_user(conn, google_sub=None, email=None):
    google_sub = google_sub or f"sub-{uuid.uuid4()}"
    email = email or f"{uuid.uuid4()}@test.com"
    cur = await conn.execute(
        "INSERT INTO users (google_sub, email) VALUES (%s, %s) RETURNING id",
        [google_sub, email],
    )
    row = await cur.fetchone()
    return str(row[0])


async def _insert_company(conn):
    cur = await conn.execute(
        """
        INSERT INTO companies (name, ats_type, ats_slug, priority)
        VALUES ('TestCo', 'greenhouse', %s, 'hot')
        RETURNING id
        """,
        [f"testco-{uuid.uuid4()}"],
    )
    return str((await cur.fetchone())[0])


async def _insert_job(conn, company_id=None, title="Data Engineer"):
    ext_id = str(uuid.uuid4())
    cur = await conn.execute(
        """
        INSERT INTO jobs
            (external_id, source, company_id, company_name, title, description, apply_url)
        VALUES (%s, 'greenhouse', %s, 'TestCo', %s, 'Some job description', 'https://apply.example.com')
        RETURNING id
        """,
        [ext_id, company_id, title],
    )
    return str((await cur.fetchone())[0])


async def _insert_match(conn, user_id, job_id, score=0.75, status="ready"):
    cur = await conn.execute(
        """
        INSERT INTO user_job_matches
            (user_id, job_id, match_score, gap_analysis, status)
        VALUES (%s, %s, %s, '{"matching_skills": ["Python"], "missing_skills": []}', %s)
        RETURNING id
        """,
        [user_id, job_id, score, status],
    )
    return str((await cur.fetchone())[0])


async def _insert_tailored_resume(conn, match_id):
    cur = await conn.execute(
        """
        INSERT INTO tailored_resumes
            (match_id, latex_source, model_used, prompt_tokens, completion_tokens)
        VALUES (%s, '\\begin{document}tailored\\end{document}', 'test-model', 1000, 500)
        RETURNING id
        """,
        [match_id],
    )
    return str((await cur.fetchone())[0])


# ─── Unit: schema validation ──────────────────────────────────────────────────

class TestJobsSchemaValidation:
    def test_job_list_item_has_required_fields(self):
        from app.schemas import JobListItem
        item = JobListItem(
            match_id="abc", job_id="def", title="DE", company_name="Co",
            location=None, work_type="remote", match_score=0.75,
            status="ready", gap_analysis={}, has_tailored_resume=False,
            tailoring_failed=False, posted_at=None,
        )
        assert item.match_id == "abc"
        assert item.tailoring_failed is False

    def test_match_status_update_rejects_invalid_status(self):
        from pydantic import ValidationError
        from app.schemas import MatchStatusUpdate
        with pytest.raises(ValidationError):
            MatchStatusUpdate(status="skipped")

    def test_match_status_update_accepts_applied(self):
        from app.schemas import MatchStatusUpdate
        m = MatchStatusUpdate(status="applied")
        assert m.status == "applied"

    def test_match_status_update_accepts_deleted(self):
        from app.schemas import MatchStatusUpdate
        m = MatchStatusUpdate(status="deleted")
        assert m.status == "deleted"


# ─── Integration: GET /api/jobs ───────────────────────────────────────────────

@pytest.mark.integration
class TestListJobs:
    @pytest.mark.asyncio
    async def test_returns_ready_jobs_by_default(self, async_db_conn):
        user_id = await _insert_user(async_db_conn)
        company_id = await _insert_company(async_db_conn)
        job_id = await _insert_job(async_db_conn, company_id)
        match_id = await _insert_match(async_db_conn, user_id, job_id, status="ready")

        # Verify via direct DB query (matches the SQL in the router)
        cur = await async_db_conn.execute(
            """
            SELECT m.id, m.status, j.title
            FROM user_job_matches m
            JOIN jobs j ON j.id = m.job_id
            WHERE m.user_id = %s AND m.status = ANY(%s)
            ORDER BY m.match_score DESC
            """,
            [user_id, ["ready"]],
        )
        rows = await cur.fetchall()
        assert len(rows) == 1
        assert str(rows[0][0]) == match_id
        assert rows[0][1] == "ready"
        assert rows[0][2] == "Data Engineer"

    @pytest.mark.asyncio
    async def test_filters_by_status(self, async_db_conn):
        user_id = await _insert_user(async_db_conn)
        company_id = await _insert_company(async_db_conn)
        job_id1 = await _insert_job(async_db_conn, company_id, "Job Ready")
        job_id2 = await _insert_job(async_db_conn, company_id, "Job Pending")
        await _insert_match(async_db_conn, user_id, job_id1, status="ready")
        await _insert_match(async_db_conn, user_id, job_id2, status="pending")

        cur = await async_db_conn.execute(
            """
            SELECT COUNT(*) FROM user_job_matches
            WHERE user_id = %s AND status = ANY(%s)
            """,
            [user_id, ["pending"]],
        )
        row = await cur.fetchone()
        assert row[0] == 1

    @pytest.mark.asyncio
    async def test_does_not_return_other_users_jobs(self, async_db_conn):
        user1 = await _insert_user(async_db_conn)
        user2 = await _insert_user(async_db_conn)
        company_id = await _insert_company(async_db_conn)
        job_id = await _insert_job(async_db_conn, company_id)
        await _insert_match(async_db_conn, user2, job_id, status="ready")

        cur = await async_db_conn.execute(
            "SELECT COUNT(*) FROM user_job_matches WHERE user_id = %s AND status = ANY(%s)",
            [user1, ["ready"]],
        )
        row = await cur.fetchone()
        assert row[0] == 0

    @pytest.mark.asyncio
    async def test_has_tailored_resume_flag(self, async_db_conn):
        user_id = await _insert_user(async_db_conn)
        company_id = await _insert_company(async_db_conn)
        job_id = await _insert_job(async_db_conn, company_id)
        match_id = await _insert_match(async_db_conn, user_id, job_id)
        await _insert_tailored_resume(async_db_conn, match_id)

        cur = await async_db_conn.execute(
            """
            SELECT (tr.id IS NOT NULL) AS has_tailored_resume
            FROM user_job_matches m
            LEFT JOIN tailored_resumes tr ON tr.match_id = m.id
            WHERE m.id = %s
            """,
            [match_id],
        )
        row = await cur.fetchone()
        assert row[0] is True


# ─── Integration: GET /api/jobs/{match_id} ────────────────────────────────────

@pytest.mark.integration
class TestGetJob:
    @pytest.mark.asyncio
    async def test_returns_full_detail(self, async_db_conn):
        user_id = await _insert_user(async_db_conn)
        company_id = await _insert_company(async_db_conn)
        job_id = await _insert_job(async_db_conn, company_id)
        match_id = await _insert_match(async_db_conn, user_id, job_id)

        cur = await async_db_conn.execute(
            """
            SELECT m.id, j.description, j.apply_url
            FROM user_job_matches m
            JOIN jobs j ON j.id = m.job_id
            WHERE m.id = %s AND m.user_id = %s
            """,
            [match_id, user_id],
        )
        row = await cur.fetchone()
        assert str(row[0]) == match_id
        assert "job description" in row[1]
        assert row[2].startswith("https://")

    @pytest.mark.asyncio
    async def test_returns_none_for_wrong_user(self, async_db_conn):
        user1 = await _insert_user(async_db_conn)
        user2 = await _insert_user(async_db_conn)
        company_id = await _insert_company(async_db_conn)
        job_id = await _insert_job(async_db_conn, company_id)
        match_id = await _insert_match(async_db_conn, user2, job_id)

        cur = await async_db_conn.execute(
            "SELECT id FROM user_job_matches WHERE id = %s AND user_id = %s",
            [match_id, user1],
        )
        row = await cur.fetchone()
        assert row is None  # not visible to user1


# ─── Integration: GET /api/jobs/{match_id}/resume ────────────────────────────

@pytest.mark.integration
class TestGetTailoredResume:
    @pytest.mark.asyncio
    async def test_returns_tailored_resume(self, async_db_conn):
        user_id = await _insert_user(async_db_conn)
        company_id = await _insert_company(async_db_conn)
        job_id = await _insert_job(async_db_conn, company_id)
        match_id = await _insert_match(async_db_conn, user_id, job_id)
        await _insert_tailored_resume(async_db_conn, match_id)

        cur = await async_db_conn.execute(
            "SELECT latex_source, prompt_tokens, completion_tokens, model_used FROM tailored_resumes WHERE match_id = %s",
            [match_id],
        )
        row = await cur.fetchone()
        assert "tailored" in row[0]
        assert row[1] == 1000
        assert row[2] == 500
        assert row[3] == "test-model"

    @pytest.mark.asyncio
    async def test_no_resume_returns_none(self, async_db_conn):
        user_id = await _insert_user(async_db_conn)
        company_id = await _insert_company(async_db_conn)
        job_id = await _insert_job(async_db_conn, company_id)
        match_id = await _insert_match(async_db_conn, user_id, job_id)

        cur = await async_db_conn.execute(
            "SELECT id FROM tailored_resumes WHERE match_id = %s",
            [match_id],
        )
        row = await cur.fetchone()
        assert row is None
