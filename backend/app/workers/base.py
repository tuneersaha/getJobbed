"""
Base worker: poll loop, SKIP LOCKED task claiming, stuck-task watchdog.
All task-queue workers subclass BaseWorker and implement process_task().
Workers run as asyncio tasks started in the FastAPI lifespan.
"""

import os
import asyncio
import logging
from abc import ABC, abstractmethod
from datetime import timedelta

from psycopg.types.json import Jsonb

WORKER_STUCK_TASK_TIMEOUT_MINUTES = int(os.environ.get("WORKER_STUCK_TASK_TIMEOUT_MINUTES", "10"))
WORKER_POLL_INTERVAL = float(os.environ.get("WORKER_POLL_INTERVAL", "5"))
_LOCK_MINUTES = 10  # how long a claimed task is locked before becoming "stuck"


class BaseWorker(ABC):
    task_type: str  # must be set in subclass

    def __init__(self):
        self._log = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    async def run(self) -> None:
        self._log.info("Worker started: task_type=%s", self.task_type)
        while True:
            try:
                task = await self._claim_task()
                if task is None:
                    await asyncio.sleep(WORKER_POLL_INTERVAL)
                    continue
                task_id, payload, attempts = task
                try:
                    await self.process_task(task_id, payload, attempts)
                    await self._mark_done(task_id)
                except Exception as exc:
                    self._log.error(
                        "Task %s failed (attempt=%d): %s", task_id, attempts, exc, exc_info=True
                    )
                    await self._mark_failed(task_id, str(exc), attempts)
            except asyncio.CancelledError:
                self._log.info("Worker cancelled: task_type=%s", self.task_type)
                return
            except Exception as exc:
                self._log.exception("Poll loop error: %s", exc)
                await asyncio.sleep(WORKER_POLL_INTERVAL)

    @abstractmethod
    async def process_task(self, task_id: int, payload: dict, attempts: int) -> None:
        """Subclasses implement this. Raise on failure; base class handles marking failed."""

    async def _claim_task(self):
        """
        In one transaction:
        1. Reset stuck 'processing' tasks older than WORKER_STUCK_TASK_TIMEOUT_MINUTES back to 'pending'.
        2. Claim the next 'pending' task with FOR UPDATE SKIP LOCKED.
        Returns (task_id, payload_dict, attempts) or None.
        """
        from app.db import get_conn

        stuck_timeout = timedelta(minutes=WORKER_STUCK_TASK_TIMEOUT_MINUTES)
        lock_duration = timedelta(minutes=_LOCK_MINUTES)

        async with get_conn() as conn:
            await conn.execute(
                """
                UPDATE job_queue
                SET status = 'pending', updated_at = NOW()
                WHERE status = 'processing'
                  AND task_type = %s
                  AND locked_until < NOW() - %s
                """,
                [self.task_type, stuck_timeout],
            )
            cur = await conn.execute(
                """
                UPDATE job_queue
                SET status     = 'processing',
                    updated_at = NOW(),
                    attempts   = attempts + 1,
                    locked_until = NOW() + %s
                WHERE id = (
                    SELECT id FROM job_queue
                    WHERE status    = 'pending'
                      AND task_type = %s
                      AND run_at   <= NOW()
                    ORDER BY run_at, id
                    LIMIT 1
                    FOR UPDATE SKIP LOCKED
                )
                RETURNING id, payload, attempts
                """,
                [lock_duration, self.task_type],
            )
            row = await cur.fetchone()
            if row is None:
                return None
            # row[1] is the JSONB payload — psycopg3 deserialises JSONB to dict automatically
            return row[0], row[1], row[2]

    async def _mark_done(self, task_id: int) -> None:
        from app.db import get_conn

        async with get_conn() as conn:
            await conn.execute(
                "UPDATE job_queue SET status = 'done', updated_at = NOW() WHERE id = %s",
                [task_id],
            )

    async def _mark_failed(self, task_id: int, error: str, attempts: int) -> None:
        from app.db import get_conn

        # max_attempts matches job_queue.max_attempts default (3)
        max_attempts = 3
        async with get_conn() as conn:
            if attempts >= max_attempts:
                await conn.execute(
                    """
                    UPDATE job_queue
                    SET status = 'dead', last_error = %s, updated_at = NOW()
                    WHERE id = %s
                    """,
                    [error[:500], task_id],
                )
            else:
                # Backoff: 30s after 1st failure, 120s after 2nd
                delays = [30, 120]
                delay = delays[min(attempts - 1, len(delays) - 1)]
                await conn.execute(
                    """
                    UPDATE job_queue
                    SET status     = 'pending',
                        last_error = %s,
                        run_at     = NOW() + %s,
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    [error[:500], timedelta(seconds=delay), task_id],
                )


async def enqueue_task(
    conn,
    task_type: str,
    payload: dict,
    run_at_offset_seconds: int = 0,
) -> int:
    """
    Insert one task into job_queue. Must be called within an active psycopg3 connection.
    Returns the new task id (BIGSERIAL).
    """
    cur = await conn.execute(
        """
        INSERT INTO job_queue (task_type, payload, run_at)
        VALUES (%s, %s, NOW() + %s)
        RETURNING id
        """,
        [task_type, Jsonb(payload), timedelta(seconds=run_at_offset_seconds)],
    )
    row = await cur.fetchone()
    return row[0]
