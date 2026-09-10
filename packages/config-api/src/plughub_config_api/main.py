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
import os
import sys
import re
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import asyncpg
import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .cache          import ConfigCache
from .config         import get_settings
from .db             import ensure_schema
from .kafka_emitter  import ConfigKafkaEmitter
from .router         import router as config_router
from .store          import ConfigStore


def _mask_dsn(dsn: str) -> str:
    """Replace password in DSN with *** for safe logging."""
    return re.sub(r"://([^:]+):[^@]+@", r"://\1:***@", dsn)

# ── logging: `plughub.*` em INFO chega ao stdout ─────────────────────────────
# ⚠️ Sem isto o serviço descarta TODO `logger.info` do repositório: o CMD é
# `uvicorn …:app`, que configura só os loggers `uvicorn*`, e o root fica no default
# WARNING — `logger.warning` ainda sai (handler de último recurso do Python), o que
# faz o defeito parecer log normal. Catalogado em `TODO.md` § "Seis serviços rodam SEM
# logging configurado" (2026-08-07) e medido de novo na AUT-43: são **sete**, e a lista
# de seis errava dos dois lados — 2 falsos positivos (`scheduler-api`/`mailing-api` já
# configuravam por este mesmo mecanismo) e 3 ausentes (`workflow-api`, `quality-ingest`,
# `quality-export`, dados como sadios por serem console-script). Aqui a promessa que
# estava sendo quebrada era a da AUT-03: *"o caminho vazio não ficou mudo"*
# (`plughub_authz.resolve_scope`, em INFO).
#
# Handler no logger `plughub`, e não `basicConfig`: este ligaria INFO na RAIZ e traria
# junto asyncpg/aiokafka/httpx/clickhouse. Nível por `PLUGHUB_LOG_LEVEL`.
# Gate: `infra/test/probe_service_log_info_reaches_stdout.sh`.
_plughub_logger = logging.getLogger("plughub")
if not _plughub_logger.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s — %(message)s"))
    _plughub_logger.addHandler(_h)
_plughub_logger.setLevel(os.getenv("PLUGHUB_LOG_LEVEL", "INFO").upper())
_plughub_logger.propagate = False
_plughub_logger.info(
    "logging configurado: `plughub.*` em %s (PLUGHUB_LOG_LEVEL). Sem esta linha o "
    "servico descarta todo INFO do repositorio — ver AUT-43.",
    logging.getLevelName(_plughub_logger.level),
)

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

    logger.info("Config API starting — database: %s", _mask_dsn(settings.database_url))

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

    # Ensure DB schema with retry — asyncpg pool creation succeeds before
    # PostgreSQL fully accepts DDL, so one immediate failure is expected.
    for _attempt in range(1, 11):
        try:
            await store.setup()
            break
        except Exception as exc:
            if _attempt == 10:
                logger.error("Schema setup failed after 10 attempts — aborting: %s", exc)
                raise
            _wait = 2.0 * _attempt
            logger.warning(
                "Schema setup failed (attempt %d/10) — retrying in %.1fs: %s",
                _attempt, _wait, exc,
            )
            await asyncio.sleep(_wait)

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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(config_router)


@app.get("/v1/health")
async def health() -> JSONResponse:
    """Checks PostgreSQL + Redis connectivity."""
    pg_status    = "ok"
    redis_status = "ok"

    try:
        pool = app.state.pool
        # Log which database we're actually connected to (diagnostics).
        db_name = await pool.fetchval("SELECT current_database()")
        logger.debug("Health check: connected to database '%s'", db_name)
        # Verify both connectivity AND that the schema is ready.
        await pool.fetchval(
            "SELECT COUNT(*) FROM public.platform_config LIMIT 0"
        )
    except asyncpg.exceptions.UndefinedTableError:
        # Table missing — attempt self-healing before declaring unhealthy.
        logger.warning(
            "platform_config table missing in health check — attempting to recreate"
        )
        try:
            await ensure_schema(app.state.pool)
            logger.info("platform_config table recreated successfully in health check")
            # Verify once more after recreation.
            await app.state.pool.fetchval(
                "SELECT COUNT(*) FROM public.platform_config LIMIT 0"
            )
        except Exception as recreate_exc:
            logger.error(
                "Self-healing failed: could not recreate platform_config: %s",
                recreate_exc,
            )
            pg_status = "error"
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
