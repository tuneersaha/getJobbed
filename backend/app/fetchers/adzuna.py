"""
Adzuna India fetcher.
GET https://api.adzuna.com/v1/api/jobs/in/search/1?app_id=...&app_key=...&what={role}&results_per_page=50
Fetches for each desired_role in user_profiles.
Not a company-slug fetcher — queries directly by role keyword.
"""

import os
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

SOURCE = "adzuna"
logger = logging.getLogger(__name__)
_BASE = "https://api.adzuna.com/v1/api/jobs/in/search/1"


async def fetch(
    client: httpx.AsyncClient,
    roles: list[str],
) -> tuple[list[NormalizedJob], list[tuple[str, str]]]:
    """
    Fetch Adzuna India jobs for each role in `roles`.
    Returns (jobs, discovered_slugs) where discovered_slugs is a list of (ats_type, slug).
    """
    app_id = os.environ["ADZUNA_APP_ID"]
    app_key = os.environ["ADZUNA_APP_KEY"]

    all_jobs: list[NormalizedJob] = []
    discovered: list[tuple[str, str]] = []
    seen_ids: set[str] = set()

    for role in roles:
        try:
            resp = await client.get(
                _BASE,
                params={
                    "app_id": app_id,
                    "app_key": app_key,
                    "what": role,
                    "results_per_page": 50,
                    "content-type": "application/json",
                },
                timeout=15,
            )
        except Exception as exc:
            logger.warning("adzuna role=%r request failed: %s", role, exc)
            continue

        if resp.status_code != 200:
            logger.warning("adzuna role=%r HTTP %d", role, resp.status_code)
            continue

        try:
            data = resp.json()
        except Exception as exc:
            logger.warning("adzuna role=%r JSON parse error: %s", role, exc)
            continue

        for job in data.get("results") or []:
            try:
                job_id = str(job.get("id") or "")
                if not job_id or job_id in seen_ids:
                    continue
                seen_ids.add(job_id)

                title = (job.get("title") or "").strip()
                if not title:
                    continue

                raw_desc = job.get("description") or ""
                description = strip_html(raw_desc) if "<" in raw_desc else raw_desc.strip()

                company_name = (job.get("company") or {}).get("display_name") or "Unknown"
                location = (job.get("location") or {}).get("display_name") or None

                # Adzuna provides redirect_url, not direct apply_url
                apply_url = job.get("redirect_url") or ""
                if not validate_apply_url(apply_url):
                    continue

                # Self-expanding slug discovery from apply_url
                found = extract_ats_slug_from_url(apply_url)
                if found:
                    discovered.append(found)

                exp_min, exp_max = extract_experience(description)
                work_type = detect_work_type(title, location, description)
                citizenship = check_citizenship_requirement(description)

                posted_at: datetime | None = None
                created = job.get("created")
                if created:
                    try:
                        posted_at = datetime.fromisoformat(created.replace("Z", "+00:00"))
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
                logger.warning("adzuna job parse error: %s", exc)

    return all_jobs, discovered
