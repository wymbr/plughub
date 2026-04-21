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
from fastapi.responses import JSONResponse

logger = logging.getLogger("plughub.analytics.api")

import redis.asyncio as aioredis

from .clickhouse import AnalyticsStore
from .config     import get_settings
from .consumer   import run_consumer
from .dashboard  import router as dashboard_router
from .reports    import router as reports_router
from .admin      import router as admin_router
from .sessions   import router as sessions_router


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
    try:
        await store.ensure_schema_async()
    except Exception as exc:
        logger.warning("ClickHouse schema setup failed — will retry on first insert: %s", exc)

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
        _run_consumer_safe(store),
        name="analytics-consumer",
    )

    yield

    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass

    await redis.aclose()


async def _run_consumer_safe(store: AnalyticsStore) -> None:
    """Wraps run_consumer with restart-on-failure (except on explicit shutdown)."""
    settings = get_settings()
    delay    = 5
    while True:
        try:
            await run_consumer(store)
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

app.include_router(dashboard_router)
app.include_router(reports_router)
app.include_router(admin_router)
app.include_router(sessions_router)


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
