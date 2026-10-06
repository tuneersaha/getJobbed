"""
iCIMS ATS fetcher.
GET https://careers-{slug}.icims.com/jobs/search/json?ss=1&in_iframe=1
iCIMS JSON search endpoints vary by instance version; we handle multiple shapes.
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

SOURCE = "icims"
logger = logging.getLogger(__name__)


async def fetch(
    client: httpx.AsyncClient,
    ats_slug: str,
    company_id: str | None,
    company_name: str,
) -> list[NormalizedJob]:
    url = f"https://careers-{ats_slug}.icims.com/jobs/search/json?ss=1&in_iframe=1"
    try:
        resp = await client.get(
            url,
            headers={"Accept": "application/json"},
            timeout=15,
        )
    except Exception as exc:
        raise RuntimeError(f"icims/{ats_slug} request failed: {exc}") from exc

    if resp.status_code == 404:
        raise ValueError(f"icims/{ats_slug} 404 — dead slug")
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"icims/{ats_slug} HTTP {resp.status_code}")

    try:
        data = resp.json()
    except Exception as exc:
        raise RuntimeError(f"icims/{ats_slug} JSON parse error: {exc}") from exc

    # Handle multiple response shapes
    raw_jobs: list = []
    if isinstance(data, list):
        raw_jobs = data
    elif isinstance(data, dict):
        # shape A: {"searchResults": {"search_result_item": [...]}}
        sr = data.get("searchResults") or {}
        if isinstance(sr, dict):
            items = sr.get("search_result_item") or sr.get("searchResultItem") or []
            raw_jobs = items if isinstance(items, list) else [items]
        # shape B: {"jobs": [...]}
        elif "jobs" in data:
            raw_jobs = data["jobs"]
        # shape C: {"results": [...]}
        elif "results" in data:
            raw_jobs = data["results"]

    results: list[NormalizedJob] = []

    for job in raw_jobs:
        try:
            # iCIMS uses req_id or id
            job_id = str(
                job.get("req_id")
                or job.get("id")
                or job.get("jobId")
                or ""
            )
            # Title lives in different places by version
            profile = job.get("job_profile") or {}
            title = (
                (profile.get("job_title") or "")
                or job.get("title")
                or job.get("jobTitle")
                or ""
            ).strip()
            if not job_id or not title:
                continue

            raw_desc = (
                job.get("description")
                or job.get("job_description")
                or (job.get("job_requirement") or {}).get("requirement")
                or ""
            )
            description = strip_html(raw_desc) if raw_desc else title

            location = (
                job.get("location")
                or (job.get("formattedLocation") or {}).get("formatted")
                or None
            )
            if isinstance(location, dict):
                location = location.get("label") or location.get("name") or None

            apply_url = (
                job.get("url")
                or job.get("applyUrl")
                or f"https://careers-{ats_slug}.icims.com/jobs/{job_id}/job"
            )
            if not validate_apply_url(apply_url):
                apply_url = f"https://careers-{ats_slug}.icims.com/jobs/{job_id}/job"

            exp_min, exp_max = extract_experience(description)
            work_type = detect_work_type(title, str(location or ""), description)
            citizenship = check_citizenship_requirement(description)

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
                    posted_at=None,
                )
            )
        except Exception as exc:
            logger.warning("icims/%s job parse error: %s", ats_slug, exc)

    return results
