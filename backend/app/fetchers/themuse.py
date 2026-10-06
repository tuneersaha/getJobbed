"""
The Muse fetcher.
GET https://www.themuse.com/api/public/jobs?level=Entry+Level&category={category}&page={n}
Fetches entry-level software/data jobs. Not company-slug based.
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

SOURCE = "themuse"
logger = logging.getLogger(__name__)
_BASE = "https://www.themuse.com/api/public/jobs"
_CATEGORIES = ["Software Engineer", "Data Science", "DevOps & Sysadmin"]
_MAX_PAGES = 3  # up to 3 pages per category


async def fetch(
    client: httpx.AsyncClient,
) -> list[NormalizedJob]:
    all_jobs: list[NormalizedJob] = []
    seen_ids: set[str] = set()

    for category in _CATEGORIES:
        for page in range(1, _MAX_PAGES + 1):
            try:
                resp = await client.get(
                    _BASE,
                    params={"level": "Entry Level", "category": category, "page": page},
                    timeout=15,
                )
            except Exception as exc:
                logger.warning("themuse category=%r page=%d request failed: %s", category, page, exc)
                break

            if resp.status_code != 200:
                logger.warning("themuse category=%r page=%d HTTP %d", category, page, resp.status_code)
                break

            try:
                data = resp.json()
            except Exception as exc:
                logger.warning("themuse category=%r page=%d JSON parse error: %s", category, page, exc)
                break

            results = data.get("results") or []
            if not results:
                break

            for job in results:
                try:
                    job_id = str(job.get("id") or "")
                    if not job_id or job_id in seen_ids:
                        continue
                    seen_ids.add(job_id)

                    title = (job.get("name") or "").strip()
                    if not title:
                        continue

                    raw_desc = job.get("contents") or ""
                    description = strip_html(raw_desc)

                    company_name = (job.get("company") or {}).get("name") or "Unknown"
                    locations = job.get("locations") or []
                    location = locations[0].get("name") if locations else None

                    refs = job.get("refs") or {}
                    apply_url = refs.get("landing_page") or ""
                    if not validate_apply_url(apply_url):
                        continue

                    exp_min, exp_max = extract_experience(description)
                    work_type = detect_work_type(title, location, description)
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
                    logger.warning("themuse job parse error: %s", exc)

            page_count = data.get("page_count") or 1
            if page >= page_count:
                break

    return all_jobs
