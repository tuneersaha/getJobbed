"""
ScoringWorker — processes 'score_job' tasks from job_queue.
Payload: {"job_id": "uuid", "user_id": "uuid"}
Triggered by: JobFetchWorker after inserting new jobs.

4-stage pipeline (cheapest first):
  Stage 1: Hard filter      (<1ms)  — title, seniority, experience, citizenship, location
  Stage 2: Semantic score   (~10ms) — sentence-transformers cosine similarity
  Stage 3: Gap analysis     (<1ms)  — taxonomy keyword match → JSONB
  Stage 4: Composite + gate (<1ms)  — score = 0.7×semantic + 0.3×coverage; threshold → tailor
"""

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from psycopg.types.json import Jsonb

from app import embeddings
from app.workers.base import BaseWorker, enqueue_task

logger = logging.getLogger(__name__)

# Scoring constants — edit here to change weights; re-score existing matches after change
SEMANTIC_WEIGHT = 0.7
COVERAGE_WEIGHT = 0.3
EXP_GAP_PENALTY = 0.85

SENIOR_TITLE_WORDS = frozenset({
    "senior", "staff", "principal", "director", "manager",
    "vp", "head of", "lead", "architect", "distinguished", "fellow",
    "sr.", " sr ", " sr,",
})


# ─── Skills taxonomy ─────────────────────────────────────────────────────────

def _find_data_dir() -> Path:
    if d := os.environ.get("DATA_DIR"):
        return Path(d)
    docker_path = Path("/app/data")
    if docker_path.exists():
        return docker_path
    # Local dev: walk up from CWD to find data/skills_taxonomy.json
    for parent in [Path.cwd()] + list(Path.cwd().parents):
        candidate = parent / "data"
        if (candidate / "skills_taxonomy.json").exists():
            return candidate
    raise FileNotFoundError("Cannot locate data/ directory with skills_taxonomy.json")


def _load_taxonomy() -> list[str]:
    path = _find_data_dir() / "skills_taxonomy.json"
    data = json.loads(path.read_text())
    return data.get("skills", data) if isinstance(data, dict) else data


_TAXONOMY: list[str] = _load_taxonomy()
_TAXONOMY_LOWER: list[str] = [s.lower() for s in _TAXONOMY]


# ─── Data helpers ─────────────────────────────────────────────────────────────

@dataclass
class _Job:
    id: str
    title: str
    description: str
    location: Optional[str]
    work_type: str
    experience_min: Optional[int]
    experience_max: Optional[int]
    requires_foreign_citizenship: str
    embedding: Optional[list[float]]


@dataclass
class _Profile:
    desired_roles: list[str]
    experience_years: int
    locations: list[str]
    remote_ok: bool
    excluded_keywords: list[str]
    tailor_threshold: float


# ─── Stage 1: Hard filter ────────────────────────────────────────────────────

def hard_filter(job: _Job, profile: _Profile, excluded_job_ids: set[str]) -> Optional[str]:
    """
    Return None if job passes all filters, or a rejection reason string.
    Cheapest checks first.
    """
    if job.id in excluded_job_ids:
        return "excluded"

    title_lower = job.title.lower()

    # Seniority guard — reject senior/staff/lead/etc. roles
    for word in SENIOR_TITLE_WORDS:
        if word in title_lower:
            return f"seniority:{word.strip()}"

    # Title must contain at least one desired role keyword (case-insensitive)
    if profile.desired_roles:
        if not any(role.lower() in title_lower for role in profile.desired_roles):
            return "role_mismatch"

    # Citizenship
    if job.requires_foreign_citizenship == "required":
        return "citizenship"

    # Experience bracket: reject if job.experience_min > user years
    if job.experience_min is not None and job.experience_min > profile.experience_years:
        return f"experience_min:{job.experience_min}>{profile.experience_years}"

    # Location: pass if remote_ok + job is remote, or if location matches
    if not profile.remote_ok and job.work_type != "remote":
        if profile.locations:
            loc = (job.location or "").lower()
            if not any(l.lower() in loc for l in profile.locations):
                return "location"

    # Excluded keywords in JD
    if profile.excluded_keywords:
        desc_lower = job.description.lower()
        for kw in profile.excluded_keywords:
            if kw.lower() in desc_lower:
                return f"excluded_keyword:{kw}"

    return None


# ─── Stage 3: Gap analysis ───────────────────────────────────────────────────

def gap_analysis(
    job: _Job,
    resume_skills: list[str],
    user_experience_years: int,
) -> dict:
    """
    Match taxonomy skills against JD and resume. Returns JSONB-ready dict.
    """
    desc_lower = job.description.lower()
    resume_set = {s.lower() for s in resume_skills}

    job_skills = [
        _TAXONOMY[i] for i, s in enumerate(_TAXONOMY_LOWER) if s in desc_lower
    ]
    job_skills_set = {s.lower() for s in job_skills}

    matching = sorted(s for s in job_skills if s.lower() in resume_set)
    missing  = sorted(s for s in job_skills if s.lower() not in resume_set)

    coverage = len(matching) / max(len(job_skills), 1)

    exp_gap = None
    if job.experience_min is not None and job.experience_min > user_experience_years:
        exp_gap = f"JD asks {job.experience_min}yr, you have {user_experience_years}yr"

    return {
        "matching_skills": matching,
        "missing_skills":  missing,
        "skills_coverage": round(coverage, 4),
        "experience_gap":  exp_gap,
    }


# ─── Stage 4: Composite score ────────────────────────────────────────────────

def composite_score(semantic: float, gap: dict) -> float:
    score = SEMANTIC_WEIGHT * semantic + COVERAGE_WEIGHT * gap["skills_coverage"]
    if gap["experience_gap"]:
        score *= EXP_GAP_PENALTY
    return round(score, 4)


# ─── Worker ──────────────────────────────────────────────────────────────────

class ScoringWorker(BaseWorker):
    task_type = "score_job"

    async def process_task(self, task_id: int, payload: dict, attempts: int) -> None:
        job_id = payload.get("job_id")
        if not job_id:
            raise ValueError("score_job payload missing job_id")

        from app.db import get_conn

        # If user_id provided score for that user; otherwise score for all active users
        user_id = payload.get("user_id")
        if user_id:
            user_ids = [user_id]
        else:
            async with get_conn() as conn:
                cur = await conn.execute(
                    """
                    SELECT DISTINCT up.user_id
                    FROM user_profiles up
                    JOIN user_resumes ur
                      ON ur.user_id = up.user_id AND ur.is_active = TRUE
                    WHERE array_length(up.desired_roles, 1) > 0
                    """
                )
                rows = await cur.fetchall()
            user_ids = [str(r[0]) for r in rows]
            if not user_ids:
                logger.info("score_job: no active users with profiles — skipping job %s", job_id)
                return

        for uid in user_ids:
            await self._score_for_user(job_id, uid)

    async def _score_for_user(self, job_id: str, user_id: str) -> None:
        from app.db import get_conn

        # ── Load job + profile + active resume ──────────────────────────────
        async with get_conn() as conn:
            job_row = await (await conn.execute(
                """
                SELECT id, title, description, location, work_type,
                       experience_min, experience_max,
                       requires_foreign_citizenship, embedding
                FROM jobs WHERE id = %s
                """,
                [job_id],
            )).fetchone()

            if job_row is None:
                logger.warning("score_job: job %s not found, skipping", job_id)
                return

            profile_row = await (await conn.execute(
                """
                SELECT desired_roles, experience_years, locations, remote_ok,
                       excluded_keywords, tailor_threshold
                FROM user_profiles WHERE user_id = %s
                """,
                [user_id],
            )).fetchone()

            if profile_row is None:
                logger.warning("score_job: no profile for user %s, skipping", user_id)
                return

            resume_row = await (await conn.execute(
                """
                SELECT id, parsed_skills, embedding
                FROM user_resumes WHERE user_id = %s AND is_active = TRUE
                """,
                [user_id],
            )).fetchone()

            if resume_row is None:
                logger.warning("score_job: no active resume for user %s, skipping", user_id)
                return

            # Excluded jobs for this user
            excluded_cur = await conn.execute(
                "SELECT job_id FROM user_excluded_jobs WHERE user_id = %s",
                [user_id],
            )
            excluded_ids = {str(r[0]) for r in await excluded_cur.fetchall()}

        job = _Job(
            id=str(job_row[0]),
            title=job_row[1],
            description=job_row[2],
            location=job_row[3],
            work_type=str(job_row[4]),
            experience_min=job_row[5],
            experience_max=job_row[6],
            requires_foreign_citizenship=str(job_row[7]),
            embedding=job_row[8],
        )

        profile = _Profile(
            desired_roles=profile_row[0] or [],
            experience_years=profile_row[1],
            locations=profile_row[2] or [],
            remote_ok=bool(profile_row[3]),
            excluded_keywords=profile_row[4] or [],
            tailor_threshold=float(profile_row[5]),
        )

        resume_skills: list[str] = resume_row[1] or []
        resume_embedding: Optional[list[float]] = resume_row[2]

        # ── Stage 1: Hard filter ─────────────────────────────────────────────
        reason = hard_filter(job, profile, excluded_ids)
        if reason:
            logger.debug("score_job: job %s filtered out (%s)", job_id, reason)
            return

        # ── Stage 2: Semantic score ──────────────────────────────────────────
        if not embeddings.is_loaded():
            raise RuntimeError("Embedding model not loaded — cannot score")

        # Compute + cache job embedding if not already stored
        job_emb = job.embedding
        if not job_emb:
            job_emb = await embeddings.encode(job.description)
            async with get_conn() as conn:
                await conn.execute(
                    "UPDATE jobs SET embedding = %s WHERE id = %s",
                    [job_emb, job_id],
                )

        if not resume_embedding:
            logger.warning("score_job: resume has no embedding for user %s", user_id)
            return

        semantic = embeddings.cosine_similarity(job_emb, resume_embedding)

        # ── Stage 3: Gap analysis ────────────────────────────────────────────
        gap = gap_analysis(job, resume_skills, profile.experience_years)

        # ── Stage 4: Composite + threshold gate ─────────────────────────────
        score = composite_score(semantic, gap)
        logger.info(
            "score_job: job=%s user=%s score=%.4f semantic=%.4f coverage=%.4f",
            job_id, user_id, score, semantic, gap["skills_coverage"],
        )

        above_threshold = score >= profile.tailor_threshold
        new_status = "tailoring" if above_threshold else "ready"

        # ── Upsert match (idempotent) ────────────────────────────────────────
        async with get_conn() as conn:
            match_cur = await conn.execute(
                """
                INSERT INTO user_job_matches (user_id, job_id, match_score, gap_analysis, status)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (user_id, job_id)
                DO UPDATE SET
                    match_score  = EXCLUDED.match_score,
                    gap_analysis = EXCLUDED.gap_analysis,
                    status       = CASE
                        WHEN user_job_matches.status IN ('applied','interviewing','offer','accepted','rejected')
                        THEN user_job_matches.status  -- never downgrade post-apply statuses
                        ELSE EXCLUDED.status
                    END,
                    updated_at   = NOW()
                RETURNING id, status
                """,
                [user_id, job_id, score, Jsonb(gap), new_status],
            )
            match_row = await match_cur.fetchone()
            match_id = str(match_row[0])
            final_status = str(match_row[1])

            # Enqueue tailor_resume only if status is now 'tailoring'
            # Guard: skip if a pending/processing/done tailor task already exists
            if final_status == "tailoring":
                existing_cur = await conn.execute(
                    """
                    SELECT id FROM job_queue
                    WHERE task_type = 'tailor_resume'
                      AND (payload->>'match_id') = %s
                      AND status IN ('pending', 'processing', 'done')
                    LIMIT 1
                    """,
                    [match_id],
                )
                if await existing_cur.fetchone() is None:
                    await enqueue_task(conn, "tailor_resume", {"match_id": match_id})

        logger.info(
            "score_job done: match_id=%s status=%s score=%.4f",
            match_id, final_status, score,
        )
