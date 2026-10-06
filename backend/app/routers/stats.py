"""
System stats.

GET /api/stats — aggregate metrics for the dashboard stats bar
"""

import logging

from fastapi import APIRouter, Depends

from app.auth import require_auth
from app.db import get_conn
from app.schemas import StatsResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("", response_model=StatsResponse)
async def get_stats(user_id: str = Depends(require_auth)):
    """
    Return dashboard stats for the authenticated user.

    Includes: last fetch time, jobs found today, queue depth,
    tailoring status, match/apply counts, cumulative token usage.
    """
    async with get_conn() as conn:
        # Last completed fetch run
        fetch_cur = await conn.execute(
            """
            SELECT completed_at
            FROM fetch_runs
            WHERE status = 'completed'
            ORDER BY completed_at DESC
            LIMIT 1
            """,
        )
        fetch_row = await fetch_cur.fetchone()
        last_fetch_at = fetch_row[0].isoformat() if fetch_row and fetch_row[0] else None

        # Jobs found today (across all fetch runs)
        today_cur = await conn.execute(
            """
            SELECT COALESCE(SUM(jobs_new), 0)
            FROM fetch_runs
            WHERE status = 'completed'
              AND completed_at >= CURRENT_DATE
            """,
        )
        today_row = await today_cur.fetchone()
        jobs_found_today = int(today_row[0] or 0)

        # Queue depth (pending tasks)
        queue_cur = await conn.execute(
            "SELECT COUNT(*) FROM job_queue WHERE status = 'pending'",
        )
        queue_row = await queue_cur.fetchone()
        queue_depth = int(queue_row[0])

        # Tailoring in progress (user-scoped)
        tailor_prog_cur = await conn.execute(
            """
            SELECT COUNT(*)
            FROM user_job_matches
            WHERE user_id = %s AND status = 'tailoring'
            """,
            [user_id],
        )
        tailor_prog_row = await tailor_prog_cur.fetchone()
        tailoring_in_progress = int(tailor_prog_row[0])

        # Tailoring failed (user-scoped)
        tailor_fail_cur = await conn.execute(
            """
            SELECT COUNT(*)
            FROM user_job_matches
            WHERE user_id = %s AND tailoring_failed_at IS NOT NULL
            """,
            [user_id],
        )
        tailor_fail_row = await tailor_fail_cur.fetchone()
        tailoring_failed = int(tailor_fail_row[0])

        # Total matched jobs (user-scoped, any non-deleted status)
        matched_cur = await conn.execute(
            """
            SELECT COUNT(*)
            FROM user_job_matches
            WHERE user_id = %s AND status != 'deleted'
            """,
            [user_id],
        )
        matched_row = await matched_cur.fetchone()
        total_matched = int(matched_row[0])

        # Total applied (user-scoped)
        applied_cur = await conn.execute(
            """
            SELECT COUNT(*)
            FROM user_job_matches
            WHERE user_id = %s
              AND status IN ('applied', 'interviewing', 'offer', 'accepted', 'rejected')
            """,
            [user_id],
        )
        applied_row = await applied_cur.fetchone()
        total_applied = int(applied_row[0])

        # Cumulative token usage (user-scoped via match → tailored_resumes)
        tokens_cur = await conn.execute(
            """
            SELECT
                COALESCE(SUM(tr.prompt_tokens), 0),
                COALESCE(SUM(tr.completion_tokens), 0)
            FROM tailored_resumes tr
            JOIN user_job_matches m ON m.id = tr.match_id
            WHERE m.user_id = %s
            """,
            [user_id],
        )
        tokens_row = await tokens_cur.fetchone()
        cumulative_prompt_tokens     = int(tokens_row[0])
        cumulative_completion_tokens = int(tokens_row[1])

    return StatsResponse(
        last_fetch_at=last_fetch_at,
        jobs_found_today=jobs_found_today,
        queue_depth=queue_depth,
        tailoring_in_progress=tailoring_in_progress,
        tailoring_failed=tailoring_failed,
        total_matched=total_matched,
        total_applied=total_applied,
        cumulative_prompt_tokens=cumulative_prompt_tokens,
        cumulative_completion_tokens=cumulative_completion_tokens,
    )
