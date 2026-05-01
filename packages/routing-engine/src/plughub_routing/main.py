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
from .routing_config import routing_config

logger = logging.getLogger("plughub.routing")


async def run() -> None:
    settings = get_settings()

    # Initialise dependencies
    redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    http_client  = httpx.AsyncClient()

    instance_registry = InstanceRegistry(redis_client)
    pool_registry     = PoolRegistry(redis_client)
    router            = Router(instance_registry, pool_registry)

    # Pre-load routing namespace from Config API so first routing call already
    # has up-to-date SLA/scoring values (performance_score_weight, etc.).
    # Failure is non-fatal — RoutingConfigCache falls back to built-in defaults.
    await routing_config.reload(settings.config_api_url, http_client)
    logger.info("Routing config cache pre-loaded from %s", settings.config_api_url)

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
            redis_client               = redis_client,
            instance_registry          = instance_registry,
            pool_registry              = pool_registry,
            kafka_topic_lifecycle      = settings.kafka_topic_lifecycle,
            kafka_topic_registry       = settings.kafka_topic_registry,
            kafka_brokers              = settings.kafka_brokers,
            kafka_group_id             = settings.kafka_group_id,
            # Queue drain — on agent_ready, pull waiting contacts from queue
            router                     = router,
            kafka_producer             = producer,
            kafka_topic_inbound        = settings.kafka_topic_inbound,
            # Config cache refresh — on config.changed namespace=routing, reload cache
            kafka_topic_config_changed = settings.kafka_topic_config_changed,
            config_api_url             = settings.config_api_url,
            http_client                = http_client,
        )
    )

    # Start crash detector in background (detects agents without heartbeat and re-routes conversations)
    crash_detector = CrashDetector(
        redis_client      = redis_client,
        instance_registry = instance_registry,
        kafka_producer    = producer,
    )
    crash_detector_task = asyncio.create_task(crash_detector.run())

    # Periodic queue drain — fallback for environments where agent_ready Kafka
    # events are not published (e.g. demo mode where Agent Assist UI subscribes
    # directly to Redis without going through the agent_login/agent_ready flow).
    # Every QUEUE_DRAIN_INTERVAL_S seconds, scan all pools with queued contacts
    # and re-publish any contact whose pool has a ready instance available.
    periodic_drain_task = asyncio.create_task(
        _periodic_queue_drain(redis_client, producer, settings)
    )

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
        periodic_drain_task.cancel()
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


async def _periodic_queue_drain(
    redis_client: aioredis.Redis,
    producer:     "AIOKafkaProducer",
    settings,
) -> None:
    """
    Periodic fallback queue drain — runs every QUEUE_DRAIN_INTERVAL_S seconds.

    This supplements the event-driven drain (triggered by agent_ready Kafka events)
    for deployment environments where agents do not publish agent_ready — notably
    the demo/dev environment where Agent Assist UI connects directly to Redis pub/sub
    without going through the agent_login → agent_ready lifecycle.

    Algorithm:
      1. SCAN Redis for all keys matching *:pool:*:queue (sorted sets)
      2. For each non-empty queue, check if any instance in the pool is ready
      3. If yes: pop the oldest session_id from the queue, retrieve the full
         contact JSON, remove the entry, and re-publish to conversations.inbound
         so the Routing Engine allocates it in the normal processing loop.
      4. Stop after draining one contact per pool per cycle — if the agent has
         capacity for more, the allocation will succeed and the routing event
         will trigger a subsequent drain cycle.
    """
    interval = getattr(settings, "queue_drain_interval_s", 15)
    if interval <= 0:
        return   # disabled
    await asyncio.sleep(interval)   # initial delay — let all services start first

    while True:
        try:
            # Scan for all queue sorted-set keys
            cursor     = 0
            drained    = 0
            while True:
                cursor, keys = await redis_client.scan(
                    cursor, match="*:pool:*:queue", count=50
                )
                for key in keys:
                    parts = key.split(":")
                    # Expected format: {tenant_id}:pool:{pool_id}:queue
                    if len(parts) < 4 or parts[-1] != "queue" or parts[-3] != "pool":
                        continue
                    tenant_id = parts[0]
                    pool_id   = ":".join(parts[2:-1])   # handles pool ids without colons

                    # Check if queue is non-empty
                    oldest = await redis_client.zrange(key, 0, 0, withscores=False)
                    if not oldest:
                        continue

                    # Check if any instance in the pool is ready
                    pool_inst_key = f"{tenant_id}:pool:{pool_id}:instances"
                    instance_ids  = await redis_client.smembers(pool_inst_key)
                    has_capacity  = False
                    for iid in instance_ids:
                        raw = await redis_client.get(f"{tenant_id}:instance:{iid}")
                        if not raw:
                            continue
                        try:
                            data = json.loads(raw)
                            status = data.get("status") or data.get("state", "")
                            current  = int(data.get("current_sessions", 0))
                            max_conc = int(data.get("max_concurrent", 1))
                            if status == "ready" and current < max_conc:
                                has_capacity = True
                                break
                        except Exception:
                            continue

                    if not has_capacity:
                        continue

                    # Dequeue oldest contact
                    session_id = oldest[0]
                    contact_key = f"{tenant_id}:queue_contact:{session_id}"
                    raw_contact = await redis_client.get(contact_key)
                    if not raw_contact:
                        # Stale entry — remove and skip
                        await redis_client.zrem(key, session_id)
                        continue

                    # Check if a queue agent is active (signal it instead of re-publishing)
                    queue_agent_active = await redis_client.get(
                        f"queue:agent_active:{session_id}"
                    )

                    # Remove from queue before acting — prevents double-routing
                    await redis_client.zrem(key, session_id)
                    await redis_client.delete(contact_key)

                    if queue_agent_active:
                        # Signal the queue agent's menu:result BLPOP
                        await redis_client.lpush(
                            f"menu:result:{session_id}", "__agent_available__"
                        )
                        logger.info(
                            "Periodic drain: signalled queue agent session=%s pool=%s",
                            session_id, pool_id,
                        )
                    else:
                        # Re-publish to conversations.inbound for normal routing
                        try:
                            contact_data = json.loads(raw_contact)
                            await producer.send(settings.kafka_topic_inbound, value=contact_data)
                            logger.info(
                                "Periodic drain: re-routing session=%s pool=%s tenant=%s",
                                session_id, pool_id, tenant_id,
                            )
                        except Exception as exc:
                            logger.warning(
                                "Periodic drain: failed to re-publish session=%s — %s",
                                session_id, exc,
                            )

                    drained += 1

                if cursor == 0:
                    break  # SCAN complete

            if drained:
                logger.info("Periodic drain: drained %d contact(s)", drained)

        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.warning("Periodic drain error: %s", exc)

        await asyncio.sleep(interval)


def main() -> None:
    """Sync entry point for the plughub-routing console script."""
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())


if __name__ == "__main__":
    main()
