"""
main.py
Quality Export FastAPI application (R13d).

Reads internal history from ClickHouse and re-emits ingestion_event_v1 through the
quality-ingest contract. Read-only on the internal side; a pure client of the
module's open endpoint on the emit side.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .ch_client import ClickHouseClient
from .config import get_settings
from .router import router as export_router

logger = logging.getLogger("plughub.quality_export")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()
    app.state.settings = settings
    app.state.ch = ClickHouseClient(
        settings.clickhouse_url,
        settings.clickhouse_db,
        settings.clickhouse_user,
        settings.clickhouse_password,
    )
    yield


app = FastAPI(
    title="PlugHub Quality Export",
    version="1.0.0",
    description=(
        "Internal history reader (R13d): reads ClickHouse session history and re-emits "
        "ingestion_event_v1 through the quality-ingest contract for re-evaluation — same "
        "port as the external importer, no divergent evaluation code."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(export_router)


@app.get("/v1/health")
async def health() -> JSONResponse:
    return JSONResponse(status_code=200, content={"status": "ok"})


def run() -> None:
    import uvicorn
    settings = get_settings()
    uvicorn.run(
        "plughub_quality_export.main:app",
        host=settings.host,
        port=settings.port,
        workers=settings.workers,
        reload=False,
    )


if __name__ == "__main__":
    run()
