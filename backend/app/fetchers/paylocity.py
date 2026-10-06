"""
Paylocity ATS fetcher.
GET https://recruiting.paylocity.com/recruiting/jobs/All/{guid}
Slug is a UUID GUID. Accept: application/json to get JSON response.
"""

import logging
from datetime import datetime

import httpx

from app.fetchers.base import (
    NormalizedJob,
    check_citizenship_requirement,
    detect_work_type,
    extract_experience,
    strip_html,
    validate_apply_url,
)

SOURCE = "paylocity"
logger = logging.getLogger(__name__)
_BASE = "https://recruiting.paylocity.com/recruiting/jobs/All"


async def fetch(
    client: httpx.AsyncClient,
    ats_slug: str,  # UUID GUID
    company_id: str | None,
    company_name: str,
) -> list[NormalizedJob]:
    url = f"{_BASE}/{ats_slug}"
    try:
        resp = await client.get(
            url,
            headers={"Accept": "application/json"},
            timeout=15,
        )
    except Exception as exc:
        raise RuntimeError(f"paylocity/{ats_slug} request failed: {exc}") from exc

    if resp.status_code == 404:
        raise ValueError(f"paylocity/{ats_slug} 404 — dead slug")
    if resp.status_code != 200:
        raise RuntimeError(f"paylocity/{ats_slug} HTTP {resp.status_code}")

    # Paylocity may return JSON or HTML depending on Accept header support
    content_type = resp.headers.get("content-type", "")
    if "application/json" not in content_type and "json" not in content_type:
        logger.warning("paylocity/%s returned non-JSON content-type: %s", ats_slug, content_type)
        return []

    try:
        data = resp.json()
    except Exception as exc:
        raise RuntimeError(f"paylocity/{ats_slug} JSON parse error: {exc}") from exc

    # Paylocity JSON shape: {"jobs": [...]} or [...]
    raw_jobs: list = []
    if isinstance(data, list):
        raw_jobs = data
    elif isinstance(data, dict):
        raw_jobs = data.get("jobs") or data.get("results") or []

    results: list[NormalizedJob] = []

    for job in raw_jobs:
        try:
            job_id = str(job.get("id") or job.get("jobId") or job.get("requisitionId") or "")
            title = (job.get("title") or job.get("jobTitle") or "").strip()
            if not job_id or not title:
                continue

            raw_desc = job.get("description") or job.get("jobDescription") or ""
            description = strip_html(raw_desc) if raw_desc else title

            location = job.get("location") or job.get("locationName") or None
            if isinstance(location, dict):
                location = location.get("name") or location.get("label") or None

            apply_url = (
                job.get("applyUrl")
                or job.get("url")
                or f"https://recruiting.paylocity.com/recruiting/jobs/{ats_slug}/{job_id}"
            )
            if not validate_apply_url(apply_url):
                apply_url = f"https://recruiting.paylocity.com/recruiting/jobs/{ats_slug}/{job_id}"

            exp_min, exp_max = extract_experience(description)
            work_type = detect_work_type(title, str(location or ""), description)
            citizenship = check_citizenship_requirement(description)

            posted_at: datetime | None = None
            date_str = job.get("postedDate") or job.get("datePosted") or job.get("createdAt")
            if date_str:
                try:
                    posted_at = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
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
                    location=str(location) if location else None,
                    work_type=work_type,
                    apply_url=apply_url,
                    experience_min=exp_min,
                    experience_max=exp_max,
                    requires_foreign_citizenship=citizenship,
                    posted_at=posted_at,
                )
            )
        except Exception as exc:
            logger.warning("paylocity/%s job parse error: %s", ats_slug, exc)

    return results
