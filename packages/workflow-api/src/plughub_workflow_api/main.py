"""
main.py
Workflow API FastAPI application.

Startup sequence:
  1. Create asyncpg connection pool (with retry)
  2. Ensure DB schema (CREATE TABLE IF NOT EXISTS)
  3. Create AIOKafka producer (optional — skipped if kafka_enabled=False)
  4. Start background timeout scanner task
  5. Serve requests
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import asyncpg
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .config import get_settings
from .db import ensure_schema
from .router import router as workflow_router
from .timeout_job import run_timeout_scanner

logger = logging.getLogger("plughub.workflow.api")


async def _create_pool_with_retry(
    dsn: str,
    *,
    min_size: int,
    max_size: int,
    retries: int = 10,
    delay:   float = 2.0,
) -> asyncpg.Pool:
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return await asyncpg.create_pool(dsn, min_size=min_size, max_size=max_size)
        except Exception as exc:
            last_exc = exc
            wait = delay * attempt
            logger.warning(
                "asyncpg pool creation failed (attempt %d/%d) — retrying in %.1fs: %s",
                attempt, retries, wait, exc,
            )
            await asyncio.sleep(wait)
    raise RuntimeError(f"Could not connect to PostgreSQL after {retries} attempts") from last_exc


async def _create_kafka_producer(brokers: str):
    """Creates an AIOKafka producer. Returns None if kafka is disabled or fails."""
    try:
        from aiokafka import AIOKafkaProducer
        producer = AIOKafkaProducer(bootstrap_servers=brokers)
        await producer.start()
        logger.info("Kafka producer started (brokers=%s)", brokers)
        return producer
    except Exception as exc:
        logger.warning("Kafka producer failed to start — events will be skipped: %s", exc)
        return None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()

    # PostgreSQL
    pool = await _create_pool_with_retry(
        settings.database_url,
        min_size=2,
        max_size=10,
    )
    try:
        await ensure_schema(pool)
    except Exception as exc:
        logger.warning("Schema setup failed — will retry on first request: %s", exc)

    # Kafka
    producer = None
    if settings.kafka_enabled:
        producer = await _create_kafka_producer(settings.kafka_brokers)

    app.state.pool     = pool
    app.state.settings = settings
    app.state.producer = producer

    # Background timeout scanner
    scanner_task = asyncio.create_task(run_timeout_scanner(app))

    yield

    # Shutdown
    scanner_task.cancel()
    try:
        await scanner_task
    except asyncio.CancelledError:
        pass

    if producer:
        await producer.stop()
    await pool.close()


app = FastAPI(
    title       = "PlugHub Workflow API",
    version     = "1.0.0",
    description = (
        "Workflow Automation API for the PlugHub Platform (Arc 4). "
        "Manages WorkflowInstance lifecycle — trigger, suspend, resume, "
        "complete, fail, cancel — and publishes workflow.* events to Kafka."
    ),
    lifespan = lifespan,
)

app.include_router(workflow_router)


@app.get("/v1/health")
async def health() -> JSONResponse:
    """Checks PostgreSQL connectivity."""
    pg_status = "ok"
    try:
        await app.state.pool.fetchval("SELECT 1")
    except Exception as exc:
        logger.warning("PG health check failed: %s", exc)
        pg_status = "error"

    status = "ok" if pg_status == "ok" else "degraded"
    code   = 200 if status == "ok" else 503
    return JSONResponse(
        status_code=code,
        content={"status": status, "postgres": pg_status},
    )


def run() -> None:
    import uvicorn
    settings = get_settings()
    uvicorn.run(
        "plughub_workflow_api.main:app",
        host    = settings.host,
        port    = settings.port,
        workers = settings.workers,
        reload  = False,
    )


if __name__ == "__main__":
    run()
