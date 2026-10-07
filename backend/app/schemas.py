"""
Pydantic request/response schemas for GetJobbed API.

All schemas validated at the API boundary.
RFC 7807 errors raised as HTTPException with detail={"code": "...", "message": "..."}.
"""

from __future__ import annotations

from typing import Any, Optional
from pydantic import BaseModel, Field


# ─── Jobs ─────────────────────────────────────────────────────────────────────

class JobListItem(BaseModel):
    match_id: str
    job_id: str
    title: str
    company_name: str
    location: Optional[str]
    work_type: str
    match_score: float
    status: str
    gap_analysis: dict[str, Any]
    has_tailored_resume: bool
    tailoring_failed: bool
    posted_at: Optional[str]


class JobListResponse(BaseModel):
    jobs: list[JobListItem]
    total: int
    has_more: bool


class JobDetail(BaseModel):
    match_id: str
    job_id: str
    title: str
    company_name: str
    location: Optional[str]
    work_type: str
    match_score: float
    status: str
    gap_analysis: dict[str, Any]
    has_tailored_resume: bool
    tailoring_failed: bool
    posted_at: Optional[str]
    description: str
    apply_url: str


class TailoredResumeResponse(BaseModel):
    latex_source: str
    prompt_tokens: int
    completion_tokens: int
    model_used: str


# ─── Matches ──────────────────────────────────────────────────────────────────

class MatchStatusUpdate(BaseModel):
    status: str = Field(..., pattern="^(applied|deleted)$")


# ─── Applied ──────────────────────────────────────────────────────────────────

class AppliedListItem(BaseModel):
    match_id: str
    job_id: str
    title: str
    company_name: str
    location: Optional[str]
    work_type: str
    match_score: float
    status: str
    gap_analysis: dict[str, Any]
    applied_at: Optional[str]
    posted_at: Optional[str]


class AppliedListResponse(BaseModel):
    jobs: list[AppliedListItem]
    total: int
    has_more: bool


class AppliedStatusUpdate(BaseModel):
    status: str = Field(..., pattern="^(interviewing|offer|accepted|rejected)$")


# ─── Profile ──────────────────────────────────────────────────────────────────

class ProfileResponse(BaseModel):
    desired_roles: list[str]
    experience_years: int
    experience_level: str
    max_experience_years: int
    locations: list[str]
    remote_ok: bool
    excluded_keywords: list[str]
    tailor_threshold: float


class ProfileUpdate(BaseModel):
    desired_roles: Optional[list[str]] = Field(
        None, min_length=1, max_length=10,
    )
    experience_years: Optional[int] = Field(None, ge=0, le=10)
    experience_level: Optional[str] = Field(
        None, pattern="^(entry|mid|senior)$",
    )
    max_experience_years: Optional[int] = Field(None, ge=0, le=20)
    locations: Optional[list[str]] = None
    remote_ok: Optional[bool] = None
    excluded_keywords: Optional[list[str]] = None
    tailor_threshold: Optional[float] = Field(None, ge=0.0, le=1.0)


# ─── Fetch trigger ────────────────────────────────────────────────────────────

class FetchTriggerResponse(BaseModel):
    task_ids: list[int]
    queued_at: str
    sources: list[str]


# ─── Stats ────────────────────────────────────────────────────────────────────

class StatsResponse(BaseModel):
    last_fetch_at: Optional[str]
    jobs_found_today: int
    queue_depth: int
    tailoring_in_progress: int
    tailoring_failed: int
    total_matched: int
    total_applied: int
    cumulative_prompt_tokens: int
    cumulative_completion_tokens: int
