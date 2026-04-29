"""
main.py
FastAPI application entry point for the Pricing API.
Port 3900.
"""
from __future__ import annotations

import logging

import asyncpg
import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .db import ensure_schema
from .router import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s — %(message)s",
)
logger = logging.getLogger("plughub.pricing")

app = FastAPI(
    title       = "PlugHub Pricing API",
    version     = "1.0.0",
    description = "Capacity-based pricing module — installation resources, reserve pools, invoice generation.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)


@app.on_event("startup")
async def startup() -> None:
    settings = get_settings()
    logger.info("Connecting to PostgreSQL…")
    app.state.pg_pool = await asyncpg.create_pool(
        settings.database_url,
        min_size = 2,
        max_size = 10,
    )
    await ensure_schema(app.state.pg_pool)
    logger.info("Pricing API ready on port %d", settings.port)


@app.on_event("shutdown")
async def shutdown() -> None:
    await app.state.pg_pool.close()
    logger.info("Pricing API shutdown")


def run() -> None:
    settings = get_settings()
    uvicorn.run(
        "plughub_pricing_api.main:app",
        host    = settings.host,
        port    = settings.port,
        workers = settings.workers,
        reload  = False,
    )


if __name__ == "__main__":
    run()
