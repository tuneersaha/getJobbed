"""
Greenhouse ATS fetcher.
GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
Returns all jobs for the company in one response (no pagination).
"""

import logging
from datetime import datetime, timezone

import httpx

from app.fetchers.base import (
    NormalizedJob,
    check_citizenship_requirement,
    detect_work_type,
    extract_experience,
    strip_html,
    validate_apply_url,
)

SOURCE = "greenhouse"
logger = logging.getLogger(__name__)
_BASE = "https://boards-api.greenhouse.io/v1/boards"


async def fetch(
    client: httpx.AsyncClient,
    ats_slug: str,
    company_id: str | None,
    company_name: str,
) -> list[NormalizedJob]:
    url = f"{_BASE}/{ats_slug}/jobs?content=true"
    try:
        resp = await client.get(url, timeout=15)
    except Exception as exc:
        raise RuntimeError(f"greenhouse/{ats_slug} request failed: {exc}") from exc

    if resp.status_code == 404:
        raise ValueError(f"greenhouse/{ats_slug} 404 — dead slug")
    if resp.status_code != 200:
        raise RuntimeError(f"greenhouse/{ats_slug} HTTP {resp.status_code}")

    try:
        data = resp.json()
    except Exception as exc:
        raise RuntimeError(f"greenhouse/{ats_slug} JSON parse error: {exc}") from exc

    raw_jobs = data.get("jobs", [])
    results: list[NormalizedJob] = []

    for job in raw_jobs:
        try:
            job_id = str(job.get("id", ""))
            title = (job.get("title") or "").strip()
            if not job_id or not title:
                continue

            raw_desc = job.get("content") or ""
            description = strip_html(raw_desc)
            location = (job.get("location") or {}).get("name") or None
            apply_url = job.get("absolute_url") or ""
            if not validate_apply_url(apply_url):
                apply_url = f"https://boards.greenhouse.io/{ats_slug}/jobs/{job_id}"

            exp_min, exp_max = extract_experience(description)
            work_type = detect_work_type(title, location, description)
            citizenship = check_citizenship_requirement(description)

            posted_at: datetime | None = None
            updated = job.get("updated_at")
            if updated:
                try:
                    posted_at = datetime.fromisoformat(updated.replace("Z", "+00:00"))
                except Exception:
                    pass

            results.append(
                NormalizedJob(
                    external_id=job_id,
                    source=SOURCE,
                    company_id=company_id,
                    company_name=company_name,
                    title=title,
                    description=description,
                    location=location,
                    work_type=work_type,
                    apply_url=apply_url,
                    experience_min=exp_min,
                    experience_max=exp_max,
                    requires_foreign_citizenship=citizenship,
                    posted_at=posted_at,
                )
            )
        except Exception as exc:
            logger.warning("greenhouse/%s job parse error: %s", ats_slug, exc)

    return results
