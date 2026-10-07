"""
Workday ATS fetcher.
Slug format: "company|wd_num|site_id"  (pipe-delimited, stored verbatim in companies.ats_slug)
POST https://{company}.{wd_num}.myworkdayjobs.com/wday/cxs/{company}/{site_id}/jobs
Handles pagination (20 jobs per page).
"""

import asyncio
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

SOURCE = "workday"
logger = logging.getLogger(__name__)
_PAGE_SIZE = 20
_MAX_PAGES = 10  # cap at 200 jobs per company per run

# Known Workday pods to probe in robots.txt discovery
_WD_PODS = ["wd1", "wd2", "wd3", "wd5", "wd10", "wd12", "wd103"]
_ROBOTS_ALLOW_RE = re.compile(r"^Allow:\s*/([a-z0-9][a-z0-9_\-]+)/", re.IGNORECASE | re.MULTILINE)


async def discover_site_ids(client: httpx.AsyncClient, tenant: str) -> list[tuple[str, str]]:
    """
    Probe robots.txt across all known Workday pods for a given tenant name.
    Returns list of (wd_num, site_id) tuples found.
    The correct pod returns HTTP 200 with Allow: /{site_id}/ lines; wrong pods return 422.
    """
    _BLOCKLIST = {"wday", "cxs", "job", "jobs", "apply", "login", "redirect", "external"}

    async def probe(pod: str) -> list[tuple[str, str]]:
        url = f"https://{tenant}.{pod}.myworkdayjobs.com/robots.txt"
        try:
            r = await client.get(url, timeout=8)
            if r.status_code != 200:
                return []
            site_ids = [
                m.group(1) for m in _ROBOTS_ALLOW_RE.finditer(r.text)
                if m.group(1).lower() not in _BLOCKLIST and len(m.group(1)) >= 2
            ]
            return [(pod, sid) for sid in site_ids]
        except Exception:
            return []

    results = await asyncio.gather(*[probe(pod) for pod in _WD_PODS])
    found = [item for sublist in results for item in sublist]
    if found:
        logger.info("workday robots.txt discovery: tenant=%s found=%s", tenant, found)
    return found


def _parse_slug(ats_slug: str) -> tuple[str, str, str] | None:
    """Parse 'company|wd_num|site_id' slug. Returns None on malformed input."""
    parts = ats_slug.split("|")
    if len(parts) != 3:
        return None
    co, wd_num, site_id = parts
    if not co or not wd_num or not site_id:
        return None
    return co, wd_num, site_id


async def fetch(
    client: httpx.AsyncClient,
    ats_slug: str,
    company_id: str | None,
    company_name: str,
) -> list[NormalizedJob]:
    parsed = _parse_slug(ats_slug)
    if parsed is None:
        raise ValueError(f"workday invalid slug format: {ats_slug!r} (expected co|wd|site)")
    co, wd_num, site_id = parsed

    base_url = f"https://{co}.{wd_num}.myworkdayjobs.com/wday/cxs/{co}/{site_id}/jobs"
    results: list[NormalizedJob] = []
    offset = 0

    for _ in range(_MAX_PAGES):
        try:
            resp = await client.post(
                base_url,
                json={"appliedFacets": {}, "limit": _PAGE_SIZE, "offset": offset, "searchText": ""},
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                timeout=20,
            )
        except Exception as exc:
            raise RuntimeError(f"workday/{ats_slug} request failed: {exc}") from exc

        if resp.status_code == 404:
            raise ValueError(f"workday/{ats_slug} 404 — dead slug")
        if resp.status_code != 200:
            raise RuntimeError(f"workday/{ats_slug} HTTP {resp.status_code}")

        try:
            data = resp.json()
        except Exception as exc:
            raise RuntimeError(f"workday/{ats_slug} JSON parse error: {exc}") from exc

        page_jobs = data.get("jobPostings") or []
        if not page_jobs:
            break

        for job in page_jobs:
            try:
                # externalPath uniquely identifies a Workday job posting
                external_path = job.get("externalPath") or ""
                # Extract job ID from path: .../job/123456/Title → "123456"
                path_parts = [p for p in external_path.split("/") if p]
                job_id = path_parts[-2] if len(path_parts) >= 2 else external_path
                if not job_id:
                    continue

                title = (job.get("title") or "").strip()
                if not title:
                    continue

                location = job.get("locationsText") or None
                # Description is NOT in the list — use title+location as stub
                description = f"{title}. {location or ''}".strip()

                apply_url = (
                    f"https://{co}.{wd_num}.myworkdayjobs.com{external_path}"
                    if external_path
                    else f"https://{co}.{wd_num}.myworkdayjobs.com"
                )
                if not validate_apply_url(apply_url):
                    continue

                exp_min, exp_max = extract_experience(description)
                work_type = detect_work_type(title, location, description)
                citizenship = check_citizenship_requirement(description)

                posted_at: datetime | None = None
                date_str = job.get("postedOnDate")
                if date_str:
                    try:
                        # Workday uses MM/DD/YYYY format
                        posted_at = datetime.strptime(date_str, "%m/%d/%Y")
                    except Exception:
                        try:
                            posted_at = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
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
                logger.warning("workday/%s job parse error: %s", ats_slug, exc)

        total = data.get("total") or 0
        offset += _PAGE_SIZE
        if offset >= total:
            break

    return results
