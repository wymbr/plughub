"""
main.py
Quality Ingest FastAPI application (R13a-2).

A pure producer of internal canonical events: it exposes the open ingestion_event_v1
endpoint, runs the masking net-pass, and emits canonical Kafka events. It owns no
store and consumes no topic.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import get_settings
from .config_client import SourceMapClient
from .emitter import KafkaEmitter
from .router import router as ingest_router

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
