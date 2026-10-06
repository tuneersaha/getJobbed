"""
Resume endpoints.

POST /api/resume  — upload LaTeX resume, parse skills, embed, store
GET  /api/resume  — metadata only (resume_id, uploaded_at, parsed_skills)
GET  /api/resume/source — returns latex_source (explicit, separate endpoint)
"""

import logging
import os
import re
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.auth import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/resume", tags=["resume"])

RESUME_MAX_BYTES = int(os.environ.get("RESUME_MAX_BYTES", str(2 * 1024 * 1024)))  # 2MB


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _extract_plain_text(latex_source: str) -> str:
    """Strip LaTeX command names but keep their arguments, then remove remaining syntax."""
    # Remove command names only (keep the content inside braces)
    text = re.sub(r"\\[a-zA-Z]+\*?", " ", latex_source)
    # Remove remaining braces and backslashes
    text = re.sub(r"[{}\\]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _extract_skills(plain_text: str, taxonomy: list[str]) -> list[str]:
    text_lower = plain_text.lower()
    return sorted(s for s in taxonomy if s.lower() in text_lower)


def _load_taxonomy() -> list[str]:
    """Reuse scoring module's loaded taxonomy to avoid double load."""
    try:
        from app.workers.scoring import _TAXONOMY
        return _TAXONOMY
    except ImportError:
        return []


# ─── Routes ──────────────────────────────────────────────────────────────────

@router.post("", status_code=201)
async def upload_resume(request: Request, user_id: str = Depends(require_auth)):
    """
    Upload a LaTeX resume.

    Accepts raw body as text/plain or application/x-tex.
    Max 2MB. Must contain \\begin{document} and \\end{document}.
    Parses skills, computes embedding, stores with is_active=TRUE.
    Returns 201 with resume_id + parsed_skills.
    """
    # ── Content-Type check ───────────────────────────────────────────────────
    content_type = request.headers.get("content-type", "")
    if not any(ct in content_type for ct in ("text/plain", "application/x-tex", "text/")):
        raise HTTPException(
            status_code=415,
            detail={"code": "UNSUPPORTED_MEDIA_TYPE", "message": "Upload as text/plain or application/x-tex"},
        )

    # ── Size check (before reading body) ────────────────────────────────────
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > RESUME_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail={"code": "RESUME_TOO_LARGE", "message": f"Resume must be under {RESUME_MAX_BYTES // 1024}KB"},
        )

    body = await request.body()
    if len(body) > RESUME_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail={"code": "RESUME_TOO_LARGE", "message": f"Resume must be under {RESUME_MAX_BYTES // 1024}KB"},
        )

    # ── Decode UTF-8 ─────────────────────────────────────────────────────────
    try:
        latex_source = body.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=400,
            detail={"code": "RESUME_INVALID_ENCODING", "message": "Resume must be valid UTF-8"},
        )

    # ── LaTeX structure validation ───────────────────────────────────────────
    if r"\begin{document}" not in latex_source or r"\end{document}" not in latex_source:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "RESUME_INVALID_LATEX",
                "message": r"Resume must contain \begin{document} and \end{document}",
            },
        )

    # ── Embedding availability check ─────────────────────────────────────────
    from app import embeddings as emb_mod
    if not emb_mod.is_loaded():
        raise HTTPException(
            status_code=503,
            detail={"code": "EMBEDDING_SERVICE_UNAVAILABLE", "message": "Embedding model not ready"},
        )

    t0 = time.monotonic()

    # ── Extract text + skills ────────────────────────────────────────────────
    plain_text = _extract_plain_text(latex_source)
    taxonomy = _load_taxonomy()
    parsed_skills = _extract_skills(plain_text, taxonomy)

    # ── Compute embedding ────────────────────────────────────────────────────
    embedding = await emb_mod.encode(plain_text)

    # ── Store (atomic swap — deactivate old, insert new) ─────────────────────
    from app.db import get_conn
    from psycopg.types.json import Jsonb

    async with get_conn() as conn:
        # Deactivate previous active resume
        await conn.execute(
            "UPDATE user_resumes SET is_active = FALSE WHERE user_id = %s AND is_active = TRUE",
            [user_id],
        )

        # Compute next version number
        ver_cur = await conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM user_resumes WHERE user_id = %s",
            [user_id],
        )
        ver_row = await ver_cur.fetchone()
        next_version = (ver_row[0] or 0) + 1

        # Insert new active resume
        ins_cur = await conn.execute(
            """
            INSERT INTO user_resumes
                (user_id, latex_source, parsed_skills, embedding, version, is_active)
            VALUES (%s, %s, %s, %s, %s, TRUE)
            RETURNING id, created_at
            """,
            [user_id, latex_source, parsed_skills, embedding, next_version],
        )
        row = await ins_cur.fetchone()
        resume_id = str(row[0])
        uploaded_at = row[1].isoformat()

    duration_ms = round((time.monotonic() - t0) * 1000)
    # Never log latex_source content
    logger.info(
        "resume.uploaded user_id=%s resume_id=%s size_bytes=%d skills=%d version=%d duration_ms=%d",
        user_id, resume_id, len(body), len(parsed_skills), next_version, duration_ms,
    )

    return JSONResponse(
        status_code=201,
        content={
            "resume_id": resume_id,
            "parsed_skills": parsed_skills,
            "uploaded_at": uploaded_at,
        },
    )


@router.get("")
async def get_resume(user_id: str = Depends(require_auth)):
    """
    Returns resume metadata: resume_id, uploaded_at, parsed_skills.
    Does NOT return latex_source — use GET /api/resume/source for that.
    """
    from app.db import get_conn

    async with get_conn() as conn:
        cur = await conn.execute(
            """
            SELECT id, created_at, parsed_skills
            FROM user_resumes
            WHERE user_id = %s AND is_active = TRUE
            """,
            [user_id],
        )
        row = await cur.fetchone()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "RESUME_NOT_FOUND", "message": "No active resume"},
        )

    return {
        "resume_id": str(row[0]),
        "uploaded_at": row[1].isoformat(),
        "parsed_skills": row[2] or [],
    }


@router.get("/source")
async def get_resume_source(user_id: str = Depends(require_auth)):
    """
    Returns the raw LaTeX source of the active resume.
    Separate endpoint so callers explicitly request sensitive content.
    """
    from app.db import get_conn

    async with get_conn() as conn:
        cur = await conn.execute(
            "SELECT latex_source FROM user_resumes WHERE user_id = %s AND is_active = TRUE",
            [user_id],
        )
        row = await cur.fetchone()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail={"code": "RESUME_NOT_FOUND", "message": "No active resume"},
        )

    return {"latex_source": row[0]}
