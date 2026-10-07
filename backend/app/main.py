"""
GetJobbed — FastAPI application entry point.

Startup: opens DB pool, stores on app.state.pool, starts background workers.
Shutdown: closes DB pool, stops workers.

CORS: restricted to APP_BASE_URL only — no wildcard origins.
Auth: all /api/* routes require Google ID token (see app/auth.py).
/health: public, no auth required.
"""

import asyncio
import os
import logging
import logging.handlers
import pathlib
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from app.db import create_pool, set_pool, get_pool, get_conn

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT)

# Also write to a rotating file so logs can be inspected on the host
_log_dir = pathlib.Path(os.environ.get("LOG_DIR", "/app/logs"))
_log_dir.mkdir(parents=True, exist_ok=True)
_file_handler = logging.handlers.RotatingFileHandler(
    _log_dir / "backend.log",
    maxBytes=10 * 1024 * 1024,  # 10 MB
    backupCount=3,
    encoding="utf-8",
)
_file_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
logging.getLogger().addHandler(_file_handler)
logger = logging.getLogger(__name__)

# Number of concurrent JobFetchWorker asyncio tasks
_FETCH_WORKER_COUNT = int(os.environ.get("FETCH_WORKER_COUNT", "2"))


# ─── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────────────────
    logger.info("Starting up — opening DB pool")
    pool = await create_pool()
    set_pool(pool)
    app.state.pool = pool
    logger.info("DB pool ready")

    # Load embedding model (CPU-bound, do in thread to avoid blocking startup)
    # Non-fatal: app starts degraded if sentence_transformers not installed.
    import asyncio as _asyncio
    from app import embeddings as _emb
    try:
        await _asyncio.to_thread(_emb.load_model)
        logger.info("Embedding model loaded")
    except Exception as exc:
        logger.warning("Embedding model NOT loaded (%s) — scoring/resume disabled", type(exc).__name__)

    # Start background workers
    from app.workers.fetch import JobFetchWorker
    from app.workers.scoring import ScoringWorker
    from app.workers.tailoring import TailoringWorker

    worker_tasks = [
        asyncio.create_task(
            JobFetchWorker().run(), name=f"fetch-worker-{i}"
        )
        for i in range(_FETCH_WORKER_COUNT)
    ]
    worker_tasks += [
        asyncio.create_task(ScoringWorker().run(),   name="scoring-worker"),
        asyncio.create_task(TailoringWorker().run(), name="tailoring-worker"),
    ]
    logger.info("Started %d fetch worker(s) + scoring + tailoring workers", _FETCH_WORKER_COUNT)

    # Start APScheduler
    from app.scheduler import create_scheduler

    scheduler = create_scheduler()
    scheduler.start()
    logger.info("Scheduler started")

    yield  # application runs here

    # ── Shutdown ─────────────────────────────────────────────────────────────
    logger.info("Shutting down — stopping scheduler")
    scheduler.shutdown(wait=False)

    logger.info("Cancelling worker tasks")
    for task in worker_tasks:
        task.cancel()
    if worker_tasks:
        await asyncio.gather(*worker_tasks, return_exceptions=True)

    logger.info("Closing DB pool")
    await pool.close()
    logger.info("Shutdown complete")


# ─── App factory ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="GetJobbed API",
    version="0.1.0",
    docs_url="/docs",
    redoc_url=None,
    lifespan=lifespan,
)

# CORS: only the frontend origin is allowed — no wildcard
app_base_url = os.environ.get("APP_BASE_URL", "http://localhost:3000")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[app_base_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Exception handlers ───────────────────────────────────────────────────────

@app.exception_handler(RequestValidationError)
async def validation_error_handler(request, exc):
    return JSONResponse(
        status_code=422,
        content={
            "code": "VALIDATION_ERROR",
            "message": "Request validation failed",
            "detail": exc.errors(),
        },
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request, exc):
    """Flatten HTTPException detail dict to top-level RFC 7807 shape."""
    detail = exc.detail
    if isinstance(detail, dict):
        content = detail
    else:
        content = {"code": "ERROR", "message": str(detail)}
    headers = getattr(exc, "headers", None) or {}
    return JSONResponse(status_code=exc.status_code, content=content, headers=headers)


# ─── Routers ──────────────────────────────────────────────────────────────────

from app.routers import resume as resume_router
from app.routers import jobs as jobs_router
from app.routers import matches as matches_router
from app.routers import applied as applied_router
from app.routers import profile as profile_router
from app.routers import fetch as fetch_router
from app.routers import stats as stats_router

app.include_router(resume_router.router)
app.include_router(jobs_router.router)
app.include_router(matches_router.router)
app.include_router(applied_router.router)
app.include_router(profile_router.router)
app.include_router(fetch_router.router)
app.include_router(stats_router.router)


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/health", tags=["system"])
async def health():
    """
    Health check endpoint. Public — no auth required.

    Returns {"status": "ok"} when DB is reachable,
    {"status": "degraded"} with the error reason when it is not.

    Never exposes DATABASE_URL, credentials, or internal config.
    """
    from app import embeddings as _emb

    try:
        pool = get_pool()
        async with get_conn(pool) as conn:
            await conn.execute("SELECT 1")
        db_status = "ok"
        db_error = None
    except Exception as exc:
        logger.warning("Health check DB error: %s", type(exc).__name__)
        db_status = "error"
        db_error = type(exc).__name__  # class name only, no message (may contain creds)

    embedding_status = "loaded" if _emb.is_loaded() else "not_loaded"
    overall = "ok" if db_status == "ok" else "degraded"

    # Queue depth — best-effort, skip on DB error
    queue_depth = 0
    if db_status == "ok":
        try:
            pool = get_pool()
            async with get_conn(pool) as conn:
                qc = await conn.execute(
                    "SELECT COUNT(*) FROM job_queue WHERE status = 'pending'"
                )
                qr = await qc.fetchone()
                queue_depth = int(qr[0])
        except Exception:
            pass

    body = {
        "status": overall,
        "db": db_status,
        "embedding_model": embedding_status,
        "queue_depth": queue_depth,
    }
    if db_error:
        body["db_error"] = db_error

    return JSONResponse(content=body)
