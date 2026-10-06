"""
Match status transitions.

PATCH /api/matches/{match_id}  — mark applied or deleted
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import require_auth
from app.db import get_conn
from app.schemas import MatchStatusUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/matches", tags=["matches"])

# Valid source statuses per target transition
_APPLIED_FROM  = {"pending", "tailoring", "ready"}
_DELETED_FROM  = {"pending", "tailoring", "ready", "applied"}


@router.patch("/{match_id}", status_code=200)
async def update_match_status(
    match_id: str,
    body: MatchStatusUpdate,
    user_id: str = Depends(require_auth),
):
    """
    Transition a match to 'applied' or 'deleted'.

    applied:  sets status=applied, applied_at=NOW()
              valid from: pending, tailoring, ready

    deleted:  inserts user_excluded_jobs + hard deletes the match
              valid from: pending, tailoring, ready, applied
              cascade removes tailored_resumes (FK ON DELETE CASCADE)

    Returns 404 for not-found or wrong-user (no enumeration).
    Returns 422 INVALID_STATUS_TRANSITION for disallowed transitions.
    """
    target = body.status  # "applied" | "deleted"

    async with get_conn() as conn:
        # Fetch current match status + job_id
        cur = await conn.execute(
            "SELECT status, job_id FROM user_job_matches WHERE id = %s AND user_id = %s",
            [match_id, user_id],
        )
        row = await cur.fetchone()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "MATCH_NOT_FOUND", "message": "Job match not found"},
        )

    current_status = row[0]
    job_id = str(row[1])

    if target == "applied":
        if current_status not in _APPLIED_FROM:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "INVALID_STATUS_TRANSITION",
                    "message": f"Cannot transition from '{current_status}' to 'applied'",
                },
            )
        async with get_conn() as conn:
            await conn.execute(
                """
                UPDATE user_job_matches
                SET status = 'applied', applied_at = NOW(), updated_at = NOW()
                WHERE id = %s AND user_id = %s
                """,
                [match_id, user_id],
            )
        logger.info("match.applied match_id=%s user_id=%s", match_id, user_id)
        return {"match_id": match_id, "status": "applied"}

    # target == "deleted"
    if current_status not in _DELETED_FROM:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "INVALID_STATUS_TRANSITION",
                "message": f"Cannot transition from '{current_status}' to 'deleted'",
            },
        )

    async with get_conn() as conn:
        # Single transaction: exclude job + delete match
        await conn.execute(
            """
            INSERT INTO user_excluded_jobs (user_id, job_id, reason)
            VALUES (%s, %s, 'deleted')
            ON CONFLICT (user_id, job_id) DO NOTHING
            """,
            [user_id, job_id],
        )
        del_cur = await conn.execute(
            "DELETE FROM user_job_matches WHERE id = %s AND user_id = %s RETURNING id",
            [match_id, user_id],
        )
        deleted_row = await del_cur.fetchone()

    if deleted_row is None:
        # Race: deleted by another request between our SELECT and DELETE
        raise HTTPException(
            status_code=404,
            detail={"code": "MATCH_NOT_FOUND", "message": "Job match not found"},
        )

    logger.info("match.deleted match_id=%s user_id=%s job_id=%s", match_id, user_id, job_id)
    return {"match_id": match_id, "status": "deleted"}
