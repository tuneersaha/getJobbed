"""
Data retention cleanup — runs as a daily APScheduler job (not a queue-based worker).
Deletes stale rows from jobs, tailored_resumes, user_resumes, fetch_runs, job_queue.
Logs deletion counts to stdout; never writes to DB (that would defeat the purpose).
TTLs are configurable via env vars so Supabase free tier stays under 500 MB.
"""

import logging
import os

logger = logging.getLogger(__name__)

JOB_TTL_DAYS            = int(os.environ.get("JOB_TTL_DAYS", "90"))
TAILORED_RESUME_TTL_DAYS = int(os.environ.get("TAILORED_RESUME_TTL_DAYS", "60"))
FETCH_RUN_TTL_DAYS       = int(os.environ.get("FETCH_RUN_TTL_DAYS", "30"))
QUEUE_TTL_DAYS           = int(os.environ.get("QUEUE_TTL_DAYS", "7"))


async def cleanup_data() -> None:
    """
    Prune stale rows across all tables.
    Runs each DELETE in sequence (not one transaction — avoids long-held locks).
    """
    from app.db import get_conn

    logger.info("[cleanup] starting data retention pass")
    totals: dict[str, int] = {}

    # 1. Old jobs with no active (applied/interviewing/offer/accepted/rejected) match
    async with get_conn() as conn:
        cur = await conn.execute(
            f"""
            DELETE FROM jobs
            WHERE fetched_at < NOW() - INTERVAL '{JOB_TTL_DAYS} days'
              AND id NOT IN (
                SELECT job_id FROM user_job_matches
                WHERE status IN ('applied', 'interviewing', 'offer', 'accepted', 'rejected')
              )
            """,
        )
        totals["jobs"] = cur.rowcount or 0  # type: ignore[attr-defined]

    # 2. Stale tailored resumes on 'ready' matches (user can re-trigger from dashboard)
    async with get_conn() as conn:
        cur = await conn.execute(
            f"""
            DELETE FROM tailored_resumes tr
            USING user_job_matches m
            WHERE tr.match_id = m.id
              AND m.status = 'ready'
              AND m.created_at < NOW() - INTERVAL '{TAILORED_RESUME_TTL_DAYS} days'
            """,
        )
        totals["tailored_resumes"] = cur.rowcount or 0

    # 3. Inactive user resumes older than 30 days
    async with get_conn() as conn:
        cur = await conn.execute(
            """
            DELETE FROM user_resumes
            WHERE is_active = FALSE
              AND created_at < NOW() - INTERVAL '30 days'
            """,
        )
        totals["user_resumes"] = cur.rowcount or 0

    # 4. Old fetch_run audit rows
    async with get_conn() as conn:
        cur = await conn.execute(
            f"""
            DELETE FROM fetch_runs
            WHERE started_at < NOW() - INTERVAL '{FETCH_RUN_TTL_DAYS} days'
            """,
        )
        totals["fetch_runs"] = cur.rowcount or 0

    # 5. Completed/failed queue tasks
    async with get_conn() as conn:
        cur = await conn.execute(
            f"""
            DELETE FROM job_queue
            WHERE status IN ('done', 'dead')
              AND updated_at < NOW() - INTERVAL '{QUEUE_TTL_DAYS} days'
            """,
        )
        totals["job_queue"] = cur.rowcount or 0

    summary = "  ".join(f"{k}=-{v}" for k, v in totals.items())
    logger.info("[cleanup] done: %s", summary)
