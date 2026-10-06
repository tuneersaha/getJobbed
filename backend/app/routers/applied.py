"""
Applied jobs tracking.

GET  /api/applied               — paginated list of applied/interviewing/offer/etc.
PATCH /api/applied/{match_id}   — update status in hiring lifecycle
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth import require_auth
from app.db import get_conn
from app.schemas import AppliedListItem, AppliedListResponse, AppliedStatusUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/applied", tags=["applied"])

_APPLIED_STATUSES = ["applied", "interviewing", "offer", "accepted", "rejected"]

# Valid source statuses for lifecycle updates
_UPDATEABLE_FROM = {"applied", "interviewing", "offer"}


@router.get("", response_model=AppliedListResponse)
async def list_applied(
    user_id: str = Depends(require_auth),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """
    Return paginated applied jobs, newest first.
    Includes all post-apply statuses: applied, interviewing, offer, accepted, rejected.
    """
    async with get_conn() as conn:
        count_cur = await conn.execute(
            """
            SELECT COUNT(*)
            FROM user_job_matches
            WHERE user_id = %s AND status = ANY(%s)
            """,
            [user_id, _APPLIED_STATUSES],
        )
        count_row = await count_cur.fetchone()
        total = count_row[0]

        cur = await conn.execute(
            """
            SELECT
                m.id,
                m.job_id,
                j.title,
                j.company_name,
                j.location,
                j.work_type,
                m.match_score,
                m.status,
                m.gap_analysis,
                m.applied_at,
                j.posted_at
            FROM user_job_matches m
            JOIN jobs j ON j.id = m.job_id
            WHERE m.user_id = %s AND m.status = ANY(%s)
            ORDER BY m.applied_at DESC NULLS LAST, m.updated_at DESC
            LIMIT %s OFFSET %s
            """,
            [user_id, _APPLIED_STATUSES, limit, offset],
        )
        rows = await cur.fetchall()

    jobs = [
        AppliedListItem(
            match_id=str(row[0]),
            job_id=str(row[1]),
            title=row[2],
            company_name=row[3],
            location=row[4],
            work_type=row[5],
            match_score=float(row[6]),
            status=row[7],
            gap_analysis=row[8] if row[8] else {},
            applied_at=row[9].isoformat() if row[9] else None,
            posted_at=row[10].isoformat() if row[10] else None,
        )
        for row in rows
    ]

    return AppliedListResponse(
        jobs=jobs,
        total=total,
        has_more=(offset + len(rows)) < total,
    )


@router.patch("/{match_id}", status_code=200)
async def update_applied_status(
    match_id: str,
    body: AppliedStatusUpdate,
    user_id: str = Depends(require_auth),
):
    """
    Update hiring lifecycle status.

    Valid source statuses: applied, interviewing, offer
    Valid target statuses: interviewing, offer, accepted, rejected

    Idempotent: updating to the same status returns 200 with no error.
    Returns 404 for not-found or wrong-user.
    Returns 422 INVALID_STATUS_TRANSITION for disallowed transitions.
    """
    target = body.status

    async with get_conn() as conn:
        cur = await conn.execute(
            "SELECT status FROM user_job_matches WHERE id = %s AND user_id = %s",
            [match_id, user_id],
        )
        row = await cur.fetchone()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "MATCH_NOT_FOUND", "message": "Job match not found"},
        )

    current_status = row[0]

    # Idempotent: same status → 200 OK
    if current_status == target:
        return {"match_id": match_id, "status": target}

    if current_status not in _UPDATEABLE_FROM:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "INVALID_STATUS_TRANSITION",
                "message": f"Cannot update from '{current_status}' — only applied/interviewing/offer can be updated",
            },
        )

    async with get_conn() as conn:
        await conn.execute(
            """
            UPDATE user_job_matches
            SET status = %s, updated_at = NOW()
            WHERE id = %s AND user_id = %s
            """,
            [target, match_id, user_id],
        )

    logger.info(
        "match.status_updated match_id=%s user_id=%s %s→%s",
        match_id, user_id, current_status, target,
    )
    return {"match_id": match_id, "status": target}
