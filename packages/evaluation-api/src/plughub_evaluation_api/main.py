"""
main.py
FastAPI application entry point for evaluation-api.

Port: 3400 (configurable via PLUGHUB_EVALUATION_PORT)
"""
from __future__ import annotations

import logging
import sys

import uvicorn
from aiokafka import AIOKafkaProducer
from fastapi import FastAPI

from .config import settings
from . import db as _db
from .router import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("plughub.evaluation")


def create_app() -> FastAPI:
    app = FastAPI(
        title="PlugHub Evaluation API",
        version="1.0.0",
        description="Arc 6 quality evaluation platform",
    )

    @app.on_event("startup")
    async def startup() -> None:
        # Database pool
        logger.info("connecting to PostgreSQL…")
        app.state.db_pool = await _db.create_pool(settings.database_url)
        await _db.ensure_schema(app.state.db_pool)
        logger.info("evaluation schema ready")

        # Kafka producer
        logger.info("connecting to Kafka…")
        producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_brokers,
            enable_idempotence=True,
        )
        await producer.start()
        app.state.kafka_producer = producer
        logger.info("Kafka producer ready")

    @app.on_event("shutdown")
    async def shutdown() -> None:
        if hasattr(app.state, "kafka_producer"):
            await app.state.kafka_producer.stop()
        if hasattr(app.state, "db_pool"):
            await app.state.db_pool.close()

    app.include_router(router)
    return app


app = create_app()


def run() -> None:
    uvicorn.run("plughub_evaluation_api.main:app", host="0.0.0.0", port=settings.port, reload=False)


if __name__ == "__main__":
    run()
