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

from .admission import AdmissionController, AdmissionDecision
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
    # Fase B (queue-attended-model): hybrid session admission control
    admission         = AdmissionController(redis_client, pool_registry)

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

    # Fase 2 — occupancy sampler: samples per-pool concurrency (active_count) +
    # tenant total every few seconds, tracks the per-minute peak, and flushes it
    # to Kafka `pool.occupancy` for the capacity/headroom report.
    occupancy_task = asyncio.create_task(
        _occupancy_sampler(redis_client, producer, settings)
    )

    # Fase B — admission reconciler: releases admission-bucket slots of sessions
    # whose session:{id}:closed marker exists (~60s lag, self-healing gauge).
    admission_reconcile_task = asyncio.create_task(
        _admission_reconciler(admission)
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
                                 redis_client, instance_registry, admission)
            )
    finally:
        listener_task.cancel()
        crash_detector_task.cancel()
        periodic_drain_task.cancel()
        occupancy_task.cancel()
        admission_reconcile_task.cancel()
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
    admission:         "AdmissionController | None" = None,
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

    # ── Fase B (queue-attended-model): hybrid session admission ───────────────
    # Runs on every routing request against the requested pool's bucket.
    # SET-based counters make re-publishes (drain, crash-recovery) idempotent;
    # cross-pool escalation migrates the bucket. Conference events are agent
    # invitations on an existing session — never re-admitted.
    if admission is not None and not event.conference_id and event.pool_id:
        try:
            _adm_pool = await router._pools.get_pool(event.tenant_id, event.pool_id)
            decision  = await admission.admit(
                event.tenant_id, event.session_id, _adm_pool, event.pool_id
            )
        except Exception as exc:
            logger.warning(
                "admission check failed (fail-open): session=%s — %s",
                event.session_id, exc,
            )
            decision = AdmissionDecision(admitted=True)
        if not decision.admitted:
            await _emit_outage(event, decision, producer, redis_client)
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


async def _pool_sla_target(
    redis_client: aioredis.Redis, tenant_id: str, pool_id: str
) -> int | None:
    """SLA (ms) do pool a partir do cache de pool_config — para denormalizar
    nos contact_closed autoritativos do routing (a linha de close é a que
    sobrevive no ReplacingMergeTree do analytics)."""
    if not tenant_id or not pool_id:
        return None
    try:
        raw = await redis_client.get(f"{tenant_id}:pool_config:{pool_id}")
        if raw:
            return (json.loads(raw) or {}).get("sla_target_ms")
    except Exception:
        pass
    return None


async def _emit_outage(
    event:        ConversationInboundEvent,
    decision:     "AdmissionDecision",
    producer:     AIOKafkaProducer,
    redis_client: aioredis.Redis,
) -> None:
    """
    Fase B (queue-attended-model): admission rejected → record the contact as
    OUTAGE (demanda reprimida) and close it gracefully.

    Emits:
      1. conversations.events contact_closed — close_reason=no_resource,
         outcome=outage, outage_cause (routing is the authoritative sessions
         writer here: the bridge never closes a never-routed session, and
         gateway-sourced events are skipped by the analytics parser).
      2. conversations.participants — synthetic zero-duration segment
         (agent_type=system, outcome=outage) pointing at the pool that lacked
         resources; close_reason carries the cause (reservation_full|shared_full).
      3. conversations.outbound session.closed — channel-gateway closes the
         customer connection (rejection render: spec § rejeição na porta).

    Also sets session:{id}:closed + contact_close_fired markers so the bridge's
    re-entry (triggered by the gateway's own contact_closed on WS teardown)
    does NOT fire _close_contact_layer and overwrite the outage row.
    """
    now_iso    = datetime.now(timezone.utc).isoformat()
    session_id = event.session_id
    seg_id     = str(uuid.uuid4())
    sla_target = await _pool_sla_target(
        redis_client, event.tenant_id, decision.pool_id or event.pool_id or ""
    )

    # Markers FIRST — block bridge re-close + mark session closed platform-wide.
    try:
        await redis_client.setex(f"session:{session_id}:closed", 604_800, "no_resource")
        await redis_client.setex(
            f"session:{session_id}:contact_close_fired", 604_800, "1"
        )
    except Exception as exc:
        logger.warning("outage: could not set close markers session=%s — %s",
                       session_id, exc)

    # 1. Authoritative sessions close row.
    try:
        await producer.send("conversations.events", value={
            "event_type":   "contact_closed",
            "session_id":   session_id,
            "tenant_id":    event.tenant_id,
            "reason":       "no_resource",      # transport (bridge ignores: not customer_side)
            "close_reason": "no_resource",      # business domain
            "outcome":      "outage",
            "outage_cause": decision.cause,
            "pool_id":      event.pool_id or "",
            "channel":      event.channel,
            "customer_id":  event.customer_id,
            "started_at":   event.started_at or now_iso,
            "ended_at":     now_iso,
            "sla_target_ms": sla_target,
            "source":       "routing_engine",
        })
    except Exception as exc:
        logger.error("outage: failed to publish contact_closed session=%s — %s",
                     session_id, exc)

    # 2. Synthetic segment — WHICH pool lacked resources (ledger = segments).
    try:
        await producer.send("conversations.participants", value={
            "event_id":       str(uuid.uuid4()),
            "type":           "participant_left",
            "session_id":     session_id,
            "tenant_id":      event.tenant_id,
            "segment_id":     seg_id,
            "participant_id": "system-admission",
            "pool_id":        decision.pool_id or event.pool_id or "",
            "agent_type_id":  "system",
            "agent_type":     "system",
            "role":           "primary",
            "sequence_index": 0,
            "joined_at":      now_iso,
            "timestamp":      now_iso,
            "duration_ms":    0,
            "outcome":        "outage",
            "close_reason":   decision.cause,   # reservation_full | shared_full
        })
    except Exception as exc:
        logger.error("outage: failed to publish synthetic segment session=%s — %s",
                     session_id, exc)

    # 3. Close the customer connection (gateway renders the rejection).
    # Render v2: farewell_text no próprio session.closed — o adapter renderiza
    # a mensagem ANTES do frame de close (sem corrida message×close).
    try:
        await producer.send("conversations.outbound", value={
            "type":       "session.closed",
            "contact_id": event.customer_id,
            "session_id": session_id,
            "channel":    event.channel,
            "reason":     "agent_done",   # gateway transport Literal — analytics skips it
            "farewell_text": routing_config.get("msg_outage_rejection"),
        })
    except Exception as exc:
        logger.error("outage: failed to publish outbound close session=%s — %s",
                     session_id, exc)

    logger.warning(
        "OUTAGE: session=%s tenant=%s pool=%s channel=%s cause=%s current=%s limit=%s",
        session_id, event.tenant_id, event.pool_id, event.channel,
        decision.cause, decision.current, decision.limit,
    )


async def _emit_queue_timeout(
    redis_client: aioredis.Redis,
    producer:     AIOKafkaProducer,
    settings,
    tenant_id:    str,
    pool_id:      str,
    session_id:   str,
    now_ms:       int,
) -> None:
    """
    Fase E (queue-attended-model): retention bound exceeded — close the queued
    contact gracefully with close_reason=max_wait_exceeded.

    Mirrors _emit_outage: routing is the authoritative sessions writer for
    never-routed sessions (the bridge cannot close them — Fase A gap).

      1. Markers session:{id}:closed=max_wait_exceeded + contact_close_fired —
         block the bridge re-close on the WS teardown that follows.
      2. Queue agent active → LPUSH session:closed:{id}: its menu BLPOP exits
         via on_disconnect and the bridge closes the REAL queue segment with
         the Fase C abandoned override.
      3. Mute queue (no queue agent) → synthetic role=queue segment so the
         segments ledger still records the wait (Fila/SLA Fase D counts it).
      4. Courtesy message + outbound session.closed (gateway closes the WS).
      5. Authoritative contact_closed: close_reason=max_wait_exceeded,
         outcome=abandoned.

    Admission slots are released by the admission reconciler via the closed
    marker (~60s lag, acceptable). Caller has already ZREM'd the queue entry.
    """
    now_iso = datetime.now(timezone.utc).isoformat()

    # Contact data persisted at enqueue (full inbound event) — channel etc.
    contact: dict = {}
    try:
        raw = await redis_client.get(f"{tenant_id}:queue_contact:{session_id}")
        if raw:
            contact = json.loads(raw)
        await redis_client.delete(f"{tenant_id}:queue_contact:{session_id}")
    except Exception:
        pass
    channel      = contact.get("channel") or "webchat"
    customer_id  = contact.get("customer_id") or session_id
    started_at   = contact.get("started_at") or now_iso
    queued_at_ms = int(contact.get("queued_at_ms") or 0)
    wait_ms      = max(now_ms - queued_at_ms, 0) if queued_at_ms else 0
    sla_target   = await _pool_sla_target(redis_client, tenant_id, pool_id)

    # 1. Markers FIRST — bridge re-entry must not overwrite this close.
    try:
        await redis_client.setex(
            f"session:{session_id}:closed", 604_800, "max_wait_exceeded"
        )
        await redis_client.setex(
            f"session:{session_id}:contact_close_fired", 604_800, "1"
        )
    except Exception as exc:
        logger.warning("queue timeout: could not set close markers session=%s — %s",
                       session_id, exc)

    # 2/3. Queue agent signal or synthetic ledger segment.
    queue_agent_active = False
    try:
        queue_agent_active = bool(
            await redis_client.exists(f"queue:agent_active:{session_id}")
        )
    except Exception:
        pass
    if queue_agent_active:
        # "__queue_timeout__" via menu:result (mesmo padrão do
        # "__agent_available__"): o flow de fila AVISA o cliente via notify
        # (stream canônico — único caminho que renderiza no webchat, que não
        # implementa deliver_text) e completa. O outcome do segmento vira
        # "abandoned" pelo override do bridge (marcador closed já setado).
        try:
            result_key = f"menu:result:{session_id}"
            await redis_client.lpush(result_key, "__queue_timeout__")
            await redis_client.expire(result_key, 300)
        except Exception as exc:
            logger.warning("queue timeout: could not signal queue agent session=%s — %s",
                           session_id, exc)
    else:
        # Mute queue — no real segment exists; emit a synthetic one so the
        # contact ledger (segments) still records the wait window.
        try:
            joined_iso = (
                datetime.fromtimestamp(queued_at_ms / 1000, tz=timezone.utc).isoformat()
                if queued_at_ms else now_iso
            )
            await producer.send("conversations.participants", value={
                "event_id":       str(uuid.uuid4()),
                "type":           "participant_left",
                "session_id":     session_id,
                "tenant_id":      tenant_id,
                "segment_id":     str(uuid.uuid4()),
                "participant_id": f"queue-{session_id}",
                "pool_id":        pool_id,
                "agent_type_id":  "system",
                "agent_type":     "system",
                "role":           "queue",
                "sequence_index": 0,
                "joined_at":      joined_iso,
                "timestamp":      now_iso,
                "duration_ms":    wait_ms,
                "outcome":        "abandoned",
                "close_reason":   "max_wait_exceeded",
            })
        except Exception as exc:
            logger.error("queue timeout: failed to publish synthetic queue segment "
                         "session=%s — %s", session_id, exc)

    # 4. Close the customer connection. Attended queue: o aviso vem do flow
    # (notify → stream) — adia o session.closed por um grace para a mensagem
    # renderizar antes do WS fechar. Mute queue: render v2 — farewell_text no
    # próprio session.closed (adapter renderiza antes do close, sem corrida).
    try:
        contact_id_raw = await redis_client.get(f"session:{session_id}:contact_id")
        contact_id = contact_id_raw or customer_id

        close_payload = {
            "type":       "session.closed",
            "contact_id": contact_id,
            "session_id": session_id,
            "channel":    channel,
            "reason":     "agent_done",   # gateway transport Literal — analytics skips it
        }

        if queue_agent_active:
            grace_s = getattr(settings, "queue_timeout_close_grace_s", 4)

            async def _delayed_close() -> None:
                try:
                    await asyncio.sleep(grace_s)
                    await producer.send(settings.kafka_topic_outbound, value=close_payload)
                except Exception as exc:
                    logger.warning(
                        "queue timeout: delayed close failed session=%s — %s",
                        session_id, exc,
                    )

            asyncio.create_task(_delayed_close())
        else:
            close_payload["farewell_text"] = routing_config.get("msg_queue_timeout")
            await producer.send(settings.kafka_topic_outbound, value=close_payload)
    except Exception as exc:
        logger.warning("queue timeout: failed to publish outbound close session=%s — %s",
                       session_id, exc)

    # 5. Authoritative sessions close row.
    try:
        await producer.send("conversations.events", value={
            "event_type":   "contact_closed",
            "session_id":   session_id,
            "tenant_id":    tenant_id,
            "reason":       "max_wait_exceeded",  # not customer_side → bridge ignores
            "close_reason": "max_wait_exceeded",  # business domain
            "outcome":      "abandoned",
            "pool_id":      pool_id,
            "channel":      channel,
            "customer_id":  customer_id,
            "started_at":   started_at,
            "ended_at":     now_iso,
            "sla_target_ms": sla_target,
            "source":       "routing_engine",
        })
    except Exception as exc:
        logger.error("queue timeout: failed to publish contact_closed session=%s — %s",
                     session_id, exc)

    logger.warning(
        "QUEUE TIMEOUT: session=%s tenant=%s pool=%s wait_ms=%d queue_agent=%s",
        session_id, tenant_id, pool_id, wait_ms, queue_agent_active,
    )


async def _emit_no_resource_drop(
    redis_client: aioredis.Redis,
    producer:     AIOKafkaProducer,
    settings,
    event:        ConversationInboundEvent,
) -> None:
    """
    Fase E (queue-attended-model): graceful drop — degenerate case where the
    contact cannot even be enqueued (no pool_id). The fallback chain ends here:
    notify the customer + close with close_reason=no_resource. Distinct from
    the door outage (outcome=outage, admission rejection): this is a broken
    journey — outcome derives from the last primary segment when one exists
    (marker session:{id}:last_outcome), else "failed".
    """
    session_id = event.session_id
    now_iso    = datetime.now(timezone.utc).isoformat()

    # Markers — block bridge re-close (same pattern as _emit_outage).
    try:
        await redis_client.setex(f"session:{session_id}:closed", 604_800, "no_resource")
        await redis_client.setex(
            f"session:{session_id}:contact_close_fired", 604_800, "1"
        )
    except Exception as exc:
        logger.warning("no-resource drop: could not set close markers session=%s — %s",
                       session_id, exc)

    # Outcome: respect the last primary segment when the session was served before.
    outcome = "failed"
    try:
        raw_lo = await redis_client.get(f"session:{session_id}:last_outcome")
        if raw_lo:
            _lo = json.loads(raw_lo if isinstance(raw_lo, str) else raw_lo.decode())
            outcome = _lo.get("outcome") or "failed"
    except Exception:
        pass

    # Notify + close the customer connection (render v2: farewell no close).
    try:
        contact_id_raw = await redis_client.get(f"session:{session_id}:contact_id")
        contact_id = contact_id_raw or event.customer_id or session_id
        await producer.send(settings.kafka_topic_outbound, value={
            "type":       "session.closed",
            "contact_id": contact_id,
            "session_id": session_id,
            "channel":    event.channel,
            "reason":     "agent_done",
            "farewell_text": routing_config.get("msg_no_resource"),
        })
    except Exception as exc:
        logger.warning("no-resource drop: failed to publish outbound session=%s — %s",
                       session_id, exc)

    # Authoritative sessions close row.
    try:
        await producer.send("conversations.events", value={
            "event_type":   "contact_closed",
            "session_id":   session_id,
            "tenant_id":    event.tenant_id,
            "reason":       "no_resource",   # not customer_side → bridge ignores
            "close_reason": "no_resource",
            "outcome":      outcome,
            "pool_id":      "",
            "channel":      event.channel,
            "customer_id":  event.customer_id,
            "started_at":   event.started_at or now_iso,
            "ended_at":     now_iso,
            "source":       "routing_engine",
        })
    except Exception as exc:
        logger.error("no-resource drop: failed to publish contact_closed session=%s — %s",
                     session_id, exc)

    logger.warning(
        "NO-RESOURCE DROP: session=%s tenant=%s channel=%s outcome=%s",
        session_id, event.tenant_id, event.channel, outcome,
    )


async def _admission_reconciler(admission: "AdmissionController") -> None:
    """Periodic release of admission slots held by closed sessions (Fase B)."""
    while True:
        try:
            await asyncio.sleep(60)
            await admission.reconcile()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("admission reconciler error — %s", exc)


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
        # Fase E (queue-attended-model): graceful drop — sem pool não há fila
        # nem agente de fila possível. Drop é último recurso (cadeia de
        # fallback): notify + close com close_reason=no_resource, em vez de
        # deixar a sessão muda eterna (fechar-sempre).
        logger.warning(
            "Cannot enqueue: no pool_id in event for session=%s — graceful drop",
            event.session_id,
        )
        await _emit_no_resource_drop(redis_client, producer, settings, event)
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

    # Fase C (queue-attended-model): write session.queue.* to ContextStore so
    # the queue-treatment skill-flow can reference @ctx.session.queue.position /
    # @ctx.session.queue.eta_ms. Runs on EVERY enqueue attempt (not just the
    # first): drain re-attempts re-enter here and refresh the position.
    asyncio.create_task(
        _write_queue_context(
            redis_client, event.tenant_id, event.session_id, pool_id,
            instance_registry,
        )
    )

    # Notify customer via conversations.outbound so channel-gateway delivers
    # a "waiting" message to the customer WebSocket while they're in queue.
    # Only send on first enqueue — suppress on periodic drain re-attempts to
    # avoid spamming the customer with repeated "waiting" messages.
    if not newly_added:
        return
    # Render v2 (queue-attended-model): com o webchat entregando mensagens de
    # sistema via WS, o aviso de espera duplicaria a saudação do agente de fila.
    # Suprimir quando o pool tem queue_config (fila atendida — o flow fala);
    # fila muda mantém o aviso (única resposta que o cliente recebe).
    # Nota: pool sem queue_config que cai no default do tenant ainda duplica —
    # o routing não enxerga o Config API; aceito (configurar queue_config no pool).
    try:
        raw_cfg = await redis_client.get(f"{event.tenant_id}:pool_config:{pool_id}")
        if raw_cfg and (json.loads(raw_cfg) or {}).get("queue_config"):
            logger.debug(
                "waiting message suppressed (attended queue): session=%s pool=%s",
                event.session_id, pool_id,
            )
            return
    except Exception:
        pass
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
                    "text": routing_config.get("msg_queue_waiting"),
                },
                "text":      routing_config.get("msg_queue_waiting"),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception as exc:
        logger.warning(
            "Could not send waiting notification to customer: session=%s — %s",
            event.session_id, exc,
        )


async def _write_queue_context(
    redis_client:      aioredis.Redis,
    tenant_id:         str,
    session_id:        str,
    pool_id:           str,
    instance_registry: InstanceRegistry,
    ttl_seconds:       int = 86_400,
) -> None:
    """
    Fase C (queue-attended-model): writes session.queue.position and
    session.queue.eta_ms to the ContextStore hash while the contact waits.

    position = queue length AFTER this contact was added (it enters at the
    tail, so length == its 1-based position). eta_ms mirrors the estimate in
    Router._publish_queue_position: position × (sla_target_ms × 0.7), with
    sla_target_ms read from the routing engine's own pool_config cache.

    Refreshed on every enqueue attempt (drain re-attempts included), so the
    queue skill-flow always reads a current-ish position. Fire-and-forget.
    """
    try:
        ctx_key = f"{tenant_id}:ctx:{session_id}"
        now_str = datetime.now(timezone.utc).isoformat()

        position = await instance_registry.get_queue_length(tenant_id, pool_id)
        position = max(int(position or 0), 1)

        sla_target_ms = 0
        raw = await redis_client.get(f"{tenant_id}:pool_config:{pool_id}")
        if raw:
            sla_target_ms = int(json.loads(raw).get("sla_target_ms", 0) or 0)
        avg_handle_ms = int(sla_target_ms * 0.7)

        def _entry(value: object) -> str:
            return json.dumps({
                "value":      value,
                "confidence": 1.0,
                "source":     "routing_engine",
                "visibility": "agents_only",
                "updated_at": now_str,
            })

        mapping: dict[str, str] = {
            "session.queue.position": _entry(position),
        }
        if avg_handle_ms > 0:
            mapping["session.queue.eta_ms"] = _entry(position * avg_handle_ms)

        await redis_client.hset(ctx_key, mapping=mapping)
        await redis_client.expire(ctx_key, ttl_seconds, nx=True)

        logger.debug(
            "ContextStore queue context written: tenant=%s session=%s pool=%s position=%d",
            tenant_id, session_id, pool_id, position,
        )
    except Exception as exc:
        logger.warning(
            "Failed to write queue context to ContextStore: session=%s — %s",
            session_id, exc,
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
        channel_types:     list[str]   = []
        mentionable_pools: dict | None = None
        agent_groups:      list[str]   = []
        max_reply_time_ms: int | None  = None
        raw = await redis_client.get(f"{tenant_id}:pool_config:{pool_id}")
        if raw:
            pool_cfg          = json.loads(raw)
            channel_types     = pool_cfg.get("channel_types", [])
            mentionable_pools = pool_cfg.get("mentionable_pools") or None
            agent_groups      = pool_cfg.get("agent_groups") or []
            max_reply_time_ms = pool_cfg.get("max_reply_time_ms") or None

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
        if agent_groups:
            mapping["session.pool.agent_groups"] = _entry(agent_groups)
        if max_reply_time_ms is not None:
            mapping["session.pool.max_reply_time_ms"] = _entry(max_reply_time_ms)

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

                    # ── Fase E: retention bound (max_wait_exceeded) ────────────
                    # Pool-level queue_config.max_wait_s wins; fallback is the
                    # platform default (bounds mute queues too). ZREM-first makes
                    # the expiry race-safe against a concurrent drain.
                    try:
                        max_wait_s = 0
                        raw_cfg = await redis_client.get(
                            f"{tenant_id}:pool_config:{pool_id}"
                        )
                        if raw_cfg:
                            _qc = (json.loads(raw_cfg) or {}).get("queue_config") or {}
                            max_wait_s = int(_qc.get("max_wait_s") or 0)
                        if max_wait_s <= 0:
                            max_wait_s = getattr(
                                settings, "queue_max_wait_default_s", 1800
                            )
                        if max_wait_s > 0:
                            _now_ms = int(
                                datetime.now(timezone.utc).timestamp() * 1000
                            )
                            cutoff  = _now_ms - max_wait_s * 1000
                            expired = await redis_client.zrangebyscore(
                                key, "-inf", cutoff
                            )
                            for exp_sid in expired:
                                removed = await redis_client.zrem(key, exp_sid)
                                if not removed:
                                    continue   # drained concurrently — skip
                                await _emit_queue_timeout(
                                    redis_client, producer, settings,
                                    tenant_id, pool_id, exp_sid, _now_ms,
                                )
                                drained += 1
                    except Exception as exc:
                        logger.warning(
                            "Queue timeout sweep failed pool=%s — %s", pool_id, exc
                        )

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


# ── Fase 2 — occupancy sampler (concurrency peak per minute → pool.occupancy) ──

_OCCUPANCY_TOPIC = "pool.occupancy"


async def _pool_capacity(redis_client: "aioredis.Redis", tenant_id: str, pool_id: str) -> int:
    """Provisioned capacity = sum of max_concurrent across the pool's instances."""
    try:
        iids = await redis_client.sunion(
            f"{tenant_id}:pool:{pool_id}:instances",
            f"{tenant_id}:pool:{pool_id}:busy_instances",
        )
    except Exception:
        return 0
    cap = 0
    for iid in iids:
        raw = await redis_client.get(f"{tenant_id}:instance:{iid}")
        if not raw:
            continue
        try:
            d = json.loads(raw)
            cap += int(d.get("max_concurrent") or d.get("max_concurrent_sessions") or 1)
        except Exception:
            cap += 1
    return cap


async def _flush_occupancy(
    redis_client: "aioredis.Redis",
    producer:     "AIOKafkaProducer",
    minute,
    peaks:        dict,
    total_peaks:  dict,
) -> None:
    """Emit one pool.occupancy event per (tenant, pool) + a per-tenant total."""
    minute_iso = minute.isoformat()
    tenant_cap_total: dict = {}
    for (tenant_id, pool_id), peak in peaks.items():
        cap = await _pool_capacity(redis_client, tenant_id, pool_id)
        tenant_cap_total[tenant_id] = tenant_cap_total.get(tenant_id, 0) + cap
        await producer.send_and_wait(_OCCUPANCY_TOPIC, {
            "tenant_id": tenant_id, "pool_id": pool_id, "minute": minute_iso,
            "peak_concurrency": peak, "provisioned_capacity": cap,
        })
    for tenant_id, tot in total_peaks.items():
        await producer.send_and_wait(_OCCUPANCY_TOPIC, {
            "tenant_id": tenant_id, "pool_id": "__total__", "minute": minute_iso,
            "peak_concurrency": tot, "provisioned_capacity": tenant_cap_total.get(tenant_id, 0),
        })


async def _occupancy_sampler(
    redis_client: "aioredis.Redis",
    producer:     "AIOKafkaProducer",
    settings,
) -> None:
    """
    Samples per-pool concurrency (the existing `active_count` counter) + the
    per-tenant instantaneous total every few seconds, tracks the per-minute peak,
    and flushes the completed minute to Kafka. The live counter is read each
    sample, so the carry-over (sessions spanning minutes) is implicit and a minute
    without events still records the steady-state concurrency.
    """
    interval = getattr(settings, "occupancy_sample_interval_s", 5)
    if interval <= 0:
        return
    await asyncio.sleep(interval)  # let services start

    cur_minute = None
    peaks: dict = {}        # (tenant_id, pool_id) -> peak this minute
    total_peaks: dict = {}  # tenant_id -> peak total this minute

    while True:
        try:
            minute = datetime.now(timezone.utc).replace(second=0, microsecond=0)
            if cur_minute is None:
                cur_minute = minute
            if minute != cur_minute:
                await _flush_occupancy(redis_client, producer, cur_minute, peaks, total_peaks)
                peaks, total_peaks = {}, {}
                cur_minute = minute

            cursor = 0
            tenant_totals: dict = {}
            seen: set = set()
            while True:
                cursor, keys = await redis_client.scan(cursor, match="*:pool:*:instances", count=50)
                for key in keys:
                    parts = key.split(":")
                    if len(parts) < 4 or parts[-1] != "instances" or parts[-3] != "pool":
                        continue
                    tenant_id = parts[0]
                    pool_id   = ":".join(parts[2:-1])
                    if (tenant_id, pool_id) in seen:
                        continue
                    seen.add((tenant_id, pool_id))
                    raw = await redis_client.get(f"{tenant_id}:pool:{pool_id}:active_count")
                    c = max(0, int(raw)) if raw else 0
                    pk = (tenant_id, pool_id)
                    peaks[pk] = max(peaks.get(pk, 0), c)
                    tenant_totals[tenant_id] = tenant_totals.get(tenant_id, 0) + c
                if cursor == 0:
                    break
            for tenant_id, tot in tenant_totals.items():
                total_peaks[tenant_id] = max(total_peaks.get(tenant_id, 0), tot)

        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.warning("Occupancy sampler error: %s", exc)

        await asyncio.sleep(interval)


def main() -> None:
    """Sync entry point for the plughub-routing console script."""
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())


if __name__ == "__main__":
    main()
