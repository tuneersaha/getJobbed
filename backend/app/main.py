"""
GetJobbed — FastAPI application entry point.

Startup: opens DB pool, stores on app.state.pool, starts background workers.
Shutdown: closes DB pool, stops workers.

CORS: restricted to APP_BASE_URL only — no wildcard origins.
Auth: all /api/* routes require Google ID token (see app/auth.py).
/health: public, no auth required.
"""

import os
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from app.db import create_pool, set_pool, get_pool, get_conn

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger(__name__)


# ─── Lifespan ─────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting up — opening DB pool")
    pool = await create_pool()
    set_pool(pool)
    app.state.pool = pool
    logger.info("DB pool ready")

    yield  # application runs here

    # Shutdown
    logger.info("Shutting down — closing DB pool")
    await pool.close()
    logger.info("DB pool closed")


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


# ─── Routes ───────────────────────────────────────────────────────────────────

@app.get("/health", tags=["system"])
async def health():
    """
    Health check endpoint. Public — no auth required.

    Returns {"status": "ok"} when DB is reachable,
    {"status": "degraded"} with the error reason when it is not.

    Never exposes DATABASE_URL, credentials, or internal config.
    """
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

    overall = "ok" if db_status == "ok" else "degraded"

    body = {"status": overall, "db": db_status}
    if db_error:
        body["db_error"] = db_error

    return JSONResponse(content=body)
