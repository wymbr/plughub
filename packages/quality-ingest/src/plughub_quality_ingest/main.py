"""
main.py
Quality Ingest FastAPI application (R13a-2).

A pure producer of internal canonical events: it exposes the open ingestion_event_v1
endpoint, runs the masking net-pass, and emits canonical Kafka events. It owns no
store and consumes no topic.
"""
from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import get_settings
from .config_client import SourceMapClient
from .emitter import KafkaEmitter
from .router import router as ingest_router

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

logger = logging.getLogger("plughub.quality_ingest")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    emitter = KafkaEmitter(settings.kafka_brokers, enabled=settings.kafka_enabled)
    try:
        await emitter.start()
    except Exception as exc:
        logger.warning("Kafka emitter failed to start — emits will be skipped: %s", exc)

    app.state.settings = settings
    app.state.emitter = emitter
    app.state.config_client = SourceMapClient(
        settings.config_api_url, cache_ttl_s=settings.source_map_cache_ttl_s
    )

    yield

    await emitter.stop()


app = FastAPI(
    title="PlugHub Quality Ingest",
    version="1.0.0",
    description=(
        "Pluggable contact-history reader (external ↔ internal) for evaluation (R13a). "
        "Exposes the open ingestion_event_v1 event interface and maps it 1:1 to the "
        "internal canonical events, reusing the existing analytics + sampling pipeline."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ingest_router)


@app.get("/v1/health")
async def health() -> JSONResponse:
    return JSONResponse(status_code=200, content={"status": "ok"})


def run() -> None:
    import uvicorn
    settings = get_settings()
    uvicorn.run(
        "plughub_quality_ingest.main:app",
        host=settings.host,
        port=settings.port,
        workers=settings.workers,
        reload=False,
    )


if __name__ == "__main__":
    run()
