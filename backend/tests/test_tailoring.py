"""
Tests for TailoringWorker.

Unit: validate_output, _strip_fences, _build_prompt.
Integration: process_task against real DB with mocked OpenRouter.
"""

import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

GOOD_LATEX = (
    r"\documentclass{article}"
    "\n"
    r"\begin{document}"
    "\n"
    r"\resumeSubheading{Acme Corp}{Data Engineer}{2022}{2024}"
    "\nBuilt data pipelines.\n"
    r"\end{document}"
)


# ─── Unit: validate_output ───────────────────────────────────────────────────

class TestValidateOutput:
    def test_valid_passes(self):
        from app.workers.tailoring import validate_output
        assert validate_output(GOOD_LATEX, GOOD_LATEX) is None

    def test_missing_documentclass_fails(self):
        from app.workers.tailoring import validate_output
        bad = GOOD_LATEX.replace(r"\documentclass{article}", "x")
        assert validate_output(bad, GOOD_LATEX) is not None

    def test_missing_begin_document_fails(self):
        from app.workers.tailoring import validate_output
        bad = GOOD_LATEX.replace(r"\begin{document}", "")
        assert validate_output(bad, GOOD_LATEX) is not None

    def test_missing_end_document_fails(self):
        from app.workers.tailoring import validate_output
        bad = GOOD_LATEX.replace(r"\end{document}", "")
        assert validate_output(bad, GOOD_LATEX) is not None

    def test_missing_resume_subheading_fails(self):
        from app.workers.tailoring import validate_output
        bad = GOOD_LATEX.replace(r"\resumeSubheading", "")
        assert validate_output(bad, GOOD_LATEX) is not None

    def test_too_short_fails(self):
        from app.workers.tailoring import validate_output
        original = GOOD_LATEX * 10  # make original long
        short = GOOD_LATEX          # output is <50% of original
        assert validate_output(short, original) is not None

    def test_too_long_fails(self):
        from app.workers.tailoring import validate_output
        original = GOOD_LATEX
        long_output = GOOD_LATEX * 5   # 500% of original
        result = validate_output(long_output, original)
        assert result is not None

    def test_error_message_mentions_marker(self):
        from app.workers.tailoring import validate_output
        bad = GOOD_LATEX.replace(r"\resumeSubheading", "")
        msg = validate_output(bad, GOOD_LATEX)
        assert "resumeSubheading" in msg


# ─── Unit: _strip_fences ─────────────────────────────────────────────────────

class TestStripFences:
    def test_no_fences_unchanged(self):
        from app.workers.tailoring import _strip_fences
        assert _strip_fences(GOOD_LATEX) == GOOD_LATEX

    def test_triple_backtick_stripped(self):
        from app.workers.tailoring import _strip_fences
        fenced = "```\n" + GOOD_LATEX + "\n```"
        assert _strip_fences(fenced) == GOOD_LATEX

    def test_lang_tag_stripped(self):
        from app.workers.tailoring import _strip_fences
        fenced = "```latex\n" + GOOD_LATEX + "\n```"
        assert _strip_fences(fenced) == GOOD_LATEX


# ─── Unit: _build_prompt ────────────────────────────────────────────────────

class TestBuildPrompt:
    def test_prompt_includes_title(self):
        from app.workers.tailoring import _build_prompt
        _, user_msg = _build_prompt("Data Engineer", "Acme", "Python SQL", ["Python"], [], GOOD_LATEX)
        assert "Data Engineer" in user_msg

    def test_prompt_includes_matching_skills(self):
        from app.workers.tailoring import _build_prompt
        _, user_msg = _build_prompt("DE", "Co", "Python", ["Python", "SQL"], ["Go"], GOOD_LATEX)
        assert "Python" in user_msg
        assert "SQL" in user_msg

    def test_prompt_truncates_jd(self):
        from app.workers.tailoring import _build_prompt, LLM_MAX_JD_CHARS
        long_jd = "x" * (LLM_MAX_JD_CHARS + 1000)
        _, user_msg = _build_prompt("DE", "Co", long_jd, [], [], GOOD_LATEX)
        # Description in prompt should be at most LLM_MAX_JD_CHARS
        assert user_msg.count("x") <= LLM_MAX_JD_CHARS

    def test_missing_skills_capped_at_10(self):
        from app.workers.tailoring import _build_prompt
        many_missing = [f"Skill{i}" for i in range(20)]
        _, user_msg = _build_prompt("DE", "Co", "desc", [], many_missing, GOOD_LATEX)
        # Only first 10 missing skills should appear
        for i in range(10):
            assert f"Skill{i}" in user_msg


# ─── Integration: TailoringWorker.process_task ───────────────────────────────

@pytest.mark.integration
class TestTailoringWorkerIntegration:
    """
    Integration tests — real DB, mocked OpenRouter.

    Uses db_pool so workers can call get_conn() (module-level singleton).
    Setup data committed before process_task() so worker's connection sees it.
    """

    @pytest.mark.asyncio
    async def test_stores_tailored_resume_on_success(self, db_pool):
        """Successful OpenRouter call stores tailored_resumes row + sets status=ready."""
        async with db_pool.connection() as conn:
            user_id, match_id, job_id = await _setup_match(conn)
            await conn.commit()

        try:
            mock_response = _mock_openrouter_response(GOOD_LATEX)

            with patch("app.workers.tailoring.TailoringWorker._get_openrouter_client") as mock_client_fn:
                mock_client = MagicMock()
                mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
                mock_client_fn.return_value = mock_client

                from app.workers.tailoring import TailoringWorker
                worker = TailoringWorker()
                await worker.process_task(task_id=1, payload={"match_id": str(match_id)}, attempts=1)

            async with db_pool.connection() as conn:
                cur = await conn.execute(
                    "SELECT latex_source FROM tailored_resumes WHERE match_id = %s",
                    [match_id],
                )
                row = await cur.fetchone()
                assert row is not None
                assert r"\resumeSubheading" in row[0]

                status_cur = await conn.execute(
                    "SELECT status FROM user_job_matches WHERE id = %s",
                    [match_id],
                )
                assert (await status_cur.fetchone())[0] == "ready"
        finally:
            await _cleanup(db_pool, user_id=user_id, job_id=job_id)

    @pytest.mark.asyncio
    async def test_idempotent_when_tailored_resume_exists(self, db_pool):
        """If tailored_resumes row already exists, skip OpenRouter and mark ready."""
        async with db_pool.connection() as conn:
            user_id, match_id, job_id = await _setup_match(conn)
            await conn.execute(
                """
                INSERT INTO tailored_resumes (match_id, latex_source, model_used, prompt_tokens, completion_tokens)
                VALUES (%s, %s, 'test-model', 0, 0)
                """,
                [match_id, GOOD_LATEX],
            )
            await conn.commit()

        try:
            call_count = 0

            with patch("app.workers.tailoring.TailoringWorker._get_openrouter_client") as mock_client_fn:
                mock_client = MagicMock()
                async def count_calls(*args, **kwargs):
                    nonlocal call_count
                    call_count += 1
                    return _mock_openrouter_response(GOOD_LATEX)
                mock_client.chat.completions.create = count_calls
                mock_client_fn.return_value = mock_client

                from app.workers.tailoring import TailoringWorker
                worker = TailoringWorker()
                await worker.process_task(task_id=1, payload={"match_id": str(match_id)}, attempts=1)

            assert call_count == 0
        finally:
            await _cleanup(db_pool, user_id=user_id, job_id=job_id)

    @pytest.mark.asyncio
    async def test_validation_failure_raises(self, db_pool):
        """LLM output failing validation raises ValueError (triggers retry)."""
        async with db_pool.connection() as conn:
            user_id, match_id, job_id = await _setup_match(conn)
            await conn.commit()

        try:
            bad_output = "not a valid latex document at all"
            mock_response = _mock_openrouter_response(bad_output)

            with patch("app.workers.tailoring.TailoringWorker._get_openrouter_client") as mock_client_fn:
                mock_client = MagicMock()
                mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
                mock_client_fn.return_value = mock_client

                from app.workers.tailoring import TailoringWorker
                worker = TailoringWorker()
                with pytest.raises(ValueError, match="validation"):
                    await worker.process_task(
                        task_id=1,
                        payload={"match_id": str(match_id)},
                        attempts=1,
                    )
        finally:
            await _cleanup(db_pool, user_id=user_id, job_id=job_id)


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _mock_openrouter_response(content: str):
    usage = MagicMock()
    usage.prompt_tokens = 100
    usage.completion_tokens = 50
    choice = MagicMock()
    choice.message.content = content
    resp = MagicMock()
    resp.choices = [choice]
    resp.usage = usage
    return resp


async def _setup_match(conn) -> tuple:
    """Insert user + profile + resume + job + match in 'tailoring' status. Returns (user_id, match_id, job_id)."""
    # User
    cur = await conn.execute(
        "INSERT INTO users (google_sub, email) VALUES (%s, %s) RETURNING id",
        [f"sub-{uuid.uuid4()}", f"{uuid.uuid4()}@test.com"],
    )
    user_id = (await cur.fetchone())[0]

    # Profile
    await conn.execute(
        """
        INSERT INTO user_profiles (user_id, desired_roles, experience_years, remote_ok, tailor_threshold)
        VALUES (%s, %s, 1, TRUE, 0.40)
        """,
        [user_id, ["Data Engineer"]],
    )

    # Resume
    await conn.execute(
        """
        INSERT INTO user_resumes (user_id, latex_source, parsed_skills, version, is_active)
        VALUES (%s, %s, %s, 1, TRUE)
        """,
        [user_id, GOOD_LATEX, ["Python", "SQL"]],
    )

    # Job
    job_cur = await conn.execute(
        """
        INSERT INTO jobs (external_id, source, company_name, title, description, apply_url,
                          work_type, requires_foreign_citizenship)
        VALUES (%s, 'greenhouse', 'Test Co', 'Data Engineer', 'Python SQL Docker',
                'https://example.com/apply', 'remote', 'not_required')
        RETURNING id
        """,
        [str(uuid.uuid4())],
    )
    job_id = (await job_cur.fetchone())[0]

    # Match in 'tailoring' status
    match_cur = await conn.execute(
        """
        INSERT INTO user_job_matches (user_id, job_id, match_score, gap_analysis, status)
        VALUES (%s, %s, 0.75, %s, 'tailoring')
        RETURNING id
        """,
        [user_id, job_id, '{"matching_skills": ["Python"], "missing_skills": ["Docker"], "skills_coverage": 0.5, "experience_gap": null}'],
    )
    match_id = (await match_cur.fetchone())[0]

    return user_id, match_id, job_id


async def _cleanup(pool, *, user_id, job_id):
    """Delete test rows in dependency order to avoid FK violations."""
    async with pool.connection() as conn:
        await conn.execute("DELETE FROM tailored_resumes WHERE match_id IN (SELECT id FROM user_job_matches WHERE user_id = %s OR job_id = %s)", [user_id, job_id])
        await conn.execute("DELETE FROM user_job_matches WHERE user_id = %s OR job_id = %s", [user_id, job_id])
        await conn.execute("DELETE FROM user_resumes WHERE user_id = %s", [user_id])
        await conn.execute("DELETE FROM user_profiles WHERE user_id = %s", [user_id])
        await conn.execute("DELETE FROM users WHERE id = %s", [user_id])
        await conn.execute("DELETE FROM jobs WHERE id = %s", [job_id])
        await conn.commit()
