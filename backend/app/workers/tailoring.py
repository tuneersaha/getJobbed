"""
TailoringWorker — processes 'tailor_resume' tasks from job_queue.
Payload: {"match_id": "uuid"}

Flow:
  1. Load match + job + active resume from DB
  2. If tailored_resumes row already exists → skip OpenRouter, mark done (idempotent)
  3. Build prompt, call OpenRouter (outside any DB transaction)
  4. Validate output (Jake's Resume template macros)
  5. Store tailored_resumes, update match status → 'ready'

Retry logic:
  Attempt 1: immediate
  Attempt 2: +30s  (BaseWorker._mark_failed handles delay)
  Attempt 3: +120s
  All 3 exhausted → UPDATE match SET status='ready', tailoring_failed_at=NOW()
    Dashboard shows "Tailoring unavailable — apply with original resume" badge.

Side effects OUTSIDE transactions (no DB locks held during HTTP):
  commit mark-processing → call OpenRouter → commit store result
"""

import logging
import os
import time
from typing import Optional

from app.workers.base import BaseWorker

logger = logging.getLogger(__name__)

LLM_MAX_JD_CHARS = int(os.environ.get("LLM_MAX_JD_CHARS", "2500"))

# Jake's Resume template validation markers
_REQUIRED_MARKERS = [
    r"\documentclass",
    r"\begin{document}",
    r"\end{document}",
    r"\resumeSubheading",
]


# ─── Prompt builder ───────────────────────────────────────────────────────────

def _build_prompt(
    title: str,
    company_name: str,
    description: str,
    matching_skills: list[str],
    missing_skills: list[str],
    latex_source: str,
) -> tuple[str, str]:
    """Returns (system_prompt, user_message)."""
    system_prompt = (
        "You are an expert technical resume writer. "
        "You receive a LaTeX resume (Jake's Resume template) and a job description.\n\n"
        "Modify the resume to better match the job description.\n\n"
        "Rules:\n"
        "1. Keep the exact LaTeX structure, template commands, and macros unchanged\n"
        "2. Only modify TEXT inside existing entries — do not invent jobs, projects, or dates\n"
        "3. Strengthen bullet points using JD keywords where they genuinely apply to real experience\n"
        "4. Reorder bullets within each role to lead with most relevant for this JD\n"
        "5. Update the Skills section to emphasize skills the candidate has that appear in the JD\n"
        "6. Tailor the summary/objective if one exists\n"
        "7. Do NOT fabricate experience, titles, dates, or skills the candidate does not have\n"
        "8. Return ONLY the complete modified .tex — no markdown fences, no explanation"
    )

    user_message = (
        f"JOB TITLE: {title}\n"
        f"COMPANY: {company_name}\n\n"
        f"JOB DESCRIPTION:\n{description[:LLM_MAX_JD_CHARS]}\n\n"
        f"SKILLS TO EMPHASIZE (candidate has, present in JD): {', '.join(matching_skills)}\n"
        f"SKILLS CANDIDATE LACKS (do not fabricate): {', '.join(missing_skills[:10])}\n\n"
        f"ORIGINAL RESUME:\n{latex_source}"
    )

    return system_prompt, user_message


# ─── Output validation ────────────────────────────────────────────────────────

def _strip_fences(output: str) -> str:
    """Remove markdown code fences if the LLM wrapped the output."""
    output = output.strip()
    if output.startswith("```"):
        lines = [l for l in output.split("\n") if not l.startswith("```")]
        output = "\n".join(lines).strip()
    return output


def validate_output(output: str, original: str) -> Optional[str]:
    """
    Returns None if valid, or an error string if invalid.
    Checks: required markers present, length within 50%–200% of original.
    """
    for marker in _REQUIRED_MARKERS:
        if marker not in output:
            return f"missing marker: {marker!r}"

    ratio = len(output) / max(len(original), 1)
    if ratio < 0.5:
        return f"output too short (ratio={ratio:.2f})"
    if ratio > 2.0:
        return f"output too long (ratio={ratio:.2f})"

    return None


# ─── Worker ───────────────────────────────────────────────────────────────────

class TailoringWorker(BaseWorker):
    task_type = "tailor_resume"

    def _get_openrouter_client(self):
        from openai import AsyncOpenAI
        return AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=os.environ["OPENROUTER_API_KEY"],
        )

    async def process_task(self, task_id: int, payload: dict, attempts: int) -> None:
        match_id = payload.get("match_id")
        if not match_id:
            raise ValueError("tailor_resume payload missing match_id")

        from app.db import get_conn

        # ── Load match + job + resume ────────────────────────────────────────
        async with get_conn() as conn:
            match_cur = await conn.execute(
                """
                SELECT m.id, m.user_id, m.job_id, m.status, m.gap_analysis,
                       j.title, j.company_name, j.description,
                       r.id AS resume_id, r.latex_source
                FROM user_job_matches m
                JOIN jobs j ON j.id = m.job_id
                JOIN user_resumes r ON r.user_id = m.user_id AND r.is_active = TRUE
                WHERE m.id = %s
                """,
                [match_id],
            )
            row = await match_cur.fetchone()

        if row is None:
            # Job or resume deleted — fatal, no retry
            logger.warning("tailor_resume: match %s not found (job/resume deleted)", match_id)
            await self._mark_tailoring_failed(match_id)
            return

        (
            _match_id, user_id, job_id, match_status,
            gap_analysis,
            title, company_name, description,
            resume_id, latex_source,
        ) = row

        # ── Idempotency: already tailored? ───────────────────────────────────
        async with get_conn() as conn:
            existing_cur = await conn.execute(
                "SELECT id FROM tailored_resumes WHERE match_id = %s",
                [match_id],
            )
            if await existing_cur.fetchone() is not None:
                logger.info("tailor_resume: match %s already has tailored resume, marking ready", match_id)
                await self._set_match_ready(match_id)
                return

        # ── Extract gap analysis data ────────────────────────────────────────
        gap = gap_analysis or {}
        matching_skills: list[str] = gap.get("matching_skills", [])
        missing_skills:  list[str] = gap.get("missing_skills", [])

        # ── Build prompt ─────────────────────────────────────────────────────
        system_prompt, user_message = _build_prompt(
            title=title,
            company_name=company_name,
            description=description,
            matching_skills=matching_skills,
            missing_skills=missing_skills,
            latex_source=latex_source,
        )

        model = os.environ.get("LLM_MODEL", "deepseek/deepseek-chat")
        client = self._get_openrouter_client()

        # ── Call OpenRouter (outside DB transaction) ─────────────────────────
        t0 = time.monotonic()
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_message},
                ],
                temperature=0.3,
            )
        except Exception as exc:
            # Network / 5xx errors — let BaseWorker retry
            logger.warning(
                "tailor_resume.api_error match=%s attempt=%d error=%s",
                match_id, attempts, type(exc).__name__,
            )
            raise

        duration_ms = round((time.monotonic() - t0) * 1000)

        raw_output = response.choices[0].message.content or ""
        prompt_tokens     = response.usage.prompt_tokens     if response.usage else 0
        completion_tokens = response.usage.completion_tokens if response.usage else 0

        # ── Validate output ──────────────────────────────────────────────────
        tailored = _strip_fences(raw_output)
        validation_error = validate_output(tailored, latex_source)

        if validation_error:
            logger.warning(
                "tailor_resume.validation_fail match=%s attempt=%d reason=%s",
                match_id, attempts, validation_error,
            )
            # Treat validation failure as retryable (LLM variance)
            raise ValueError(f"Output validation failed: {validation_error}")

        # ── Store result (idempotent insert) ─────────────────────────────────
        async with get_conn() as conn:
            await conn.execute(
                """
                INSERT INTO tailored_resumes
                    (match_id, latex_source, model_used, prompt_tokens, completion_tokens)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (match_id) DO NOTHING
                """,
                [match_id, tailored, model, prompt_tokens, completion_tokens],
            )
            await conn.execute(
                """
                UPDATE user_job_matches
                SET status = 'ready', updated_at = NOW()
                WHERE id = %s AND status = 'tailoring'
                """,
                [match_id],
            )

        logger.info(
            "tailor_resume.success match=%s model=%s prompt_tokens=%d completion_tokens=%d duration_ms=%d",
            match_id, model, prompt_tokens, completion_tokens, duration_ms,
        )

    async def _mark_failed(self, task_id: int, error: str, attempts: int) -> None:
        """
        Override to intercept final failure (attempts >= max_attempts).
        On exhaustion: set tailoring_failed_at on the match, then call super.
        """
        from app.db import get_conn

        max_attempts = 3
        if attempts >= max_attempts:
            # Extract match_id from queue payload before delegating
            async with get_conn() as conn:
                cur = await conn.execute(
                    "SELECT payload FROM job_queue WHERE id = %s",
                    [task_id],
                )
                row = await cur.fetchone()
            if row:
                match_id = (row[0] or {}).get("match_id")
                if match_id:
                    await self._mark_tailoring_failed(match_id)

        await super()._mark_failed(task_id, error, attempts)

    async def _mark_tailoring_failed(self, match_id: str) -> None:
        from app.db import get_conn

        async with get_conn() as conn:
            await conn.execute(
                """
                UPDATE user_job_matches
                SET status = 'ready', tailoring_failed_at = NOW(), updated_at = NOW()
                WHERE id = %s AND status IN ('tailoring', 'pending')
                """,
                [match_id],
            )
        logger.warning("tailor_resume.fatal match=%s — tailoring_failed_at set", match_id)

    async def _set_match_ready(self, match_id: str) -> None:
        from app.db import get_conn

        async with get_conn() as conn:
            await conn.execute(
                """
                UPDATE user_job_matches
                SET status = 'ready', updated_at = NOW()
                WHERE id = %s AND status = 'tailoring'
                """,
                [match_id],
            )
