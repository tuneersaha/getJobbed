"""
Paylocity ATS fetcher.
GET https://recruiting.paylocity.com/recruiting/jobs/All/{guid}

Paylocity returns HTML with `window.pageData = {...}` containing a Jobs array.
The Accept: application/json path is kept as a fallback in case they ever support it.
"""

import asyncio
import json as _json
import logging
import re
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

# Extract `window.pageData = {...}` from the page HTML
_PAGE_DATA_RE = re.compile(r'window\.pageData\s*=\s*')


def _extract_page_data(html: str) -> dict | None:
    m = _PAGE_DATA_RE.search(html)
    if not m:
        return None
    try:
        obj, _ = _json.JSONDecoder().raw_decode(html, m.end())
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _normalize_job(job: dict, ats_slug: str, company_id: str | None, company_name: str) -> NormalizedJob | None:
    """Normalize a job dict from either the JSON API or the HTML pageData format."""
    # Support both camelCase (pageData) and snake_case (JSON API) field names
    job_id = str(
        job.get("JobId") or job.get("id") or job.get("jobId") or job.get("requisitionId") or ""
    )
    title = (
        job.get("JobTitle") or job.get("title") or job.get("jobTitle") or ""
    ).strip()
    if not job_id or not title:
        return None

    raw_desc = job.get("Description") or job.get("description") or job.get("jobDescription") or ""
    description = strip_html(raw_desc) if raw_desc else title

    # Location: prefer structured JobLocation, fall back to LocationName string
    loc_obj = job.get("JobLocation") or {}
    if loc_obj and isinstance(loc_obj, dict):
        city = loc_obj.get("City") or ""
        state = loc_obj.get("State") or ""
        location = f"{city}, {state}".strip(", ") if city or state else loc_obj.get("Name")
    else:
        location = job.get("LocationName") or job.get("location") or job.get("locationName") or None
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

    is_remote = job.get("IsRemote") or job.get("isRemote") or False
    if is_remote:
        work_type = "remote"
    else:
        work_type = detect_work_type(title, str(location or ""), description)

    citizenship = check_citizenship_requirement(description)

    posted_at: datetime | None = None
    date_str = (
        job.get("PublishedDate")
        or job.get("postedDate")
        or job.get("datePosted")
        or job.get("createdAt")
    )
    if date_str:
        try:
            posted_at = datetime.fromisoformat(str(date_str).replace("Z", "+00:00"))
        except Exception:
            pass

    return NormalizedJob(
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


async def fetch(
    client: httpx.AsyncClient,
    ats_slug: str,
    company_id: str | None,
    company_name: str,
) -> list[NormalizedJob]:
    url = f"{_BASE}/{ats_slug}"
    resp = None
    for attempt in range(3):
        try:
            resp = await client.get(url, headers={"Accept": "application/json"}, timeout=15)
        except Exception as exc:
            raise RuntimeError(f"paylocity/{ats_slug} request failed: {exc}") from exc

        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 60 * (attempt + 1)))
            retry_after = min(retry_after, 120)
            logger.warning("paylocity/%s 429 — sleeping %ds (attempt %d)", ats_slug, retry_after, attempt + 1)
            await asyncio.sleep(retry_after)
            continue
        break

    if resp.status_code == 404:
        raise ValueError(f"paylocity/{ats_slug} 404 — dead slug")
    if resp.status_code != 200:
        raise RuntimeError(f"paylocity/{ats_slug} HTTP {resp.status_code}")

    content_type = resp.headers.get("content-type", "")
    raw_jobs: list[dict] = []

    if "json" in content_type:
        try:
            data = resp.json()
        except Exception as exc:
            raise RuntimeError(f"paylocity/{ats_slug} JSON parse error: {exc}") from exc
        if isinstance(data, list):
            raw_jobs = data
        elif isinstance(data, dict):
            raw_jobs = data.get("jobs") or data.get("results") or data.get("Jobs") or []
    else:
        # HTML response — extract window.pageData
        page_data = _extract_page_data(resp.text)
        if page_data is None:
            logger.warning("paylocity/%s HTML response with no extractable pageData", ats_slug)
            return []
        raw_jobs = page_data.get("Jobs") or []

    results: list[NormalizedJob] = []
    for job in raw_jobs:
        try:
            nj = _normalize_job(job, ats_slug, company_id, company_name)
            if nj:
                results.append(nj)
        except Exception as exc:
            logger.warning("paylocity/%s job parse error: %s", ats_slug, exc)

    return results
