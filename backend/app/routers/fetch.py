"""
Manual fetch trigger.

POST /api/fetch/trigger  — enqueue a fetch_jobs task (with cooldown guard)
"""

import logging
import os

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from app.auth import require_auth
from app.db import get_conn
from app.schemas import FetchTriggerResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/fetch", tags=["fetch"])


@router.post("/trigger", status_code=202)
async def trigger_fetch(user_id: str = Depends(require_auth)):
    """
    Manually enqueue a fetch_jobs task.

    Cooldown: if a fetch run completed within the last FETCH_TRIGGER_COOLDOWN_MINUTES
    minutes, return 429 FETCH_COOLDOWN_ACTIVE.

    Returns 202 with task_id and queued_at.
    """
    cooldown_minutes = int(os.environ.get("FETCH_TRIGGER_COOLDOWN_MINUTES", "30"))

    async with get_conn() as conn:
        # Cooldown guard: check last completed fetch run
        cooldown_cur = await conn.execute(
            """
            SELECT completed_at
            FROM fetch_runs
            WHERE status = 'completed'
              AND completed_at > NOW() - INTERVAL '1 minute' * %s
            ORDER BY completed_at DESC
            LIMIT 1
            """,
            [cooldown_minutes],
        )
        cooldown_row = await cooldown_cur.fetchone()

        if cooldown_row is not None:
            raise HTTPException(
                status_code=429,
                detail={
                    "code": "FETCH_COOLDOWN_ACTIVE",
                    "message": (
                        f"A fetch completed recently. "
                        f"Wait {cooldown_minutes} minutes between manual triggers."
                    ),
                },
            )

        # Enqueue a fetch_jobs task for all sources
        ins_cur = await conn.execute(
            """
            INSERT INTO job_queue (task_type, payload, status, max_attempts)
            VALUES ('fetch_jobs', '{}', 'pending', 3)
            RETURNING id, created_at
            """,
        )
        row = await ins_cur.fetchone()
        task_id = row[0]
        queued_at = row[1].isoformat()

    logger.info("fetch.triggered task_id=%s user_id=%s", task_id, user_id)

    return JSONResponse(
        status_code=202,
        content={"task_id": task_id, "queued_at": queued_at},
    )
