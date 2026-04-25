"""
main.py
Config API FastAPI application.

Startup sequence:
  1. Create asyncpg connection pool
  2. Create Redis client
  3. Build ConfigStore (pool + cache)
  4. Ensure DB schema (CREATE TABLE IF NOT EXISTS)
  5. Serve requests

The seed script (plughub-config-seed) is run separately as a one-off job.
"""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import asyncpg
import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .cache          import ConfigCache
from .config         import get_settings
from .kafka_emitter  import ConfigKafkaEmitter
from .router         import router as config_router
from .store          import ConfigStore

logger = logging.getLogger("plughub.config.api")


async def _create_pool_with_retry(dsn: str, *, min_size: int, max_size: int,
                                   retries: int = 10, delay: float = 2.0) -> asyncpg.Pool:
    """Create asyncpg pool with exponential-backoff retry.

    pg_isready passes before PostgreSQL accepts authenticated connections,
    so the first attempt may fail even when the Docker healthcheck is green.
    """
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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()

    pool = await _create_pool_with_retry(
        settings.database_url,
        min_size=2,
        max_size=10,
    )

    redis = aioredis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
    )

    cache   = ConfigCache(redis, ttl=settings.cache_ttl_s)
    store   = ConfigStore(pool, cache)
    emitter = ConfigKafkaEmitter(settings.kafka_brokers_list)

    try:
        await store.setup()
    except Exception as exc:
        logger.warning("Schema setup failed — will retry on first request: %s", exc)

    await emitter.start()

    app.state.store   = store
    app.state.pool    = pool
    app.state.redis   = redis
    app.state.emitter = emitter

    yield

    await emitter.stop()
    await pool.close()
    await redis.aclose()


app = FastAPI(
    title       = "PlugHub Config API",
    version     = "1.0.0",
    description = "Two-level (global + per-tenant) configuration store for the PlugHub Platform",
    lifespan    = lifespan,
)

app.include_router(config_router)


@app.get("/v1/health")
async def health() -> JSONResponse:
    """Checks PostgreSQL + Redis connectivity."""
    pg_status    = "ok"
    redis_status = "ok"

    try:
        await app.state.pool.fetchval("SELECT 1")
    except Exception as exc:
        logger.warning("PG health check failed: %s", exc)
        pg_status = "error"

    try:
        await app.state.redis.ping()
    except Exception as exc:
        logger.warning("Redis health check failed: %s", exc)
        redis_status = "error"

    status = "ok" if pg_status == "ok" and redis_status == "ok" else "degraded"
    code   = 200 if status == "ok" else 503
    return JSONResponse(
        status_code=code,
        content={"status": status, "postgres": pg_status, "redis": redis_status},
    )


def run() -> None:
    import uvicorn
    settings = get_settings()
    uvicorn.run(
        "plughub_config_api.main:app",
        host    = settings.host,
        port    = settings.port,
        workers = settings.workers,
        reload  = False,
    )


if __name__ == "__main__":
    run()
