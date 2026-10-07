"""
JobFetchWorker — processes 'fetch_jobs' tasks from job_queue.
One task per ATS source type (greenhouse, lever, … themuse).
For company-based sources: queries companies by tier, fetches concurrently via asyncio semaphore.
For role-based sources (adzuna, remotive, themuse): reads user desired_roles and calls API.
Upserts new jobs, enqueues score_job tasks for each new row, updates audit log.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone

import httpx

from app.fetchers.base import NormalizedJob, validate_apply_url
from app.workers.base import BaseWorker, enqueue_task

logger = logging.getLogger(__name__)

FETCH_CONCURRENCY = int(os.environ.get("FETCH_CONCURRENCY", "20"))
# Paylocity aggressively rate-limits; cap its concurrency regardless of FETCH_CONCURRENCY
PAYLOCITY_CONCURRENCY = int(os.environ.get("PAYLOCITY_CONCURRENCY", "3"))
COLD_SAMPLE_SIZE  = int(os.environ.get("COLD_SAMPLE_SIZE", "100"))
DEAD_PROBE_SIZE   = int(os.environ.get("DEAD_PROBE_BATCH_SIZE", "20"))
EMPTY_DEAD_THRESHOLD = 5  # consecutive empty runs → mark dead

# Sources that have company slugs in the companies table
COMPANY_SOURCES = {"greenhouse", "lever", "ashby", "bamboohr", "icims", "paylocity", "workday"}
# Sources queried by role keyword (no company slugs)
ROLE_SOURCES = {"adzuna", "remotive", "themuse"}


def _get_fetcher(source: str):
    """Return the fetcher module for this source."""
    if source == "greenhouse":
        from app.fetchers import greenhouse
        return greenhouse
    if source == "lever":
        from app.fetchers import lever
        return lever
    if source == "ashby":
        from app.fetchers import ashby
        return ashby
    if source == "bamboohr":
        from app.fetchers import bamboohr
        return bamboohr
    if source == "icims":
        from app.fetchers import icims
        return icims
    if source == "paylocity":
        from app.fetchers import paylocity
        return paylocity
    if source == "workday":
        from app.fetchers import workday
        return workday
    if source == "adzuna":
        from app.fetchers import adzuna
        return adzuna
    if source == "remotive":
        from app.fetchers import remotive
        return remotive
    if source == "themuse":
        from app.fetchers import themuse
        return themuse
    raise ValueError(f"Unknown source: {source!r}")


class JobFetchWorker(BaseWorker):
    task_type = "fetch_jobs"

    async def process_task(self, task_id: int, payload: dict, attempts: int) -> None:
        source = payload.get("source")
        if not source:
            raise ValueError("fetch_jobs payload missing 'source'")

        run_id = await self._start_run(source)
        jobs_found = 0
        jobs_new = 0
        discovered_slugs: list[tuple[str, str]] = []

        try:
            limits = httpx.Limits(max_connections=FETCH_CONCURRENCY + 5, max_keepalive_connections=20)
            async with httpx.AsyncClient(
                follow_redirects=True,
                limits=limits,
                headers={"User-Agent": "GetJobbed/1.0 (job aggregator)"},
            ) as client:
                if source in COMPANY_SOURCES:
                    jobs_found, jobs_new, discovered_slugs = await self._run_company_source(source, client)
                elif source in ROLE_SOURCES:
                    jobs_found, jobs_new, discovered_slugs = await self._run_role_source(source, client)
                else:
                    raise ValueError(f"Unknown source: {source!r}")

            # Upsert any newly discovered ATS slugs (self-expanding slug discovery)
            if discovered_slugs:
                await self._upsert_discovered_slugs(discovered_slugs)

            await self._complete_run(run_id, jobs_found, jobs_new)
            logger.info(
                "fetch/%s done: found=%d new=%d discovered_slugs=%d",
                source, jobs_found, jobs_new, len(discovered_slugs),
            )
        except Exception as exc:
            await self._fail_run(run_id, str(exc))
            raise

    # ─── Company-based sources ────────────────────────────────────────────────

    async def _run_company_source(
        self,
        source: str,
        client: httpx.AsyncClient,
    ) -> tuple[int, int, list[tuple[str, str]]]:
        companies = await self._get_companies(source)
        if not companies:
            logger.info("fetch/%s: no companies to fetch this cycle", source)
            return 0, 0, []

        concurrency = PAYLOCITY_CONCURRENCY if source == "paylocity" else FETCH_CONCURRENCY
        sem = asyncio.Semaphore(concurrency)
        fetcher = _get_fetcher(source)

        async def fetch_one(company):
            async with sem:
                return await self._fetch_and_upsert(source, company, client, fetcher)

        results = await asyncio.gather(
            *[fetch_one(c) for c in companies],
            return_exceptions=True,
        )

        total_found = 0
        total_new = 0
        all_discovered: list[tuple[str, str]] = []

        for r in results:
            if isinstance(r, Exception):
                pass  # already logged in _fetch_and_upsert
            else:
                found, new, discovered = r
                total_found += found
                total_new += new
                all_discovered.extend(discovered)

        return total_found, total_new, all_discovered

    async def _get_companies(self, source: str) -> list[dict]:
        """
        Return companies to fetch this cycle based on tier logic:
        - hot:  all (always fetch)
        - warm: not fetched in last 20 hours
        - cold: random sample of COLD_SAMPLE_SIZE not fetched in 20h
        - dead: those whose dead_retry_at <= NOW() (resurrection probe)
        """
        from app.db import get_conn

        async with get_conn() as conn:
            cur = await conn.execute(
                """
                (
                    SELECT id, ats_slug, COALESCE(name, ats_slug) AS company_name, priority
                    FROM companies
                    WHERE ats_type = %s::ats_type AND priority = 'hot'
                )
                UNION ALL
                (
                    SELECT id, ats_slug, COALESCE(name, ats_slug) AS company_name, priority
                    FROM companies
                    WHERE ats_type = %s::ats_type
                      AND priority = 'warm'
                      AND (last_fetched_at IS NULL OR last_fetched_at < NOW() - INTERVAL '20 hours')
                )
                UNION ALL
                (
                    SELECT id, ats_slug, COALESCE(name, ats_slug) AS company_name, priority
                    FROM companies
                    WHERE ats_type = %s::ats_type
                      AND priority = 'cold'
                      AND (last_fetched_at IS NULL OR last_fetched_at < NOW() - INTERVAL '20 hours')
                    ORDER BY RANDOM()
                    LIMIT %s
                )
                UNION ALL
                (
                    SELECT id, ats_slug, COALESCE(name, ats_slug) AS company_name, priority
                    FROM companies
                    WHERE ats_type = %s::ats_type
                      AND priority = 'dead'
                      AND dead_retry_at IS NOT NULL
                      AND dead_retry_at <= NOW()
                    LIMIT %s
                )
                """,
                [source, source, source, COLD_SAMPLE_SIZE, source, DEAD_PROBE_SIZE],
            )
            rows = await cur.fetchall()

        return [
            {"id": str(r[0]), "ats_slug": r[1], "company_name": r[2], "priority": r[3]}
            for r in rows
        ]

    async def _fetch_and_upsert(
        self,
        source: str,
        company: dict,
        client: httpx.AsyncClient,
        fetcher,
    ) -> tuple[int, int, list[tuple[str, str]]]:
        """
        Fetch jobs for one company, upsert into DB, enqueue score_job for new rows.
        Returns (jobs_found, jobs_new, discovered_slugs).
        """
        company_id = company["id"]
        ats_slug = company["ats_slug"]
        company_name = company["company_name"]
        is_dead_probe = company["priority"] == "dead"

        try:
            jobs: list[NormalizedJob] = await fetcher.fetch(client, ats_slug, company_id, company_name)
        except ValueError as exc:
            # ValueError = 404 or invalid slug → mark dead
            logger.warning("fetch/%s %s: %s (marking dead)", source, ats_slug, exc)
            await self._mark_company_dead(company_id)
            return 0, 0, []
        except Exception as exc:
            logger.error("fetch/%s %s: %s", source, ats_slug, exc)
            # Update last_fetched_at even on failure so we don't hammer a broken endpoint
            await self._update_company_fetched(company_id, jobs_found=False)
            return 0, 0, []

        jobs_found = len(jobs)
        jobs_new = 0
        discovered: list[tuple[str, str]] = []

        if jobs_found == 0:
            await self._update_company_fetched(company_id, jobs_found=False)
            return 0, 0, []

        # Upsert jobs + enqueue score_job for new rows
        from app.db import get_conn

        async with get_conn() as conn:
            for job in jobs:
                if not validate_apply_url(job.apply_url):
                    continue
                new_id = await self._upsert_job(conn, job)
                if new_id is not None:
                    jobs_new += 1
                    await enqueue_task(conn, "score_job", {"job_id": new_id})

                from app.fetchers.base import extract_ats_slug_from_url
                found = extract_ats_slug_from_url(job.apply_url)
                if found:
                    discovered.append(found)

        await self._update_company_fetched(company_id, jobs_found=True, was_dead=is_dead_probe)
        return jobs_found, jobs_new, discovered

    # ─── Role-based sources ───────────────────────────────────────────────────

    async def _run_role_source(
        self,
        source: str,
        client: httpx.AsyncClient,
    ) -> tuple[int, int, list[tuple[str, str]]]:
        roles = await self._get_desired_roles()
        if not roles:
            logger.info("fetch/%s: no desired_roles configured — skipping", source)
            return 0, 0, []

        fetcher = _get_fetcher(source)
        discovered: list[tuple[str, str]] = []

        if source in ("themuse", "remotive"):
            jobs = await fetcher.fetch(client)
            disc: list[tuple[str, str]] = []
        else:
            jobs, disc = await fetcher.fetch(client, roles)
            discovered.extend(disc)

        jobs_found = len(jobs)
        jobs_new = 0

        from app.db import get_conn

        async with get_conn() as conn:
            for job in jobs:
                if not validate_apply_url(job.apply_url):
                    continue
                new_id = await self._upsert_job(conn, job)
                if new_id is not None:
                    jobs_new += 1
                    await enqueue_task(conn, "score_job", {"job_id": new_id})

        return jobs_found, jobs_new, discovered

    async def _get_desired_roles(self) -> list[str]:
        from app.db import get_conn

        async with get_conn() as conn:
            cur = await conn.execute(
                "SELECT desired_roles FROM user_profiles WHERE array_length(desired_roles, 1) > 0 LIMIT 1"
            )
            row = await cur.fetchone()
            return list(row[0]) if row else []

    # ─── DB helpers ───────────────────────────────────────────────────────────

    async def _upsert_job(self, conn, job: NormalizedJob) -> str | None:
        """
        UPSERT one job. Returns the job's UUID string if newly inserted, None if it already existed.
        """
        cur = await conn.execute(
            """
            INSERT INTO jobs (
                external_id, source, company_id, company_name,
                title, description, location, work_type,
                apply_url, experience_min, experience_max,
                requires_foreign_citizenship, posted_at
            )
            VALUES (
                %s, %s::job_source, %s, %s,
                %s, %s, %s, %s::work_arrangement,
                %s, %s, %s,
                %s::citizenship_req, %s
            )
            ON CONFLICT (source, external_id) DO NOTHING
            RETURNING id
            """,
            [
                job.external_id,
                job.source,
                job.company_id,
                job.company_name,
                job.title,
                job.description,
                job.location,
                job.work_type,
                job.apply_url,
                job.experience_min,
                job.experience_max,
                job.requires_foreign_citizenship,
                job.posted_at,
            ],
        )
        row = await cur.fetchone()
        return str(row[0]) if row else None

    async def _update_company_fetched(
        self,
        company_id: str,
        jobs_found: bool,
        was_dead: bool = False,
    ) -> None:
        from app.db import get_conn

        async with get_conn() as conn:
            if jobs_found:
                await conn.execute(
                    """
                    UPDATE companies
                    SET last_fetched_at        = NOW(),
                        last_job_found_at      = NOW(),
                        consecutive_empty_runs = 0,
                        priority               = CASE WHEN priority = 'dead' THEN 'warm'
                                                      WHEN priority = 'cold' THEN 'warm'
                                                      ELSE 'hot' END,
                        dead_since             = NULL,
                        dead_probe_count       = 0,
                        dead_retry_at          = NULL
                    WHERE id = %s
                    """,
                    [company_id],
                )
            else:
                await conn.execute(
                    """
                    UPDATE companies
                    SET last_fetched_at        = NOW(),
                        consecutive_empty_runs = consecutive_empty_runs + 1
                    WHERE id = %s
                    """,
                    [company_id],
                )
                # Check if we've hit the dead threshold
                cur = await conn.execute(
                    "SELECT consecutive_empty_runs FROM companies WHERE id = %s",
                    [company_id],
                )
                row = await cur.fetchone()
                if row and row[0] >= EMPTY_DEAD_THRESHOLD:
                    await self._mark_company_dead(company_id, conn=conn)

    async def _mark_company_dead(self, company_id: str, conn=None) -> None:
        from app.db import get_conn
        from app.fetchers.base import SLUG_RE

        _PROBE_SCHEDULE = [20, 30, 45, 45, 45, 45]  # days

        async def _do(c):
            await c.execute(
                """
                UPDATE companies
                SET priority         = 'dead',
                    dead_since       = COALESCE(dead_since, NOW()),
                    dead_probe_count = dead_probe_count + 1,
                    dead_retry_at    = NOW() + (
                        CASE WHEN dead_probe_count < 6
                             THEN ARRAY[20,30,45,45,45,45][dead_probe_count + 1]
                             ELSE 120
                        END * INTERVAL '1 day'
                    ),
                    last_fetched_at  = NOW()
                WHERE id = %s
                """,
                [company_id],
            )

        if conn:
            await _do(conn)
        else:
            async with get_conn() as conn2:
                await _do(conn2)

    async def _upsert_discovered_slugs(self, discovered: list[tuple[str, str]]) -> None:
        """Insert newly discovered ATS slugs as 'cold' companies (ON CONFLICT DO NOTHING)."""
        from app.db import get_conn

        async with get_conn() as conn:
            for ats_type, slug in discovered:
                try:
                    await conn.execute(
                        """
                        INSERT INTO companies (ats_type, ats_slug, priority)
                        VALUES (%s::ats_type, %s, 'cold')
                        ON CONFLICT (ats_type, ats_slug) DO NOTHING
                        """,
                        [ats_type, slug],
                    )
                except Exception as exc:
                    logger.warning("slug discovery upsert failed: %s/%s: %s", ats_type, slug, exc)

    # ─── Fetch run audit log ──────────────────────────────────────────────────

    async def _start_run(self, source: str) -> str:
        from app.db import get_conn

        async with get_conn() as conn:
            cur = await conn.execute(
                "INSERT INTO fetch_runs (source, status) VALUES (%s, 'running') RETURNING id",
                [source],
            )
            row = await cur.fetchone()
            return str(row[0])

    async def _complete_run(self, run_id: str, jobs_found: int, jobs_new: int) -> None:
        from app.db import get_conn

        async with get_conn() as conn:
            await conn.execute(
                """
                UPDATE fetch_runs
                SET status = 'completed', completed_at = NOW(), jobs_found = %s, jobs_new = %s
                WHERE id = %s
                """,
                [jobs_found, jobs_new, run_id],
            )

    async def _fail_run(self, run_id: str, error: str) -> None:
        from app.db import get_conn

        async with get_conn() as conn:
            await conn.execute(
                """
                UPDATE fetch_runs
                SET status = 'failed', completed_at = NOW(), error = %s
                WHERE id = %s
                """,
                [error[:500], run_id],
            )
