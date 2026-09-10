"""
main.py
Dialog API FastAPI application — generic scripted-dialog form store.

Startup:
  1. Create asyncpg pool (with retry)
  2. Ensure DB schema (CREATE TABLE IF NOT EXISTS)
  3. Serve requests
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import asyncpg
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .config import get_settings
from .db import ensure_schema
from .router import router as dialog_router

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

logger = logging.getLogger("plughub.dialog.api")


async def _create_pool_with_retry(
    dsn: str,
    *,
    min_size: int,
    max_size: int,
    retries: int = 10,
    delay: float = 2.0,
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


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()

    pool = await _create_pool_with_retry(settings.database_url, min_size=2, max_size=10)

    try:
        await ensure_schema(pool)
    except Exception as exc:
        logger.warning("Schema setup failed — will retry on first request: %s", exc)

    app.state.pool     = pool
    app.state.settings = settings

    yield

    await pool.close()


app = FastAPI(
    title       = "PlugHub Dialog API",
    version     = "1.0.0",
    description = (
        "Generic scripted-dialog form store (survey + OTP). Stores versioned "
        "(draft/published), i18n DialogForm JSON served to the Tier-3 dialog-runner "
        "via the generic form_get tool."
    ),
    lifespan    = lifespan,
)

app.include_router(dialog_router)


@app.get("/v1/health")
async def health() -> JSONResponse:
    pg_status = "ok"
    try:
        await app.state.pool.fetchval("SELECT 1")
    except Exception as exc:
        logger.warning("PG health check failed: %s", exc)
        pg_status = "error"

    status = "ok" if pg_status == "ok" else "degraded"
    code   = 200 if status == "ok" else 503
    return JSONResponse(status_code=code, content={"status": status, "postgres": pg_status})


def run() -> None:
    import uvicorn
    settings = get_settings()
    uvicorn.run(
        "plughub_dialog_api.main:app",
        host    = settings.host,
        port    = settings.port,
        workers = settings.workers,
        reload  = False,
    )


if __name__ == "__main__":
    run()
