"""
main.py
Rules Engine entry point.
Spec: PlugHub v24.0 sections 3.2, 10.2

Two execution loops run concurrently:

1. Redis pub/sub loop (existing)
   Listens for per-turn session updates → evaluates escalation rules.

2. Kafka consumer loop (new)
   Consumes conversations.events (contact_closed) and agent.lifecycle (agent_login)
   → evaluation sampling decisions → publishes evaluation.requested.
"""

from __future__ import annotations
import asyncio
import json
import logging
from datetime import datetime, timezone

import httpx
import redis.asyncio as aioredis
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from .config import get_settings
from .models import EvaluationContext, ContactClosedEvent, PoolEvaluationConfig
from .evaluator import RuleEvaluator
from .escalator import Escalator
from .rule_store import RuleStore
from .evaluation_sampler import EvaluationSampler

logger = logging.getLogger("plughub.rules")


async def run() -> None:
    settings   = get_settings()
    redis_main = aioredis.from_url(settings.redis_url, decode_responses=True)
    redis_sub  = aioredis.from_url(settings.redis_url, decode_responses=True)
    http       = httpx.AsyncClient()

    rule_store = RuleStore(redis_main)
    evaluator  = RuleEvaluator()
    escalator  = Escalator(http)

    # Kafka producer for evaluation.requested events
    kafka_producer = AIOKafkaProducer(bootstrap_servers=settings.kafka_broker)
    await kafka_producer.start()

    sampler = EvaluationSampler(
        redis             = redis_main,
        kafka_producer    = kafka_producer,
        evaluation_topic  = settings.kafka_topic_evaluation,
        counter_ttl_s     = settings.eval_sampling_counter_ttl,
    )

    # Run both loops concurrently
    try:
        await asyncio.gather(
            _run_escalation_loop(redis_sub, rule_store, evaluator, escalator, redis_main, settings),
            _run_kafka_consumer(sampler, settings),
        )
    finally:
        await redis_main.aclose()
        await redis_sub.aclose()
        await http.aclose()
        await kafka_producer.stop()


# ─────────────────────────────────────────────
# Loop 1 — escalation (Redis pub/sub, existing)
# ─────────────────────────────────────────────

async def _run_escalation_loop(
    redis_sub:  aioredis.Redis,
    rule_store: RuleStore,
    evaluator:  RuleEvaluator,
    escalator:  Escalator,
    redis_main: aioredis.Redis,
    settings,
) -> None:
    pubsub = redis_sub.pubsub()
    await pubsub.psubscribe(f"{settings.redis_session_channel}:*")
    logger.info("✅ Rules Engine (escalation) started — listening %s:*", settings.redis_session_channel)

    try:
        async for message in pubsub.listen():
            if message["type"] not in ("pmessage", "message"):
                continue
            asyncio.create_task(
                _process_update(message, rule_store, evaluator, escalator, redis_main)
            )
    finally:
        await pubsub.aclose()


async def _process_update(
    message:    dict,
    rule_store: RuleStore,
    evaluator:  RuleEvaluator,
    escalator:  Escalator,
    redis:      aioredis.Redis,
) -> None:
    try:
        data       = json.loads(message.get("data", "{}"))
        session_id = data.get("session_id")
        tenant_id  = data.get("tenant_id")

        if not session_id or not tenant_id:
            return

        ctx   = await _build_context(redis, session_id, tenant_id, data)
        rules = await rule_store.get_active_rules(tenant_id)
        if not rules:
            return

        rules_sorted = sorted(rules, key=lambda r: r.priority, reverse=True)
        for rule in rules_sorted:
            result = evaluator.evaluate(rule, ctx)
            if result.triggered:
                await escalator.trigger(result)
                break

    except Exception as exc:
        logger.error("Error processing session update: %s", exc)


async def _build_context(
    redis:      aioredis.Redis,
    session_id: str,
    tenant_id:  str,
    data:       dict,
) -> EvaluationContext:
    session_key = f"session:{session_id}:ai"
    raw         = await redis.get(session_key)

    sentiment_history: list[float] = []
    turn_count        = data.get("turn_count", 0)
    elapsed_ms        = data.get("elapsed_ms", 0)
    sentiment_score   = data.get("sentiment_score", 0.0)
    intent_confidence = data.get("intent_confidence", 0.0)
    flags             = data.get("flags", [])

    if raw:
        try:
            session_data      = json.loads(raw)
            turns             = session_data.get("consolidated_turns", [])
            sentiment_history = [t.get("sentiment_score", 0.0) for t in turns]
            if not turn_count:
                turn_count    = len(turns)
        except Exception:
            pass

    return EvaluationContext(
        session_id=        session_id,
        tenant_id=         tenant_id,
        turn_count=        turn_count,
        elapsed_ms=        elapsed_ms,
        sentiment_score=   sentiment_score,
        intent_confidence= intent_confidence,
        flags=             flags,
        sentiment_history= sentiment_history,
    )


# ─────────────────────────────────────────────
# Loop 2 — evaluation sampling (Kafka consumer)
# ─────────────────────────────────────────────

async def _run_kafka_consumer(sampler: EvaluationSampler, settings) -> None:
    consumer = AIOKafkaConsumer(
        settings.kafka_topic_conversations,
        settings.kafka_topic_lifecycle,
        bootstrap_servers = settings.kafka_broker,
        group_id          = settings.kafka_group_id + "-sampling",
        value_deserializer= lambda v: json.loads(v.decode("utf-8")),
        auto_offset_reset = "latest",
    )
    await consumer.start()
    logger.info(
        "✅ Rules Engine (sampling) started — topics: %s, %s",
        settings.kafka_topic_conversations, settings.kafka_topic_lifecycle,
    )

    try:
        async for msg in consumer:
            asyncio.create_task(_dispatch_kafka(msg.topic, msg.value, sampler, settings))
    finally:
        await consumer.stop()


async def _dispatch_kafka(
    topic:    str,
    payload:  dict,
    sampler:  EvaluationSampler,
    settings,
) -> None:
    try:
        if topic == settings.kafka_topic_conversations:
            await _handle_conversations_event(payload, sampler)
        elif topic == settings.kafka_topic_lifecycle:
            await _handle_lifecycle_event(payload, sampler)
    except Exception as exc:
        logger.error("Error in Kafka dispatch: topic=%s — %s", topic, exc)


async def _handle_conversations_event(payload: dict, sampler: EvaluationSampler) -> None:
    event_type = payload.get("event", "")
    if event_type != "conversation_completed":
        return

    # Map conversation_completed fields to ContactClosedEvent
    try:
        event = ContactClosedEvent(
            tenant_id        = payload["tenant_id"],
            contact_id       = payload["conversation_id"],
            agent_id         = payload["instance_id"],
            agent_session_id = payload.get("agent_session_id", payload["instance_id"]),
            agent_type       = payload.get("agent_type", "human"),
            pool_id          = payload.get("pool_id", ""),
            transcript_id    = payload.get("transcript_id"),
            context_package  = payload.get("context_package", {}),
            contact          = payload.get("contact", {}),
            outcome          = payload.get("outcome", "resolved"),
        )
        await sampler.on_contact_closed(event)
    except Exception as exc:
        logger.error("Error handling conversation_completed: %s — %s", payload, exc)


async def _handle_lifecycle_event(payload: dict, sampler: EvaluationSampler) -> None:
    event_type = payload.get("event", "")
    if event_type != "agent_login":
        return

    tenant_id        = payload.get("tenant_id", "")
    agent_session_id = payload.get("instance_id", "")  # instance_id serves as session id

    if tenant_id and agent_session_id:
        await sampler.on_agent_login(tenant_id, agent_session_id)


def main() -> None:
    """Sync entry point for the plughub-rules console script."""
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())


if __name__ == "__main__":
    main()
