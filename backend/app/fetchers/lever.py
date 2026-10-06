"""
Lever ATS fetcher.
GET https://api.lever.co/v0/postings/{slug}?mode=json
Returns all postings in one JSON array.
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

SOURCE = "lever"
logger = logging.getLogger(__name__)
_BASE = "https://api.lever.co/v0/postings"


async def fetch(
    client: httpx.AsyncClient,
    ats_slug: str,
    company_id: str | None,
    company_name: str,
) -> list[NormalizedJob]:
    url = f"{_BASE}/{ats_slug}?mode=json"
    try:
        resp = await client.get(url, timeout=15)
    except Exception as exc:
        raise RuntimeError(f"lever/{ats_slug} request failed: {exc}") from exc

    if resp.status_code == 404:
        raise ValueError(f"lever/{ats_slug} 404 — dead slug")
    if resp.status_code != 200:
        raise RuntimeError(f"lever/{ats_slug} HTTP {resp.status_code}")

    try:
        raw_jobs = resp.json()
    except Exception as exc:
        raise RuntimeError(f"lever/{ats_slug} JSON parse error: {exc}") from exc

    if not isinstance(raw_jobs, list):
        return []

    results: list[NormalizedJob] = []

    for job in raw_jobs:
        try:
            job_id = str(job.get("id", ""))
            title = (job.get("text") or "").strip()
            if not job_id or not title:
                continue

            # Lever uses descriptionBody (HTML) or descriptionPlain
            raw_desc = job.get("descriptionBody") or job.get("descriptionPlain") or ""
            description = strip_html(raw_desc)
            if not description:
                description = (job.get("additionalPlain") or "").strip()

            categories = job.get("categories") or {}
            location = categories.get("location") or None
            apply_url = job.get("applyUrl") or f"https://jobs.lever.co/{ats_slug}/{job_id}/apply"
            if not validate_apply_url(apply_url):
                apply_url = f"https://jobs.lever.co/{ats_slug}/{job_id}/apply"

            exp_min, exp_max = extract_experience(description)
            work_type = detect_work_type(title, location, description)
            citizenship = check_citizenship_requirement(description)

            posted_at: datetime | None = None
            created_ms = job.get("createdAt")
            if created_ms:
                try:
                    posted_at = datetime.fromtimestamp(created_ms / 1000, tz=timezone.utc)
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
            logger.warning("lever/%s job parse error: %s", ats_slug, exc)

    return results
