"""
Job dashboard endpoints.

GET /api/jobs               — paginated job list (dashboard view)
GET /api/jobs/{match_id}    — full job detail
GET /api/jobs/{match_id}/resume — tailored resume for a match
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.auth import require_auth
from app.db import get_conn
from app.schemas import (
    JobDetail,
    JobListItem,
    JobListResponse,
    TailoredResumeResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

_DASHBOARD_STATUSES = {"pending", "tailoring", "ready", "skipped"}
_APPLIED_STATUSES   = {"applied", "interviewing", "offer", "accepted", "rejected"}
_ALL_STATUSES       = _DASHBOARD_STATUSES | _APPLIED_STATUSES | {"deleted"}


# ─── Routes ──────────────────────────────────────────────────────────────────

@router.get("", response_model=JobListResponse)
async def list_jobs(
    user_id: str = Depends(require_auth),
    status: Annotated[list[str], Query()] = ["ready"],
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """
    Return paginated job matches for the authenticated user.

    Default status=ready (dashboard view). Accepts multiple status= params.
    Returns jobs ordered by match_score descending.
    """
    # Validate requested statuses
    invalid = set(status) - _ALL_STATUSES
    if invalid:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "INVALID_STATUS",
                "message": f"Unknown status values: {sorted(invalid)}",
            },
        )

    async with get_conn() as conn:
        # Total count
        count_cur = await conn.execute(
            """
            SELECT COUNT(*)
            FROM user_job_matches
            WHERE user_id = %s AND status = ANY(%s)
            """,
            [user_id, list(status)],
        )
        count_row = await count_cur.fetchone()
        total = count_row[0]

        # Paginated rows
        cur = await conn.execute(
            """
            SELECT
                m.id              AS match_id,
                m.job_id,
                j.title,
                j.company_name,
                j.location,
                j.work_type,
                m.match_score,
                m.status,
                m.gap_analysis,
                m.tailoring_failed_at,
                j.posted_at,
                (tr.id IS NOT NULL) AS has_tailored_resume
            FROM user_job_matches m
            JOIN jobs j ON j.id = m.job_id
            LEFT JOIN tailored_resumes tr ON tr.match_id = m.id
            WHERE m.user_id = %s AND m.status = ANY(%s)
            ORDER BY m.match_score DESC
            LIMIT %s OFFSET %s
            """,
            [user_id, list(status), limit, offset],
        )
        rows = await cur.fetchall()

    jobs = [
        JobListItem(
            match_id=str(row[0]),
            job_id=str(row[1]),
            title=row[2],
            company_name=row[3],
            location=row[4],
            work_type=row[5],
            match_score=float(row[6]),
            status=row[7],
            gap_analysis=row[8] if row[8] else {},
            tailoring_failed=row[9] is not None,
            posted_at=row[10].isoformat() if row[10] else None,
            has_tailored_resume=row[11],
        )
        for row in rows
    ]

    return JobListResponse(
        jobs=jobs,
        total=total,
        has_more=(offset + len(rows)) < total,
    )


@router.get("/{match_id}", response_model=JobDetail)
async def get_job(
    match_id: str,
    user_id: str = Depends(require_auth),
):
    """
    Full job detail including description and apply_url.

    Returns 404 for both not-found and wrong-user (no enumeration).
    """
    async with get_conn() as conn:
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
                m.tailoring_failed_at,
                j.posted_at,
                (tr.id IS NOT NULL) AS has_tailored_resume,
                j.description,
                j.apply_url
            FROM user_job_matches m
            JOIN jobs j ON j.id = m.job_id
            LEFT JOIN tailored_resumes tr ON tr.match_id = m.id
            WHERE m.id = %s AND m.user_id = %s
            """,
            [match_id, user_id],
        )
        row = await cur.fetchone()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "MATCH_NOT_FOUND", "message": "Job match not found"},
        )

    return JobDetail(
        match_id=str(row[0]),
        job_id=str(row[1]),
        title=row[2],
        company_name=row[3],
        location=row[4],
        work_type=row[5],
        match_score=float(row[6]),
        status=row[7],
        gap_analysis=row[8] if row[8] else {},
        tailoring_failed=row[9] is not None,
        posted_at=row[10].isoformat() if row[10] else None,
        has_tailored_resume=row[11],
        description=row[12],
        apply_url=row[13],
    )


@router.get("/{match_id}/resume", response_model=TailoredResumeResponse)
async def get_tailored_resume(
    match_id: str,
    user_id: str = Depends(require_auth),
):
    """
    Tailored LaTeX resume for a match.

    Returns 404 if no tailored resume exists yet (still tailoring or failed).
    """
    async with get_conn() as conn:
        # Verify match belongs to user first
        match_cur = await conn.execute(
            "SELECT id FROM user_job_matches WHERE id = %s AND user_id = %s",
            [match_id, user_id],
        )
        match_row = await match_cur.fetchone()
        if match_row is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "MATCH_NOT_FOUND", "message": "Job match not found"},
            )

        cur = await conn.execute(
            """
            SELECT latex_source, prompt_tokens, completion_tokens, model_used
            FROM tailored_resumes
            WHERE match_id = %s
            """,
            [match_id],
        )
        row = await cur.fetchone()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail={
                "code": "TAILORED_RESUME_NOT_FOUND",
                "message": "No tailored resume for this match",
            },
        )

    return TailoredResumeResponse(
        latex_source=row[0],
        prompt_tokens=row[1],
        completion_tokens=row[2],
        model_used=row[3],
    )
