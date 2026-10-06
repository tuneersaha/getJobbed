"""
Remotive remote jobs fetcher.
GET https://remotive.com/api/remote-jobs?category=software-dev&limit=100
Returns remote software jobs. Not company-slug based.
"""

import logging
from datetime import datetime

import httpx

from app.fetchers.base import (
    NormalizedJob,
    check_citizenship_requirement,
    detect_work_type,
    extract_experience,
    extract_ats_slug_from_url,
    strip_html,
    validate_apply_url,
)

SOURCE = "remotive"
logger = logging.getLogger(__name__)
_URL = "https://remotive.com/api/remote-jobs"
_CATEGORIES = ["software-dev", "data"]


async def fetch(
    client: httpx.AsyncClient,
) -> tuple[list[NormalizedJob], list[tuple[str, str]]]:
    """
    Returns (jobs, discovered_slugs).
    Fetches software-dev + data categories.
    """
    all_jobs: list[NormalizedJob] = []
    discovered: list[tuple[str, str]] = []
    seen_ids: set[str] = set()

    for category in _CATEGORIES:
        try:
            resp = await client.get(
                _URL,
                params={"category": category, "limit": 100},
                timeout=15,
            )
        except Exception as exc:
            logger.warning("remotive category=%r request failed: %s", category, exc)
            continue

        if resp.status_code != 200:
            logger.warning("remotive category=%r HTTP %d", category, resp.status_code)
            continue

        try:
            data = resp.json()
        except Exception as exc:
            logger.warning("remotive category=%r JSON parse error: %s", category, exc)
            continue

        for job in data.get("jobs") or []:
            try:
                job_id = str(job.get("id") or "")
                if not job_id or job_id in seen_ids:
                    continue
                seen_ids.add(job_id)

                title = (job.get("title") or "").strip()
                if not title:
                    continue

                raw_desc = job.get("description") or ""
                description = strip_html(raw_desc)

                company_name = job.get("company_name") or "Unknown"
                location = job.get("candidate_required_location") or "Remote"

                apply_url = job.get("url") or ""
                if not validate_apply_url(apply_url):
                    continue

                # Self-expanding slug discovery
                found = extract_ats_slug_from_url(apply_url)
                if found:
                    discovered.append(found)

                exp_min, exp_max = extract_experience(description)
                work_type = "remote"  # Remotive is all-remote by definition
                citizenship = check_citizenship_requirement(description)

                posted_at: datetime | None = None
                pub_date = job.get("publication_date")
                if pub_date:
                    try:
                        posted_at = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
                    except Exception:
                        pass

                all_jobs.append(
                    NormalizedJob(
                        external_id=job_id,
                        source=SOURCE,
                        company_id=None,
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
                logger.warning("remotive job parse error: %s", exc)

    return all_jobs, discovered
