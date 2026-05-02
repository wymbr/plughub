"""
main.py
FastAPI application entry point for evaluation-api.

Port: 3400 (configurable via PLUGHUB_EVALUATION_PORT)
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from datetime import datetime, timezone

import redis.asyncio as aioredis
import uvicorn
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from . import db as _db
from .router import router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("plughub.evaluation")


# ─── workflow.events consumer ─────────────────────────────────────────────────

async def _on_workflow_event(db_pool: _db.asyncpg.Pool, msg_value: bytes) -> None:
    """
    Update evaluation result workflow state from workflow.events Kafka events.

    workflow.suspended → set action_required, current_round, deadline_at, resume_token
    workflow.completed → lock result with appropriate lock_reason
    """
    try:
        event = json.loads(msg_value)
    except Exception:
        return

    event_type = event.get("event_type", "")
    context = event.get("context") or {}
    result_id = context.get("result_id")
    tenant_id = context.get("tenant_id")

    if not result_id:
        return  # not an evaluation workflow event

    if event_type == "workflow.suspended":
        # Determine which party should act next based on the suspended step name
        suspended_step = event.get("suspended_at_step", "")
        if "revisao" in suspended_step or "review" in suspended_step:
            action_required = "review"
        elif "contestacao" in suspended_step or "contest" in suspended_step:
            action_required = "contestation"
        else:
            action_required = None

        deadline_at: datetime | None = None
        if event.get("resume_expires_at"):
            try:
                deadline_at = datetime.fromisoformat(event["resume_expires_at"])
            except Exception:
                pass

        await _db.update_result_workflow_state(
            db_pool,
            result_id,
            action_required=action_required,
            current_round=context.get("current_round", 1),
            deadline_at=deadline_at,
            resume_token=event.get("resume_token"),
            workflow_instance_id=event.get("instance_id"),
        )
        logger.info(
            "result %s workflow suspended: action=%s round=%s",
            result_id, action_required, context.get("current_round"),
        )

    elif event_type == "workflow.completed":
        lock_reason = context.get("lock_reason", "completed")
        await _db.lock_result(db_pool, result_id, lock_reason=lock_reason, locked_by="workflow")
        logger.info("result %s locked by workflow: lock_reason=%s", result_id, lock_reason)

    elif event_type == "workflow.timed_out":
        # Workflow timeout = freeze result at current state
        await _db.update_result_workflow_state(
            db_pool,
            result_id,
            action_required=None,
            locked=True,
            lock_reason="review_timeout",
        )
        logger.info("result %s locked (workflow timeout)", result_id)


async def _run_workflow_consumer(app: FastAPI) -> None:
    consumer = AIOKafkaConsumer(
        settings.workflow_events_topic,
        bootstrap_servers=settings.kafka_brokers,
        group_id="evaluation-api-workflow-consumer",
        auto_offset_reset="latest",
        enable_auto_commit=True,
    )
    await consumer.start()
    logger.info("workflow.events consumer started")
    try:
        async for msg in consumer:
            if msg.value:
                try:
                    await _on_workflow_event(app.state.db_pool, msg.value)
                except Exception as exc:
                    logger.error("workflow event processing error: %s", exc)
    finally:
        await consumer.stop()


def create_app() -> FastAPI:
    app = FastAPI(
        title="PlugHub Evaluation API",
        version="1.0.0",
        description="Arc 6 quality evaluation platform",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    async def startup() -> None:
        # Database pool
        logger.info("connecting to PostgreSQL…")
        app.state.db_pool = await _db.create_pool(settings.database_url)
        await _db.ensure_schema(app.state.db_pool)
        logger.info("evaluation schema ready")

        # Redis (for ContextStore writes)
        logger.info("connecting to Redis…")
        app.state.redis = aioredis.from_url(settings.redis_url, decode_responses=True)
        logger.info("Redis client ready")

        # Kafka producer
        logger.info("connecting to Kafka…")
        producer = AIOKafkaProducer(
            bootstrap_servers=settings.kafka_brokers,
            enable_idempotence=True,
        )
        await producer.start()
        app.state.kafka_producer = producer
        logger.info("Kafka producer ready")

        # Start workflow.events consumer as background task
        app.state.workflow_consumer_task = asyncio.create_task(
            _run_workflow_consumer(app),
            name="workflow-events-consumer",
        )
        logger.info("workflow.events consumer task scheduled")

    @app.on_event("shutdown")
    async def shutdown() -> None:
        if hasattr(app.state, "workflow_consumer_task"):
            app.state.workflow_consumer_task.cancel()
        if hasattr(app.state, "kafka_producer"):
            await app.state.kafka_producer.stop()
        if hasattr(app.state, "redis"):
            await app.state.redis.aclose()
        if hasattr(app.state, "db_pool"):
            await app.state.db_pool.close()

    app.include_router(router)
    return app


app = create_app()


def run() -> None:
    uvicorn.run("plughub_evaluation_api.main:app", host="0.0.0.0", port=settings.port, reload=False)


if __name__ == "__main__":
    run()
