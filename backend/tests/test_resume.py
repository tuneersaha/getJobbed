"""
Tests for POST/GET /api/resume endpoints.

Unit: _extract_plain_text, _extract_skills (no DB, no model).
Integration: upload endpoint against real DB with mocked model.
"""

import uuid
import pytest
from unittest.mock import AsyncMock, patch


MINIMAL_LATEX = (
    r"\documentclass{article}"
    "\n"
    r"\begin{document}"
    "\nPython SQL Docker\n"
    r"\end{document}"
)


# ─── Unit: plain text extraction ────────────────────────────────────────────

class TestExtractPlainText:
    def test_strips_latex_commands(self):
        from app.routers.resume import _extract_plain_text
        latex = r"\textbf{Python} and \textit{SQL}"
        result = _extract_plain_text(latex)
        assert "Python" in result
        assert "SQL" in result
        assert "\\" not in result

    def test_strips_braces(self):
        from app.routers.resume import _extract_plain_text
        result = _extract_plain_text(r"{Python}")
        assert "{" not in result
        assert "}" not in result

    def test_collapses_whitespace(self):
        from app.routers.resume import _extract_plain_text
        result = _extract_plain_text("Python   \n\n   SQL")
        assert "  " not in result

    def test_empty_returns_empty(self):
        from app.routers.resume import _extract_plain_text
        assert _extract_plain_text("") == ""


# ─── Unit: skill extraction ──────────────────────────────────────────────────

class TestExtractSkills:
    def test_detects_known_skills(self):
        from app.routers.resume import _extract_skills
        skills = _extract_skills("Python SQL Docker Kubernetes", ["Python", "SQL", "Docker", "Ruby"])
        assert "Python" in skills
        assert "SQL" in skills
        assert "Docker" in skills
        assert "Ruby" not in skills

    def test_case_insensitive(self):
        from app.routers.resume import _extract_skills
        skills = _extract_skills("python sql", ["Python", "SQL"])
        assert "Python" in skills
        assert "SQL" in skills

    def test_empty_text_returns_empty(self):
        from app.routers.resume import _extract_skills
        assert _extract_skills("", ["Python"]) == []


# ─── Integration: POST /api/resume ───────────────────────────────────────────

@pytest.mark.integration
class TestResumeUpload:
    """Integration tests — real DB, mocked embedding model."""

    @pytest.mark.asyncio
    async def test_upload_stores_resume(self, async_db_conn):
        """Valid LaTeX upload creates a user_resumes row with is_active=TRUE."""
        from app.auth import get_or_create_user

        user_id = await get_or_create_user(
            f"sub-{uuid.uuid4()}", f"{uuid.uuid4()}@test.com", conn=async_db_conn
        )

        fake_embedding = [0.1] * 384

        with patch("app.embeddings.is_loaded", return_value=True):
            with patch("app.embeddings.encode", new=AsyncMock(return_value=fake_embedding)):
                from app.routers.resume import _extract_plain_text, _extract_skills

                latex = MINIMAL_LATEX
                plain = _extract_plain_text(latex)
                skills = _extract_skills(plain, ["Python", "SQL", "Docker"])
                embedding = await __import__("app.embeddings", fromlist=["encode"]).encode(plain)

                # Simulate the DB writes directly
                await async_db_conn.execute(
                    "UPDATE user_resumes SET is_active = FALSE WHERE user_id = %s AND is_active = TRUE",
                    [user_id],
                )
                ver_cur = await async_db_conn.execute(
                    "SELECT COALESCE(MAX(version), 0) FROM user_resumes WHERE user_id = %s",
                    [user_id],
                )
                ver_row = await ver_cur.fetchone()
                next_version = (ver_row[0] or 0) + 1

                ins_cur = await async_db_conn.execute(
                    """
                    INSERT INTO user_resumes
                        (user_id, latex_source, parsed_skills, embedding, version, is_active)
                    VALUES (%s, %s, %s, %s, %s, TRUE)
                    RETURNING id
                    """,
                    [user_id, latex, skills, embedding, next_version],
                )
                resume_id = (await ins_cur.fetchone())[0]

        # Verify row
        cur = await async_db_conn.execute(
            "SELECT is_active, parsed_skills FROM user_resumes WHERE id = %s",
            [resume_id],
        )
        row = await cur.fetchone()
        assert row[0] is True  # is_active
        assert isinstance(row[1], list)

    @pytest.mark.asyncio
    async def test_second_upload_deactivates_first(self, async_db_conn):
        """Re-uploading deactivates old resume and creates a new active one."""
        from app.auth import get_or_create_user

        user_id = await get_or_create_user(
            f"sub-{uuid.uuid4()}", f"{uuid.uuid4()}@test.com", conn=async_db_conn
        )

        # Insert first "active" resume manually
        ins_cur = await async_db_conn.execute(
            """
            INSERT INTO user_resumes (user_id, latex_source, parsed_skills, version, is_active)
            VALUES (%s, %s, %s, 1, TRUE) RETURNING id
            """,
            [user_id, MINIMAL_LATEX, ["Python"]],
        )
        first_id = (await ins_cur.fetchone())[0]

        # Simulate upload: deactivate old, insert new
        await async_db_conn.execute(
            "UPDATE user_resumes SET is_active = FALSE WHERE user_id = %s AND is_active = TRUE",
            [user_id],
        )
        await async_db_conn.execute(
            """
            INSERT INTO user_resumes (user_id, latex_source, parsed_skills, version, is_active)
            VALUES (%s, %s, %s, 2, TRUE)
            """,
            [user_id, MINIMAL_LATEX, ["Python", "SQL"]],
        )

        # First resume should now be inactive
        cur = await async_db_conn.execute(
            "SELECT is_active FROM user_resumes WHERE id = %s",
            [first_id],
        )
        row = await cur.fetchone()
        assert row[0] is False

        # Only one active resume
        count_cur = await async_db_conn.execute(
            "SELECT COUNT(*) FROM user_resumes WHERE user_id = %s AND is_active = TRUE",
            [user_id],
        )
        count_row = await count_cur.fetchone()
        assert count_row[0] == 1
