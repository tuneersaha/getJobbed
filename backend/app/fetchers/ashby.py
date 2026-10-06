"""
Ashby ATS fetcher.
POST https://api.ashbyhq.com/posting-public/job-posting/list
Body: {"organizationHostedJobsPageName": slug}
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

SOURCE = "ashby"
logger = logging.getLogger(__name__)
_URL = "https://api.ashbyhq.com/posting-public/job-posting/list"


async def fetch(
    client: httpx.AsyncClient,
    ats_slug: str,
    company_id: str | None,
    company_name: str,
) -> list[NormalizedJob]:
    try:
        resp = await client.post(
            _URL,
            json={"organizationHostedJobsPageName": ats_slug},
            timeout=15,
        )
    except Exception as exc:
        raise RuntimeError(f"ashby/{ats_slug} request failed: {exc}") from exc

    if resp.status_code == 404:
        raise ValueError(f"ashby/{ats_slug} 404 — dead slug")
    if resp.status_code != 200:
        raise RuntimeError(f"ashby/{ats_slug} HTTP {resp.status_code}")

    try:
        data = resp.json()
    except Exception as exc:
        raise RuntimeError(f"ashby/{ats_slug} JSON parse error: {exc}") from exc

    raw_jobs = data.get("results") or data.get("jobPostings") or []
    if not isinstance(raw_jobs, list):
        return []

    results: list[NormalizedJob] = []

    for job in raw_jobs:
        try:
            job_id = str(job.get("id", ""))
            title = (job.get("title") or "").strip()
            if not job_id or not title:
                continue

            raw_desc = job.get("descriptionHtml") or job.get("descriptionPlain") or ""
            description = strip_html(raw_desc)

            location = job.get("locationName") or None
            if job.get("isRemote"):
                location = location or "Remote"

            apply_url = job.get("jobUrl") or f"https://jobs.ashbyhq.com/{ats_slug}/{job_id}"
            if not validate_apply_url(apply_url):
                apply_url = f"https://jobs.ashbyhq.com/{ats_slug}/{job_id}"

            exp_min, exp_max = extract_experience(description)
            work_type = detect_work_type(title, location, description)
            if job.get("isRemote") and work_type == "unknown":
                work_type = "remote"
            citizenship = check_citizenship_requirement(description)

            posted_at: datetime | None = None
            published = job.get("publishedAt")
            if published:
                try:
                    posted_at = datetime.fromisoformat(published.replace("Z", "+00:00"))
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
            logger.warning("ashby/%s job parse error: %s", ats_slug, exc)

    return results
