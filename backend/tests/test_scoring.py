"""
Tests for Sprint 3 scoring pipeline.

Unit tests: hard_filter, gap_analysis, composite_score — pure Python, no DB.
Integration tests: ScoringWorker.process_task against real DB.
"""

import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# ─── Unit: hard_filter ────────────────────────────────────────────────────────

class TestHardFilter:
    """Unit tests — no DB, no model."""

    def _job(self, **kw):
        from app.workers.scoring import _Job
        defaults = dict(
            id=str(uuid.uuid4()),
            title="Data Engineer",
            description="We need Python, SQL and dbt skills.",
            location="Hyderabad, India",
            work_type="onsite",
            experience_min=0,
            experience_max=2,
            requires_foreign_citizenship="not_required",
            embedding=None,
        )
        return _Job(**{**defaults, **kw})

    def _profile(self, **kw):
        from app.workers.scoring import _Profile
        defaults = dict(
            desired_roles=["Data Engineer", "Backend"],
            experience_years=1,
            locations=["India", "Hyderabad"],
            remote_ok=False,
            excluded_keywords=[],
            tailor_threshold=0.40,
        )
        return _Profile(**{**defaults, **kw})

    def test_passes_matching_job(self):
        from app.workers.scoring import hard_filter
        job = self._job()
        profile = self._profile()
        assert hard_filter(job, profile, set()) is None

    def test_excluded_job_id_rejected(self):
        from app.workers.scoring import hard_filter
        job = self._job()
        assert hard_filter(job, self._profile(), {job.id}) == "excluded"

    def test_senior_title_rejected(self):
        from app.workers.scoring import hard_filter
        job = self._job(title="Senior Data Engineer")
        assert hard_filter(job, self._profile(), set()) is not None

    def test_staff_title_rejected(self):
        from app.workers.scoring import hard_filter
        job = self._job(title="Staff Engineer")
        assert hard_filter(job, self._profile(), set()) is not None

    def test_role_mismatch_rejected(self):
        from app.workers.scoring import hard_filter
        job = self._job(title="Graphic Designer")  # no seniority words, wrong role
        assert hard_filter(job, self._profile(), set()) == "role_mismatch"

    def test_citizenship_required_rejected(self):
        from app.workers.scoring import hard_filter
        job = self._job(requires_foreign_citizenship="required")
        assert hard_filter(job, self._profile(), set()) == "citizenship"

    def test_experience_min_too_high_rejected(self):
        from app.workers.scoring import hard_filter
        job = self._job(experience_min=5)
        profile = self._profile(experience_years=1)
        reason = hard_filter(job, profile, set())
        assert reason is not None and reason.startswith("experience_min")

    def test_experience_zero_passes(self):
        from app.workers.scoring import hard_filter
        job = self._job(experience_min=0, experience_max=2)
        profile = self._profile(experience_years=0)
        assert hard_filter(job, profile, set()) is None

    def test_excluded_keyword_in_jd_rejected(self):
        from app.workers.scoring import hard_filter
        job = self._job(description="Must have Security Clearance level 5")
        profile = self._profile(excluded_keywords=["Security Clearance"])
        assert hard_filter(job, profile, set()) is not None

    def test_remote_job_passes_when_remote_ok(self):
        from app.workers.scoring import hard_filter
        job = self._job(work_type="remote", location="Remote")
        profile = self._profile(remote_ok=True, locations=[])
        assert hard_filter(job, profile, set()) is None

    def test_no_desired_roles_passes_all_titles(self):
        from app.workers.scoring import hard_filter
        job = self._job(title="Some Niche Role")
        profile = self._profile(desired_roles=[])
        assert hard_filter(job, profile, set()) is None


# ─── Unit: gap_analysis ───────────────────────────────────────────────────────

class TestGapAnalysis:
    def _job(self, description="Python SQL Docker Kubernetes"):
        from app.workers.scoring import _Job
        return _Job(
            id=str(uuid.uuid4()),
            title="Data Engineer",
            description=description,
            location=None,
            work_type="remote",
            experience_min=0,
            experience_max=2,
            requires_foreign_citizenship="not_required",
            embedding=None,
        )

    def test_matching_skills_detected(self):
        from app.workers.scoring import gap_analysis
        gap = gap_analysis(self._job("Python SQL Docker"), ["Python", "SQL"], 1)
        assert "Python" in gap["matching_skills"]
        assert "SQL" in gap["matching_skills"]

    def test_missing_skills_detected(self):
        from app.workers.scoring import gap_analysis
        gap = gap_analysis(self._job("Python SQL Docker"), ["Python"], 1)
        assert "SQL" in gap["missing_skills"] or "Docker" in gap["missing_skills"]

    def test_coverage_is_float_0_to_1(self):
        from app.workers.scoring import gap_analysis
        gap = gap_analysis(self._job("Python SQL"), ["Python"], 1)
        assert 0.0 <= gap["skills_coverage"] <= 1.0

    def test_no_experience_gap_when_sufficient(self):
        from app.workers.scoring import gap_analysis
        from app.workers.scoring import _Job
        job = _Job(
            id=str(uuid.uuid4()),
            title="Data Engineer",
            description="Python SQL",
            location=None,
            work_type="remote",
            experience_min=0,
            experience_max=2,
            requires_foreign_citizenship="not_required",
            embedding=None,
        )
        gap = gap_analysis(job, ["Python"], 2)
        assert gap["experience_gap"] is None

    def test_experience_gap_when_under(self):
        from app.workers.scoring import gap_analysis
        from app.workers.scoring import _Job
        job = _Job(
            id=str(uuid.uuid4()),
            title="Data Engineer",
            description="Python SQL",
            location=None,
            work_type="remote",
            experience_min=3,
            experience_max=5,
            requires_foreign_citizenship="not_required",
            embedding=None,
        )
        gap = gap_analysis(job, ["Python"], 1)
        assert gap["experience_gap"] is not None
        assert "3yr" in gap["experience_gap"]
        assert "1yr" in gap["experience_gap"]

    def test_full_coverage_with_all_skills(self):
        from app.workers.scoring import gap_analysis
        gap = gap_analysis(self._job("Python SQL"), ["Python", "SQL"], 1)
        assert gap["skills_coverage"] == 1.0
        assert gap["missing_skills"] == []


# ─── Unit: composite_score ────────────────────────────────────────────────────

class TestCompositeScore:
    def _gap(self, coverage=1.0, exp_gap=None):
        return {"skills_coverage": coverage, "experience_gap": exp_gap}

    def test_perfect_score(self):
        from app.workers.scoring import composite_score
        score = composite_score(1.0, self._gap(1.0))
        assert score == 1.0

    def test_weights_applied(self):
        from app.workers.scoring import composite_score
        # 0.7*0.8 + 0.3*0.6 = 0.56 + 0.18 = 0.74
        score = composite_score(0.8, self._gap(0.6))
        assert abs(score - 0.74) < 0.001

    def test_exp_gap_penalty_applied(self):
        from app.workers.scoring import composite_score, EXP_GAP_PENALTY
        base = composite_score(0.8, self._gap(0.6))
        penalised = composite_score(0.8, self._gap(0.6, exp_gap="JD asks 3yr, you have 1yr"))
        assert abs(penalised - base * EXP_GAP_PENALTY) < 0.001

    def test_score_rounded_to_4_decimals(self):
        from app.workers.scoring import composite_score
        score = composite_score(0.123456789, self._gap(0.5))
        assert score == round(score, 4)


# ─── Unit: validate_output (TailoringWorker) ─────────────────────────────────

class TestValidateOutput:
    _good = (
        r"\documentclass{article}"
        "\n"
        r"\begin{document}"
        "\n"
        r"\resumeSubheading{Acme}{Engineer}{2022}{2024}"
        "\n"
        r"\end{document}"
    )

    def test_valid_output_passes(self):
        from app.workers.tailoring import validate_output
        assert validate_output(self._good, self._good) is None

    def test_missing_documentclass_fails(self):
        from app.workers.tailoring import validate_output
        bad = self._good.replace(r"\documentclass{article}", "")
        assert validate_output(bad, self._good) is not None

    def test_missing_begin_document_fails(self):
        from app.workers.tailoring import validate_output
        bad = self._good.replace(r"\begin{document}", "")
        assert validate_output(bad, self._good) is not None

    def test_missing_resume_subheading_fails(self):
        from app.workers.tailoring import validate_output
        bad = self._good.replace(r"\resumeSubheading", "")
        assert validate_output(bad, self._good) is not None

    def test_too_short_fails(self):
        from app.workers.tailoring import validate_output
        short = self._good[:10]
        assert validate_output(short, self._good) is not None

    def test_strip_fences(self):
        from app.workers.tailoring import _strip_fences
        fenced = "```latex\n" + self._good + "\n```"
        assert _strip_fences(fenced) == self._good


# ─── Unit: _extract_plain_text (resume router) ───────────────────────────────

class TestExtractPlainText:
    def test_strips_commands(self):
        from app.routers.resume import _extract_plain_text
        latex = r"\textbf{Python} and \textit{SQL}"
        result = _extract_plain_text(latex)
        assert "Python" in result
        assert "SQL" in result
        assert "\\" not in result

    def test_collapses_whitespace(self):
        from app.routers.resume import _extract_plain_text
        latex = "Hello   \n\n  World"
        result = _extract_plain_text(latex)
        assert "  " not in result


# ─── Integration: ScoringWorker.process_task ─────────────────────────────────

@pytest.mark.integration
class TestScoringWorkerIntegration:
    """
    Integration tests — real DB, committed transactions.

    Uses db_pool so workers can call get_conn() (module-level singleton).
    Setup data must be committed before process_task() so the worker's
    separate pool connection can see it.
    """

    @pytest.mark.asyncio
    async def test_creates_match_for_passing_job(self, db_pool):
        """process_task creates a user_job_matches row when job passes filter."""
        async with db_pool.connection() as conn:
            user_id = await _insert_user(conn)
            await _insert_profile(conn, user_id)
            resume_emb = [0.1] * 384
            await _insert_resume(conn, user_id, embedding=resume_emb)
            job_id = await _insert_job(conn, title="Data Engineer", description="Python SQL Docker")
            await conn.commit()

        try:
            job_emb = [0.1] * 384
            with patch("app.embeddings.is_loaded", return_value=True):
                with patch("app.embeddings.encode", new=AsyncMock(return_value=job_emb)):
                    with patch("app.embeddings.cosine_similarity", return_value=1.0):
                        from app.workers.scoring import ScoringWorker
                        worker = ScoringWorker()
                        await worker.process_task(
                            task_id=1,
                            payload={"job_id": str(job_id), "user_id": str(user_id)},
                            attempts=1,
                        )

            async with db_pool.connection() as conn:
                cur = await conn.execute(
                    "SELECT match_score, status FROM user_job_matches WHERE user_id = %s AND job_id = %s",
                    [user_id, job_id],
                )
                row = await cur.fetchone()
                assert row is not None
                assert float(row[0]) > 0.0
        finally:
            await _cleanup(db_pool, user_id=user_id, job_id=job_id)

    @pytest.mark.asyncio
    async def test_skips_filtered_job(self, db_pool):
        """process_task does NOT create a match when job is filtered out."""
        async with db_pool.connection() as conn:
            user_id = await _insert_user(conn)
            await _insert_profile(conn, user_id)
            resume_emb = [0.1] * 384
            await _insert_resume(conn, user_id, embedding=resume_emb)
            job_id = await _insert_job(conn, title="Senior Data Engineer", description="Python SQL")
            await conn.commit()

        try:
            with patch("app.embeddings.is_loaded", return_value=True):
                from app.workers.scoring import ScoringWorker
                worker = ScoringWorker()
                await worker.process_task(
                    task_id=1,
                    payload={"job_id": str(job_id), "user_id": str(user_id)},
                    attempts=1,
                )

            async with db_pool.connection() as conn:
                cur = await conn.execute(
                    "SELECT id FROM user_job_matches WHERE user_id = %s AND job_id = %s",
                    [user_id, job_id],
                )
                assert await cur.fetchone() is None
        finally:
            await _cleanup(db_pool, user_id=user_id, job_id=job_id)

    @pytest.mark.asyncio
    async def test_idempotent_second_run(self, db_pool):
        """Running process_task twice produces the same match row (idempotent)."""
        async with db_pool.connection() as conn:
            user_id = await _insert_user(conn)
            await _insert_profile(conn, user_id)
            resume_emb = [0.1] * 384
            await _insert_resume(conn, user_id, embedding=resume_emb)
            job_id = await _insert_job(conn, description="Python SQL Docker")
            await conn.commit()

        try:
            job_emb = [0.1] * 384
            payload = {"job_id": str(job_id), "user_id": str(user_id)}

            with patch("app.embeddings.is_loaded", return_value=True):
                with patch("app.embeddings.encode", new=AsyncMock(return_value=job_emb)):
                    with patch("app.embeddings.cosine_similarity", return_value=1.0):
                        from app.workers.scoring import ScoringWorker
                        worker = ScoringWorker()
                        await worker.process_task(task_id=1, payload=payload, attempts=1)
                        await worker.process_task(task_id=2, payload=payload, attempts=1)

            async with db_pool.connection() as conn:
                cur = await conn.execute(
                    "SELECT COUNT(*) FROM user_job_matches WHERE user_id = %s AND job_id = %s",
                    [user_id, job_id],
                )
                row = await cur.fetchone()
                assert row[0] == 1
        finally:
            await _cleanup(db_pool, user_id=user_id, job_id=job_id)


# ─── Helpers for integration tests ───────────────────────────────────────────

async def _insert_user(conn):
    cur = await conn.execute(
        "INSERT INTO users (google_sub, email) VALUES (%s, %s) RETURNING id",
        [f"sub-{uuid.uuid4()}", f"{uuid.uuid4()}@test.com"],
    )
    return (await cur.fetchone())[0]


async def _insert_profile(conn, user_id):
    await conn.execute(
        """
        INSERT INTO user_profiles
            (user_id, desired_roles, experience_years, locations, remote_ok, tailor_threshold)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        [user_id, ["Data Engineer", "Backend"], 1, ["India"], True, 0.40],
    )


async def _insert_resume(conn, user_id, embedding=None):
    cur = await conn.execute(
        """
        INSERT INTO user_resumes (user_id, latex_source, parsed_skills, embedding, is_active)
        VALUES (%s, %s, %s, %s, TRUE)
        RETURNING id
        """,
        [user_id, r"\begin{document}Python SQL\end{document}", ["Python", "SQL"], embedding],
    )
    return (await cur.fetchone())[0]


async def _insert_job(conn, title="Data Engineer", description="Python SQL Docker"):
    cur = await conn.execute(
        """
        INSERT INTO jobs
            (external_id, source, company_name, title, description, apply_url,
             work_type, requires_foreign_citizenship, experience_min, experience_max)
        VALUES (%s, 'greenhouse', 'Test Co', %s, %s, 'https://example.com/apply',
                'remote', 'not_required', 0, 2)
        RETURNING id
        """,
        [str(uuid.uuid4()), title, description],
    )
    return (await cur.fetchone())[0]


async def _cleanup(pool, *, user_id, job_id):
    """Delete test rows in dependency order to avoid FK violations."""
    async with pool.connection() as conn:
        await conn.execute(
            "DELETE FROM user_job_matches WHERE user_id = %s OR job_id = %s",
            [user_id, job_id],
        )
        await conn.execute("DELETE FROM user_resumes WHERE user_id = %s", [user_id])
        await conn.execute("DELETE FROM user_profiles WHERE user_id = %s", [user_id])
        await conn.execute("DELETE FROM users WHERE id = %s", [user_id])
        await conn.execute("DELETE FROM jobs WHERE id = %s", [job_id])
        await conn.commit()
