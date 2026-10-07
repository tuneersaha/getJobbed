"""
Manual fetch trigger.

POST /api/fetch/trigger  — enqueue one fetch_jobs task per source (with cooldown guard)
"""

import datetime
import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from app.auth import require_auth
from app.db import get_conn
from app.workers.base import enqueue_task

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/fetch", tags=["fetch"])

_ALL_SOURCES = [
    "greenhouse", "lever", "ashby", "bamboohr", "icims",
    "paylocity", "workday", "adzuna", "remotive", "themuse",
]


@router.post("/trigger", status_code=202)
async def trigger_fetch(user_id: str = Depends(require_auth)):
    """
    Manually enqueue one fetch_jobs task per source.

    Cooldown: if a fetch run completed within the last FETCH_TRIGGER_COOLDOWN_MINUTES
    minutes, return 429 FETCH_COOLDOWN_ACTIVE.

    Returns 202 with task_ids and queued_at.
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

        # Enqueue one task per source — stagger slightly so workers don't all start at once
        task_ids = []
        queued_at = datetime.datetime.now(datetime.timezone.utc).isoformat()
        for i, source in enumerate(_ALL_SOURCES):
            task_id = await enqueue_task(
                conn,
                "fetch_jobs",
                {"source": source},
                run_at_offset_seconds=i * 2,
            )
            task_ids.append(task_id)

    logger.info("fetch.triggered sources=%d user_id=%s task_ids=%s", len(task_ids), user_id, task_ids)

    return JSONResponse(
        status_code=202,
        content={"task_ids": task_ids, "queued_at": queued_at, "sources": _ALL_SOURCES},
    )
