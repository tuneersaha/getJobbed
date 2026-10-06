"""
User profile / settings endpoints.

GET /api/profile  — return all profile fields
PUT /api/profile  — partial update (only provided fields are changed)
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import require_auth
from app.db import get_conn
from app.schemas import ProfileResponse, ProfileUpdate

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/profile", tags=["profile"])


@router.get("", response_model=ProfileResponse)
async def get_profile(user_id: str = Depends(require_auth)):
    """
    Return the user's profile. Auto-creates a default profile row if none exists.
    """
    async with get_conn() as conn:
        cur = await conn.execute(
            """
            SELECT
                desired_roles, experience_years, experience_level,
                max_experience_years, locations, remote_ok,
                excluded_keywords, tailor_threshold
            FROM user_profiles
            WHERE user_id = %s
            """,
            [user_id],
        )
        row = await cur.fetchone()

        if row is None:
            # Auto-create default profile
            ins_cur = await conn.execute(
                """
                INSERT INTO user_profiles (user_id)
                VALUES (%s)
                ON CONFLICT (user_id) DO NOTHING
                RETURNING
                    desired_roles, experience_years, experience_level,
                    max_experience_years, locations, remote_ok,
                    excluded_keywords, tailor_threshold
                """,
                [user_id],
            )
            row = await ins_cur.fetchone()

        if row is None:
            # Concurrent insert — re-read
            cur2 = await conn.execute(
                """
                SELECT
                    desired_roles, experience_years, experience_level,
                    max_experience_years, locations, remote_ok,
                    excluded_keywords, tailor_threshold
                FROM user_profiles WHERE user_id = %s
                """,
                [user_id],
            )
            row = await cur2.fetchone()

    return ProfileResponse(
        desired_roles=row[0] or [],
        experience_years=row[1],
        experience_level=row[2],
        max_experience_years=row[3],
        locations=row[4] or [],
        remote_ok=row[5],
        excluded_keywords=row[6] or [],
        tailor_threshold=float(row[7]),
    )


@router.put("", response_model=ProfileResponse)
async def update_profile(
    body: ProfileUpdate,
    user_id: str = Depends(require_auth),
):
    """
    Partial update — only fields provided in the request body are changed.
    Upserts profile row if it doesn't exist yet.

    Constraints enforced by Pydantic:
    - desired_roles: 1–10 items
    - experience_years: 0–10
    - tailor_threshold: 0.0–1.0
    - experience_level: entry | mid | senior
    """
    # Build SET clause dynamically from provided fields
    updates = body.model_dump(exclude_none=True)

    if not updates:
        # No fields → re-read and return current profile
        return await get_profile(user_id=user_id)

    # Ensure desired_roles has at least 1 item if provided
    if "desired_roles" in updates and len(updates["desired_roles"]) == 0:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "VALIDATION_ERROR",
                "message": "desired_roles must have at least 1 role",
            },
        )

    set_clauses = []
    values = []
    for field, value in updates.items():
        set_clauses.append(f"{field} = %s")
        values.append(value)

    set_clauses.append("updated_at = NOW()")
    values.append(user_id)

    sql = f"""
        INSERT INTO user_profiles (user_id)
        VALUES (%s)
        ON CONFLICT (user_id) DO UPDATE
        SET {', '.join(set_clauses)}
    """
    # For the INSERT path, user_id goes first; for the UPDATE SET clause, user_id is last
    # Restructure: upsert then select
    async with get_conn() as conn:
        # Ensure row exists
        await conn.execute(
            "INSERT INTO user_profiles (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING",
            [user_id],
        )

        # Apply updates
        update_sql = (
            "UPDATE user_profiles SET "
            + ", ".join(set_clauses[:-1])  # exclude the updated_at we'll add manually
            + ", updated_at = NOW() WHERE user_id = %s"
        )
        await conn.execute(update_sql, values)

        # Re-read to return current state
        cur = await conn.execute(
            """
            SELECT
                desired_roles, experience_years, experience_level,
                max_experience_years, locations, remote_ok,
                excluded_keywords, tailor_threshold
            FROM user_profiles WHERE user_id = %s
            """,
            [user_id],
        )
        row = await cur.fetchone()

    logger.info(
        "profile.updated user_id=%s fields=%s",
        user_id, list(updates.keys()),
    )

    return ProfileResponse(
        desired_roles=row[0] or [],
        experience_years=row[1],
        experience_level=row[2],
        max_experience_years=row[3],
        locations=row[4] or [],
        remote_ok=row[5],
        excluded_keywords=row[6] or [],
        tailor_threshold=float(row[7]),
    )
