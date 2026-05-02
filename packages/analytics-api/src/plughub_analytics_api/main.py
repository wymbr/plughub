"""
main.py
Analytics API FastAPI application.

Endpoints (Phase 1 — this task):
  GET /v1/health   — checks ClickHouse + Kafka connectivity

Endpoints (Phase 2 — Task 3):
  GET /dashboard/operational  — SSE Redis pool snapshots
  GET /dashboard/metrics      — last 24h from ClickHouse
  GET /dashboard/sentiment    — pool sentiment_live

Endpoints (Phase 3 — Task 4):
  GET /reports/sessions
  GET /reports/agents
  GET /reports/quality
  GET /reports/usage

Kafka consumer runs as a background asyncio task started in the lifespan.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

logger = logging.getLogger("plughub.analytics.api")

import redis.asyncio as aioredis

from .clickhouse      import AnalyticsStore
from .config          import get_settings
from .consumer        import run_consumer
from .dashboard       import router as dashboard_router
from .performance_job import run_performance_job_loop
from .reports         import router as reports_router
from .admin           import router as admin_router
from .sessions        import router as sessions_router
from .supervisor      import router as supervisor_router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()

    # ── ClickHouse ────────────────────────────────────────────────────────────
    store = AnalyticsStore(
        host     = settings.clickhouse_host,
        port     = settings.clickhouse_port,
        user     = settings.clickhouse_user,
        password = settings.clickhouse_password,
        database = settings.clickhouse_database,
    )
    # Retry until ClickHouse is ready — it may still be initialising when we start.
    # Uses exponential backoff capped at 30 s; gives up after 10 minutes total.
    _ch_delay = 2.0
    _ch_deadline = asyncio.get_event_loop().time() + 600
    while True:
        try:
            await store.ensure_schema_async()
            logger.info("ClickHouse schema ready")
            break
        except Exception as exc:
            if asyncio.get_event_loop().time() >= _ch_deadline:
                logger.error("ClickHouse schema setup failed after 10 min — giving up: %s", exc)
                break
            logger.warning("ClickHouse not ready yet (%s) — retrying in %.0fs", exc, _ch_delay)
            await asyncio.sleep(_ch_delay)
            _ch_delay = min(_ch_delay * 1.5, 30.0)

    app.state.store = store

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis = aioredis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
    )
    app.state.redis = redis

    # ── Kafka consumer ────────────────────────────────────────────────────────
    consumer_task = asyncio.create_task(
        _run_consumer_safe(store, redis),
        name="analytics-consumer",
    )

    # ── Performance sync (Arc 7d) ─────────────────────────────────────────────
    # Reads v_agent_performance from ClickHouse every 5 min and writes
    # performance scores to Redis for consumption by the routing-engine.
    perf_task = asyncio.create_task(
        run_performance_job_loop(store, redis),
        name="performance-sync",
    )

    yield

    consumer_task.cancel()
    perf_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass
    try:
        await perf_task
    except asyncio.CancelledError:
        pass

    await redis.aclose()


async def _run_consumer_safe(store: AnalyticsStore, redis: object | None = None) -> None:
    """Wraps run_consumer with restart-on-failure (except on explicit shutdown)."""
    settings = get_settings()
    delay    = 5
    while True:
        try:
            await run_consumer(store, redis)
            break  # clean exit (shutdown signal)
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error(
                "Consumer crashed — restarting in %ds: %s", delay, exc, exc_info=True
            )
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)


app = FastAPI(
    title="PlugHub Analytics API",
    version="1.0.0",
    description="Kafka→ClickHouse consumer + REST analytics for PlugHub Platform",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(dashboard_router)
app.include_router(reports_router)
app.include_router(admin_router)
app.include_router(sessions_router)
app.include_router(supervisor_router)


# ─── Health ───────────────────────────────────────────────────────────────────

@app.get("/v1/health")
async def health() -> JSONResponse:
    """
    Checks ClickHouse connectivity.
    Returns 200 when healthy, 503 when degraded.
    """
    store: AnalyticsStore = app.state.store
    ch_status = "ok"
    try:
        await asyncio.to_thread(store._client.command, "SELECT 1")
    except Exception as exc:
        logger.warning("ClickHouse health check failed: %s", exc)
        ch_status = "error"

    status  = "ok" if ch_status == "ok" else "degraded"
    code    = 200 if status == "ok" else 503

    return JSONResponse(
        status_code=code,
        content={"status": status, "clickhouse": ch_status},
    )


@app.post("/admin/performance-sync")
async def trigger_performance_sync() -> JSONResponse:
    """
    Arc 7d — Manual trigger for the performance score sync.
    Runs immediately (blocking the request) and returns the sync result.
    Useful for testing or forcing a refresh after data backfill.
    """
    from .performance_job import run_performance_sync
    store: AnalyticsStore = app.state.store
    redis = app.state.redis
    result = await run_performance_sync(store, redis)
    return JSONResponse(status_code=200, content=result)


def run() -> None:
    import uvicorn
    settings = get_settings()
    uvicorn.run(
        "plughub_analytics_api.main:app",
        host    = settings.host,
        port    = settings.port,
        workers = settings.workers,
        reload  = False,
    )


if __name__ == "__main__":
    run()
