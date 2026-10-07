"""
BambooHR ATS fetcher.
Step 1: GET https://{slug}.bamboohr.com/careers/list  → job list (no descriptions)
Step 2: GET https://{slug}.bamboohr.com/careers/{id}?detail=true  → per-job description
Descriptions fetched concurrently via asyncio.gather (capped at 10 per company).
"""

import asyncio
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

SOURCE = "bamboohr"
logger = logging.getLogger(__name__)
_DETAIL_CONCURRENCY = 10


async def fetch(
    client: httpx.AsyncClient,
    ats_slug: str,
    company_id: str | None,
    company_name: str,
) -> list[NormalizedJob]:
    list_url = f"https://{ats_slug}.bamboohr.com/careers/list"
    try:
        resp = await client.get(
            list_url,
            headers={"Accept": "application/json"},
            timeout=15,
        )
    except Exception as exc:
        raise RuntimeError(f"bamboohr/{ats_slug} list request failed: {exc}") from exc

    if resp.status_code == 404:
        raise ValueError(f"bamboohr/{ats_slug} 404 — dead slug")
    if resp.status_code != 200:
        raise RuntimeError(f"bamboohr/{ats_slug} HTTP {resp.status_code}")
    # If slug doesn't exist, BambooHR 302s to www.bamboohr.com
    if resp.url.host != f"{ats_slug}.bamboohr.com":
        raise ValueError(f"bamboohr/{ats_slug} redirected to {resp.url.host} — dead slug")

    try:
        data = resp.json()
    except Exception as exc:
        raise RuntimeError(f"bamboohr/{ats_slug} JSON parse error: {exc}") from exc

    # BambooHR returns either {"result": [...]} or a direct list
    raw_jobs = data.get("result") if isinstance(data, dict) else data
    if not isinstance(raw_jobs, list):
        return []

    # Fetch descriptions concurrently (limited to _DETAIL_CONCURRENCY)
    sem = asyncio.Semaphore(_DETAIL_CONCURRENCY)

    async def fetch_detail(job_id: str) -> str:
        detail_url = f"https://{ats_slug}.bamboohr.com/careers/{job_id}?detail=true"
        async with sem:
            try:
                r = await client.get(
                    detail_url,
                    headers={"Accept": "application/json"},
                    timeout=10,
                )
                if r.status_code == 200:
                    d = r.json()
                    return d.get("description") or ""
            except Exception:
                pass
        return ""

    job_ids = [str(j.get("id", "")) for j in raw_jobs if j.get("id")]
    descriptions = await asyncio.gather(*[fetch_detail(jid) for jid in job_ids])
    desc_map = dict(zip(job_ids, descriptions))

    results: list[NormalizedJob] = []

    for job in raw_jobs:
        try:
            job_id = str(job.get("id", ""))
            title = (
                job.get("jobOpeningName")
                or (job.get("title") or {}).get("label")
                or ""
            ).strip()
            if not job_id or not title:
                continue

            raw_desc = desc_map.get(job_id) or ""
            description = strip_html(raw_desc) if raw_desc else title

            location_obj = job.get("location") or {}
            location = (
                location_obj.get("label")
                or location_obj.get("name")
                or None
            )

            apply_url = f"https://{ats_slug}.bamboohr.com/careers/{job_id}"

            exp_min, exp_max = extract_experience(description)
            work_type = detect_work_type(title, location, description)
            citizenship = check_citizenship_requirement(description)

            posted_at: datetime | None = None
            date_str = job.get("datePosted")
            if date_str:
                try:
                    posted_at = datetime.fromisoformat(date_str)
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
            logger.warning("bamboohr/%s job parse error: %s", ats_slug, exc)

    return results
