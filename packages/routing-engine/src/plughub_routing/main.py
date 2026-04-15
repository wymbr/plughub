"""
main.py
Routing Engine entry point — Kafka consumer + listeners.
Spec: PlugHub v24.0 section 3.3
"""

from __future__ import annotations
import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

import httpx
from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
import redis.asyncio as aioredis

from .config import get_settings
from .crash_detector import CrashDetector
from .evaluation_consumer import EvaluationConsumer, load_evaluation_flow
from .models import ConversationInboundEvent, ConversationRoutedEvent
from .registry import InstanceRegistry, PoolRegistry
from .router import Router
from .kafka_listener import run_listeners

logger = logging.getLogger("plughub.routing")


async def run() -> None:
    settings = get_settings()

    # Initialise dependencies
    redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    http_client  = httpx.AsyncClient()

    instance_registry = InstanceRegistry(redis_client)
    pool_registry     = PoolRegistry(redis_client)
    router            = Router(instance_registry, pool_registry)

    consumer = AIOKafkaConsumer(
        settings.kafka_topic_inbound,
        bootstrap_servers=settings.kafka_brokers,
        group_id=settings.kafka_group_id,
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        auto_offset_reset="earliest",
    )
    producer = AIOKafkaProducer(
        bootstrap_servers=settings.kafka_brokers,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
    )

    await consumer.start()
    await producer.start()
    logger.info("✅ Routing Engine started — consuming %s", settings.kafka_topic_inbound)

    # Start kafka_listener in background (populates Redis cache of pools and instances)
    listener_task = asyncio.create_task(
        run_listeners(
            redis_client          = redis_client,
            instance_registry     = instance_registry,
            pool_registry         = pool_registry,
            kafka_topic_lifecycle = settings.kafka_topic_lifecycle,
            kafka_topic_registry  = settings.kafka_topic_registry,
            kafka_brokers         = settings.kafka_brokers,
            kafka_group_id        = settings.kafka_group_id,
            # Queue drain — on agent_ready, pull waiting contacts from queue
            router                = router,
            kafka_producer        = producer,
            kafka_topic_inbound   = settings.kafka_topic_inbound,
        )
    )

    # Start crash detector in background (detects agents without heartbeat and re-routes conversations)
    crash_detector = CrashDetector(
        redis_client      = redis_client,
        instance_registry = instance_registry,
        kafka_producer    = producer,
    )
    crash_detector_task = asyncio.create_task(crash_detector.run())

    # Start evaluation consumer in background (triggers SkillFlowEngine for sampled contacts)
    evaluation_flow = await load_evaluation_flow(
        skill_flow_service_url = settings.skill_flow_service_url,
        evaluation_skill_id    = settings.evaluation_skill_id,
        http_client            = http_client,
    )
    evaluation_consumer = EvaluationConsumer(
        http_client            = http_client,
        skill_flow_service_url = settings.skill_flow_service_url,
        evaluation_skill_id    = settings.evaluation_skill_id,
        skill_flow             = evaluation_flow,
    )
    evaluation_task = asyncio.create_task(
        evaluation_consumer.run(
            kafka_topic    = settings.kafka_topic_evaluation,
            kafka_brokers  = settings.kafka_brokers,
            kafka_group_id = settings.kafka_group_id,
        )
    )

    try:
        async for msg in consumer:
            asyncio.create_task(
                _process_message(msg.value, router, producer, settings,
                                 redis_client, instance_registry)
            )
    finally:
        listener_task.cancel()
        crash_detector_task.cancel()
        evaluation_task.cancel()
        await consumer.stop()
        await producer.stop()
        await redis_client.aclose()
        await http_client.aclose()


async def _process_message(
    payload:           dict,
    router:            Router,
    producer:          AIOKafkaProducer,
    settings,
    redis_client:      aioredis.Redis,
    instance_registry: InstanceRegistry,
) -> None:
    from pydantic import ValidationError

    try:
        event = ConversationInboundEvent.model_validate(payload)
    except ValidationError:
        # conversations.inbound carries two event formats:
        #   1. ConversationInboundEvent  — routing request (tenant_id, customer_id, started_at …)
        #   2. NormalizedInboundEvent    — customer message (author, content, context_snapshot …)
        # The Routing Engine only processes format 1. Format 2 is consumed by the
        # Orchestrator Bridge. Silently discard anything that doesn't validate.
        if "author" in payload:
            logger.debug(
                "Skipping NormalizedInboundEvent (customer message) session=%s",
                payload.get("session_id"),
            )
        else:
            logger.warning(
                "Unrecognised inbound event (not a routing request): session=%s fields=%s",
                payload.get("session_id"), list(payload.keys()),
            )
        return

    try:
        result = await router.route(event)

        routed_event = ConversationRoutedEvent(
            session_id=event.session_id,
            tenant_id=event.tenant_id,
            result=result,
            routed_at=datetime.now(timezone.utc).isoformat(),
        )

        topic = settings.kafka_topic_routed if result.allocated else settings.kafka_topic_queued
        await producer.send(topic, value=routed_event.model_dump())

        if result.allocated:
            logger.info(
                "Routed session=%s → instance=%s pool=%s priority_score=%.4f mode=%s",
                event.session_id, result.instance_id,
                result.pool_id, result.priority_score, result.routing_mode,
            )
        else:
            logger.warning(
                "Queued session=%s channel=%s tenant=%s pool=%s — no agents available",
                event.session_id, event.channel, event.tenant_id, event.pool_id,
            )
            # Persist contact to queue for drain-on-agent-ready
            now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
            await _persist_queued_contact(
                event, producer, redis_client, instance_registry, now_ms, settings
            )

    except Exception as exc:
        logger.error("Error routing session: %s — %s", payload.get("session_id"), exc)


async def _persist_queued_contact(
    event:             ConversationInboundEvent,
    producer:          AIOKafkaProducer,
    redis_client:      aioredis.Redis,
    instance_registry: InstanceRegistry,
    now_ms:            int,
    settings,
) -> None:
    """
    Stores contact in the pool queue sorted set and notifies the customer.
    Full original event is preserved so it can be re-published verbatim when
    an agent becomes available (drain-on-ready).
    """
    pool_id = event.pool_id or ""
    if not pool_id:
        logger.warning(
            "Cannot enqueue: no pool_id in event for session=%s", event.session_id
        )
        return

    # Store the full event dict + queue metadata so drain can re-publish it intact
    contact_data = event.model_dump()
    contact_data["queued_at_ms"] = now_ms
    contact_data["tier"]         = event.customer_profile.tier

    try:
        await instance_registry.add_queued_contact(
            tenant_id    = event.tenant_id,
            pool_id      = pool_id,
            session_id   = event.session_id,
            contact_data = contact_data,
            queued_at_ms = now_ms,
        )
        logger.info(
            "Contact persisted to queue: session=%s pool=%s tenant=%s",
            event.session_id, pool_id, event.tenant_id,
        )
    except Exception as exc:
        logger.error(
            "Failed to persist queued contact: session=%s — %s", event.session_id, exc
        )

    # Notify customer via conversations.outbound so channel-gateway delivers
    # a "waiting" message to the customer WebSocket while they're in queue.
    try:
        contact_id_raw = await redis_client.get(
            f"session:{event.session_id}:contact_id"
        )
        contact_id = contact_id_raw or event.session_id
        await producer.send(
            settings.kafka_topic_outbound,
            value={
                "type":       "message.text",
                "contact_id": contact_id,
                "session_id": event.session_id,
                "message_id": str(uuid.uuid4()),
                "channel":    event.channel,
                "direction":  "outbound",
                "author":     {"type": "system", "id": "routing-engine"},
                "content":    {
                    "type": "text",
                    "text": "Aguardando agente disponível. Por favor, aguarde...",
                },
                "text":      "Aguardando agente disponível. Por favor, aguarde...",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception as exc:
        logger.warning(
            "Could not send waiting notification to customer: session=%s — %s",
            event.session_id, exc,
        )


def main() -> None:
    """Sync entry point for the plughub-routing console script."""
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())


if __name__ == "__main__":
    main()
