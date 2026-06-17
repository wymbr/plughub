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
from .sampling import should_sample, compute_priority
from .router import router, _ingest_from_completed_event
from .contestation_router import contestation_router

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


# ─── conversations.session_closed → sampling (S2.1, campaign-driven) ───────────

def _duration_s(started_at: str | None, closed_at: str | None) -> float:
    """Duração da sessão em segundos a partir dos ISO timestamps (0 se faltarem)."""
    if not started_at or not closed_at:
        return 0.0
    try:
        a = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(closed_at).replace("Z", "+00:00"))
        return max(0.0, (b - a).total_seconds())
    except Exception:
        return 0.0


async def _sample_on_close(db_pool: _db.asyncpg.Pool, payload: dict) -> None:
    """No fechamento, amostra contra as campanhas ATIVAS e cria uma
    EvaluationInstance(status=scheduled) por match. NÃO roda o avaliador — só
    registra o candidato (barato). O dispatcher (S2.2/S2.3) é quem despacha na
    janela do calendário da campanha."""
    session_id = payload.get("session_id")
    tenant_id  = payload.get("tenant_id")
    if not session_id or not tenant_id:
        return

    session_meta = {
        "pool_id":       payload.get("pool_id"),
        "channel":       payload.get("channel"),
        "outcome":       payload.get("outcome"),
        "agent_type_id": payload.get("agent_type_id"),
        "duration_s":    _duration_s(payload.get("started_at"), payload.get("closed_at")),
    }

    campaigns = await _db.list_campaigns(db_pool, tenant_id, status="active", limit=500)
    for c in campaigns:
        # Hard filter: pool avaliado (evaluation_pool_id; fallback ao pool_id legado).
        epid = c.get("evaluation_pool_id") or c.get("pool_id")
        if epid and session_meta.get("pool_id") != epid:
            continue
        rules = c.get("sampling_rules") or {}
        if not should_sample(
            session_id, session_meta, rules, counter=int(c.get("total_instances") or 0)
        ):
            continue
        if await _db.instance_exists_for_session(db_pool, c["id"], session_id, tenant_id):
            continue
        priority = compute_priority(session_meta, rules)
        inst = await _db.create_instance(
            db_pool,
            tenant_id=tenant_id,
            campaign_id=c["id"],
            form_id=c["form_id"],
            session_id=session_id,
            priority=priority,
        )
        logger.info(
            "sampling: scheduled instance %s (campaign=%s session=%s pool=%s)",
            inst.get("id"), c["id"], session_id, session_meta.get("pool_id"),
        )


async def _run_session_closed_consumer(app: FastAPI) -> None:
    consumer = AIOKafkaConsumer(
        "conversations.session_closed",
        bootstrap_servers=settings.kafka_brokers,
        group_id="evaluation-api-sampling-consumer",
        auto_offset_reset="latest",
    )
    await consumer.start()
    logger.info("conversations.session_closed sampling consumer started")
    try:
        async for msg in consumer:
            if not msg.value:
                continue
            try:
                payload = json.loads(msg.value)
            except Exception:
                logger.warning("sampling: invalid session_closed payload")
                continue
            try:
                await _sample_on_close(app.state.db_pool, payload)
            except Exception as exc:
                logger.error("sampling: failed for %s: %s", payload.get("session_id"), exc)
    finally:
        await consumer.stop()


async def _run_evaluation_completed_consumer(app: FastAPI) -> None:
    """Consume evaluation.events, filter `evaluation.completed` (published by the
    evaluation_submit MCP tool) and persist via ingest. This is the Arc 13
    real-evaluator link — previously missing, so only analytics-api/ClickHouse
    consumed these events while the evaluation-api Postgres (results + instance
    lifecycle) never advanced. group_id is independent of the other consumers."""
    consumer = AIOKafkaConsumer(
        settings.evaluation_topic,
        bootstrap_servers=settings.kafka_brokers,
        group_id="evaluation-api-ingest-consumer",
        auto_offset_reset="latest",
    )
    await consumer.start()
    logger.info("evaluation.events ingest consumer started (topic=%s)", settings.evaluation_topic)
    try:
        async for msg in consumer:
            if not msg.value:
                continue
            try:
                payload = json.loads(msg.value)
            except Exception:
                logger.warning("ingest-consumer: invalid evaluation.events payload")
                continue
            if payload.get("event_type") != "evaluation.completed":
                continue
            try:
                await _ingest_from_completed_event(
                    app.state.db_pool, app.state.kafka_producer, payload,
                )
            except Exception as exc:
                logger.error(
                    "ingest-consumer: failed for instance=%s: %s",
                    payload.get("instance_id"), exc,
                )
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

        # S2.1 — conversations.session_closed sampling consumer (campaign-driven)
        app.state.sampling_consumer_task = asyncio.create_task(
            _run_session_closed_consumer(app),
            name="session-closed-sampling-consumer",
        )
        logger.info("conversations.session_closed sampling consumer task scheduled")

        # Arc 13 real-evaluator link — evaluation.completed → ingest
        # (persists EvaluationResult + advances instance; was missing).
        app.state.ingest_consumer_task = asyncio.create_task(
            _run_evaluation_completed_consumer(app),
            name="evaluation-completed-ingest-consumer",
        )
        logger.info("evaluation.events ingest consumer task scheduled")

    @app.on_event("shutdown")
    async def shutdown() -> None:
        if hasattr(app.state, "workflow_consumer_task"):
            app.state.workflow_consumer_task.cancel()
        if hasattr(app.state, "sampling_consumer_task"):
            app.state.sampling_consumer_task.cancel()
        if hasattr(app.state, "ingest_consumer_task"):
            app.state.ingest_consumer_task.cancel()
        if hasattr(app.state, "kafka_producer"):
            await app.state.kafka_producer.stop()
        if hasattr(app.state, "redis"):
            await app.state.redis.aclose()
        if hasattr(app.state, "db_pool"):
            await app.state.db_pool.close()

    app.include_router(router)
    app.include_router(contestation_router)  # Arc 13 — contestation, curation, calibration
    return app


app = create_app()


def run() -> None:
    uvicorn.run("plughub_evaluation_api.main:app", host="0.0.0.0", port=settings.port, reload=False)


if __name__ == "__main__":
    run()
