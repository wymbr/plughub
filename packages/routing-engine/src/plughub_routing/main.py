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
        # Low-latency tuning: reduce broker wait time before returning data.
        # Default fetch_max_wait_ms=500 adds up to 500ms per poll cycle.
        # With fetch_min_bytes=1, the broker returns as soon as any data arrives.
        fetch_max_wait_ms=100,
        fetch_min_bytes=1,
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
            # Session close events — remove orphan queue entries on client disconnect
            kafka_topic_events         = settings.kafka_topic_events,
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

    # Guard: do not route (and therefore do not INCR active_count) for sessions
    # that are already closing or closed.  This prevents a race condition where:
    #   1. WS1 closes → _trigger_contact_close sets close_fired + publishes agent_done
    #                  → remove_conversation() DECRs active_count and deletes serving-pool key
    #   2. Browser refresh → WS2 connects with same session_id → publishes new
    #      conversations.inbound → mark_busy() fires (serving key gone → guard misses)
    #                            → active_count INCR'd again (counter stuck at 1)
    #   3. WS2 closes → bridge idempotency guard (close_fired already set) → no agent_done
    #                  → no DECR → counter permanently stuck
    # Checking both keys covers two states: close_fired = bridge initiated close;
    # session:{id}:closed = routing engine confirmed close from contact_closed event.
    #
    # EXCEPTION: conference events (conference_id set) are hook/specialist invitations
    # dispatched by fire_pool_hooks() AFTER the session is already closing (e.g. wrap-up
    # and NPS agents in on_human_end).  These must be allowed through — they are
    # legitimate activations on a closing session.  The router already passes
    # session_id=None to mark_busy() for conference events, so they never INCR the
    # active_count or update the serving-pool key; they are safe to route even when
    # session:{id}:closed is set.
    if not event.conference_id:
        is_closing = await redis_client.exists(
            f"session:{event.session_id}:close_fired",
            f"session:{event.session_id}:closed",
        )
        if is_closing:
            logger.info(
                "routing: skipping already-closing session=%s pool=%s",
                event.session_id, event.pool_id,
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
            # Write session.pool.* to ContextStore so skill-flows can reference
            # @ctx.session.pool.id, @ctx.session.pool.channels, and
            # @ctx.session.pool.mentionable_pools without querying agent-registry.
            asyncio.create_task(
                _write_pool_context(
                    redis_client,
                    event.tenant_id,
                    event.session_id,
                    result.pool_id or "",
                )
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

    newly_added = False
    try:
        newly_added = await instance_registry.add_queued_contact(
            tenant_id    = event.tenant_id,
            pool_id      = pool_id,
            session_id   = event.session_id,
            contact_data = contact_data,
            queued_at_ms = now_ms,
        )
        if newly_added:
            logger.info(
                "Contact persisted to queue: session=%s pool=%s tenant=%s",
                event.session_id, pool_id, event.tenant_id,
            )
        else:
            logger.debug(
                "Contact already in queue (re-attempt suppressed notification): session=%s pool=%s",
                event.session_id, pool_id,
            )
    except Exception as exc:
        logger.error(
            "Failed to persist queued contact: session=%s — %s", event.session_id, exc
        )

    # Notify customer via conversations.outbound so channel-gateway delivers
    # a "waiting" message to the customer WebSocket while they're in queue.
    # Only send on first enqueue — suppress on periodic drain re-attempts to
    # avoid spamming the customer with repeated "waiting" messages.
    if not newly_added:
        return
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


async def _write_pool_context(
    redis_client: aioredis.Redis,
    tenant_id:    str,
    session_id:   str,
    pool_id:      str,
    ttl_seconds:  int = 86_400,
) -> None:
    """
    Writes session.pool.* entries to the ContextStore Redis hash so skill-flows
    can reference @ctx.session.pool.id, @ctx.session.pool.channels,
    @ctx.session.pool.mentionable_pools, and @ctx.session.pool.max_reply_time_ms
    without querying the agent-registry.

    Reads pool_config from the routing engine's own Redis cache
    ({tenant_id}:pool_config:{pool_id}) to avoid an additional I/O path.

    Visibility is "agents_only" — pool topology is never exposed to the customer
    channel. Confidence is 1.0 — these are factual routing decisions, not inferred.

    Fire-and-forget: called via asyncio.create_task(); swallows all exceptions.
    TTL set with NX so only the first allocation write wins; subsequent reconnect
    routing events do not reset the TTL beyond the original session lifetime.
    """
    if not pool_id:
        return
    try:
        ctx_key = f"{tenant_id}:ctx:{session_id}"
        now_str = datetime.now(timezone.utc).isoformat()

        # Read pool config from routing engine Redis cache — no new I/O path
        channel_types:             list[str]   = []
        mentionable_pools:         dict | None = None
        mentionable_journeys:      list | None = None
        agent_groups:              list[str]   = []
        max_reply_time_ms:         int | None  = None
        authorized_journey_types:  list[str]   = []
        raw = await redis_client.get(f"{tenant_id}:pool_config:{pool_id}")
        if raw:
            pool_cfg                  = json.loads(raw)
            channel_types             = pool_cfg.get("channel_types", [])
            mentionable_pools         = pool_cfg.get("mentionable_pools") or None
            mentionable_journeys      = pool_cfg.get("mentionable_journeys") or None
            agent_groups              = pool_cfg.get("agent_groups") or []
            max_reply_time_ms         = pool_cfg.get("max_reply_time_ms") or None
            authorized_journey_types  = pool_cfg.get("authorized_journey_types") or []

        def _entry(value: object) -> str:
            return json.dumps({
                "value":      value,
                "confidence": 1.0,
                "source":     "routing_engine",
                "visibility": "agents_only",
                "updated_at": now_str,
            })

        mapping: dict[str, str] = {
            "session.pool.id":       _entry(pool_id),
            "session.pool.channels": _entry(channel_types),
        }
        if mentionable_pools:
            mapping["session.pool.mentionable_pools"] = _entry(mentionable_pools)
        if mentionable_journeys:
            mapping["session.pool.mentionable_journeys"] = _entry(mentionable_journeys)
        if agent_groups:
            mapping["session.pool.agent_groups"] = _entry(agent_groups)
        if max_reply_time_ms is not None:
            mapping["session.pool.max_reply_time_ms"] = _entry(max_reply_time_ms)
        # Arc 17: always write (even as []) so journey_start can check authorization
        mapping["session.authorized_journey_types"] = _entry(authorized_journey_types)

        await redis_client.hset(ctx_key, mapping=mapping)
        # EXPIRE with NX: only sets TTL if no TTL is currently on the key,
        # so we never shorten an expiry already set by another component.
        await redis_client.expire(ctx_key, ttl_seconds, nx=True)

        logger.debug(
            "ContextStore pool context written: tenant=%s session=%s pool=%s channels=%s",
            tenant_id, session_id, pool_id, channel_types,
        )
    except Exception as exc:
        logger.warning(
            "Failed to write pool context to ContextStore: session=%s — %s",
            session_id, exc,
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

                    # Skip sessions already closed (client disconnected while in queue)
                    closed_marker = await redis_client.get(f"session:{session_id}:closed")
                    if closed_marker:
                        await redis_client.zrem(key, session_id)
                        await redis_client.delete(f"{tenant_id}:queue_contact:{session_id}")
                        logger.info(
                            "Periodic drain: skipped closed session=%s pool=%s reason=%s",
                            session_id, pool_id,
                            closed_marker if isinstance(closed_marker, str) else closed_marker.decode(),
                        )
                        continue

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
