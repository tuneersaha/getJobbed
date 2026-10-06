"""
APScheduler setup embedded in the FastAPI process.
Cron jobs:
  - fetch_jobs:          every 6 hours (FETCH_CRON_SCHEDULE env var)
  - sync_company_lists:  every Sunday 02:00
  - cleanup_data:        every day 03:00

All jobs are async (AsyncIOScheduler). They acquire DB connections from the
running pool — do NOT call any scheduler function before the DB pool is open.
"""

import logging
import os

from apscheduler.schedulers.asyncio import AsyncIOScheduler

logger = logging.getLogger(__name__)

# Default: 0 */6 * * *  (every 6 hours at :00)
FETCH_CRON_SCHEDULE = os.environ.get("FETCH_CRON_SCHEDULE", "0 */6 * * *")


def _parse_cron(cron: str) -> dict:
    """Convert a 5-part cron string to APScheduler keyword args."""
    parts = cron.strip().split()
    if len(parts) != 5:
        raise ValueError(f"Expected 5-part cron expression, got: {cron!r}")
    minute, hour, day, month, day_of_week = parts
    return {
        "minute": minute,
        "hour": hour,
        "day": day,
        "month": month,
        "day_of_week": day_of_week,
    }


# ─── Scheduler jobs ───────────────────────────────────────────────────────────

async def _enqueue_fetch_jobs() -> None:
    """
    Enqueue one 'fetch_jobs' task per ATS source.
    Called by APScheduler on the cron schedule.
    """
    from app.db import get_conn
    from app.workers.base import enqueue_task

    sources = [
        "greenhouse", "lever", "ashby", "bamboohr",
        "icims", "paylocity", "workday",
        "adzuna", "remotive", "themuse",
    ]
    async with get_conn() as conn:
        for source in sources:
            await enqueue_task(conn, "fetch_jobs", {"source": source})

    logger.info("Scheduled: enqueued %d fetch_jobs tasks", len(sources))


async def _run_cleanup() -> None:
    """Daily cleanup job called by APScheduler at 03:00."""
    from app.workers.cleanup import cleanup_data

    try:
        await cleanup_data()
    except Exception as exc:
        logger.exception("Cleanup job failed: %s", exc)


async def _sync_company_lists() -> None:
    """
    Weekly company list sync: re-fetches Feashliaa GitHub JSON files and inserts
    any new slugs. Existing companies are never overwritten (ON CONFLICT DO NOTHING).
    """
    import httpx
    from app.db import get_conn

    FEASHLIAA_BASE = "https://raw.githubusercontent.com/Feashliaa/job-board-aggregator/main/data"
    SOURCES = {
        "greenhouse": f"{FEASHLIAA_BASE}/greenhouse_companies.json",
        "lever":      f"{FEASHLIAA_BASE}/lever_companies.json",
        "ashby":      f"{FEASHLIAA_BASE}/ashby_companies.json",
        "bamboohr":   f"{FEASHLIAA_BASE}/bamboohr_companies.json",
        "icims":      f"{FEASHLIAA_BASE}/icims_companies.json",
        "paylocity":  f"{FEASHLIAA_BASE}/paylocity_companies_clean.json",
        "workday":    f"{FEASHLIAA_BASE}/workday_companies.json",
    }

    total_inserted = 0
    async with httpx.AsyncClient(timeout=30) as client:
        for ats_type, url in SOURCES.items():
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                records = resp.json()
                if not isinstance(records, list):
                    continue
            except Exception as exc:
                logger.warning("sync_company_lists: failed to fetch %s: %s", ats_type, exc)
                continue

            async with get_conn() as conn:
                for record in records:
                    try:
                        slug = (
                            record.get("slug")
                            or record.get("ats_slug")
                            or record.get("guid")
                            or record.get("company")
                            or ""
                        )
                        if not slug:
                            continue
                        await conn.execute(
                            """
                            INSERT INTO companies (ats_type, ats_slug, priority)
                            VALUES (%s::ats_type, %s, 'warm')
                            ON CONFLICT (ats_type, ats_slug) DO NOTHING
                            """,
                            [ats_type, str(slug)],
                        )
                        total_inserted += 1
                    except Exception as exc:
                        logger.debug("sync_company_lists: row skip: %s", exc)

    logger.info("sync_company_lists complete: attempted %d rows", total_inserted)


# ─── Lifecycle ────────────────────────────────────────────────────────────────

def create_scheduler() -> AsyncIOScheduler:
    """Build and return an AsyncIOScheduler with all cron jobs configured."""
    scheduler = AsyncIOScheduler()

    # Fetch jobs cron (default: every 6 hours)
    cron_kwargs = _parse_cron(FETCH_CRON_SCHEDULE)
    scheduler.add_job(_enqueue_fetch_jobs, "cron", **cron_kwargs, id="fetch_jobs")

    # Daily cleanup at 03:00
    scheduler.add_job(_run_cleanup, "cron", hour=3, minute=0, id="cleanup_data")

    # Weekly company list sync — Sunday 02:00
    scheduler.add_job(
        _sync_company_lists, "cron",
        day_of_week="sun", hour=2, minute=0,
        id="sync_company_lists",
    )

    return scheduler
