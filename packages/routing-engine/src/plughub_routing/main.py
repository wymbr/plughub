"""
main.py
Routing Engine entry point — Kafka consumer + listeners.
Spec: PlugHub v24.0 section 3.3
"""

from __future__ import annotations
import asyncio
import json
import logging
import os
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
from .registry import (
    InstanceRegistry,
    PoolRegistry,
    _pool_peak_cap_key,
    _pool_peak_key,
    minute_bucket,
)
from .router import Router
from .http_api import start_http_api
from .kafka_listener import run_listeners
from .routing_config import routing_config
from . import mute_queue

logger = logging.getLogger("plughub.routing")


async def run() -> None:
    settings = get_settings()

    # Initialise dependencies
    redis_client = aioredis.from_url(settings.redis_url, decode_responses=True)
    http_client  = httpx.AsyncClient()

    instance_registry = InstanceRegistry(redis_client)
    pool_registry     = PoolRegistry(redis_client)
    router            = Router(instance_registry, pool_registry)
    # Admissão de sessão. Fatia 3 (2026-08-02): o pote misto
    # (`max_concurrent_sessions`) saiu; sobrou o gate por TIPO (`kind:ai ≤ C_ai`).
    # `pool_registry` deixou de ser dependência — servia só para somar reservas.
    admission         = AdmissionController(redis_client)

    # Pre-load routing namespace from Config API so first routing call already
    # has up-to-date SLA/scoring values (performance_score_weight, etc.).
    # Failure is non-fatal — RoutingConfigCache falls back to built-in defaults.
    routing_config.configure_tenant(settings.tenant_id)
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

    # F2a: o Router publica conversations.routed em work_task_claim (pull) e
    # queue.position_updated nos contatos enfileirados — ambos via self._producer.
    # O Router foi criado antes do producer (linha ~41), então injeta-se agora.
    router._producer = producer

    # F2a-1: API HTTP do dispatch pull (claim/release). O Routing Engine continua o
    # único árbitro — ZREM/claim/mark_busy/lease/routed acontecem DENTRO dele; a
    # Console/mcp-server só solicita. (work_queue_list é Redis-direta no mcp-server.)
    http_api_runner = await start_http_api(
        router, int(os.getenv("ROUTING_HTTP_PORT", "3550"))
    )

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
            # Fila de sistema (Fase A): drain só re-publica unadmitted com headroom
            admission                  = admission,
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
        _periodic_queue_drain(redis_client, producer, settings, admission)
    )

    # Fase 2 — occupancy sampler: samples per-pool concurrency (derivada do semáforo) +
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
        await http_api_runner.cleanup()
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

    # Guard: do not route (and therefore do not re-claim a slot) for sessions
    # that are already closing or closed.  This prevents a race condition where:
    #   1. WS1 closes → _trigger_contact_close sets close_fired + publishes agent_done
    #                  → remove_conversation() libera a vaga e apaga a serving-pool key
    #   2. Browser refresh → WS2 connects with same session_id → publishes new
    #      conversations.inbound → o router reivindica uma vaga de novo (serving key
    #      gone → guard misses)
    #   3. WS2 closes → bridge idempotency guard (close_fired already set) → no agent_done
    #                  → a vaga fica ocupada por uma sessão morta (só o reap a recupera)
    # Checking both keys covers two states: close_fired = bridge initiated close;
    # session:{id}:closed = routing engine confirmed close from contact_closed event.
    #
    # EXCEPTION: conference events (conference_id set) are hook/specialist invitations
    # dispatched by fire_pool_hooks() AFTER the session is already closing (e.g. wrap-up
    # and NPS agents in on_human_end).  These must be allowed through — they are
    # legitimate activations on a closing session.  The router already passes
    # session_id=None to mark_busy() for conference events, so they never touch the
    # serving-pool key; they are safe to route even when session:{id}:closed is set.
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
            _adm_pool = None
            decision  = AdmissionDecision(admitted=True)
        if not decision.admitted:
            # Fatia 3 (2026-08-02): a única rejeição possível é `quota` em pool de IA.
            # O `_try_overflow_enqueue` (overflow p/ fila muda gratuita) foi REMOVIDO
            # com ela: existia porque `C` esgotado derrubava contato em pool HUMANO, e
            # humano deixou de ser gateado por sessão. O próprio overflow já recusava
            # IA de propósito ("capacidade de IA libera em segundos"), então após a
            # remoção do pote misto ele virou um ramo que nenhuma entrada alcança.
            await _emit_outage(event, decision, producer, redis_client)
            return
        # ── Saída de fila: a espera acabou (admitida / agente disponível) ─────
        # Fecha a passagem pela fila com o segmento `role=queue outcome=handoff`
        # — a fonte que o /reports/pools/queue (Fase D) já lê. Desde a D12
        # (2026-08-28) vale para os DOIS tiers, não só a fila muda; quem não
        # passou por fila não tem `first_queued_ms` e sai sem emitir nada.
        try:
            await mute_queue.resolve_queue_exit(
                redis_client, producer, event.tenant_id,
                event.pool_id or "", event.session_id, "handoff",
            )
        except Exception as exc:
            logger.warning(
                "mute queue handoff resolve failed session=%s — %s",
                event.session_id, exc,
            )

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
                event, producer, redis_client, instance_registry, now_ms, settings,
                admission=admission,
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


# `_try_overflow_enqueue` REMOVIDA (fatia 3, 2026-08-02). Acomodava na fila muda
# gratuita o contato de pool HUMANO recusado por `C` esgotado — e o único jeito de um
# pool humano ser recusado era o pote misto, que saiu. A função já recusava IA de
# propósito ("capacidade de IA libera em segundos; esperar por IA não faz sentido"),
# então sobrou um ramo sem nenhuma entrada possível. Foi ela também que introduzia a
# causa `queue_full` na demanda reprimida — causa que agora não tem produtor.
#
# A FILA MUDA CONTINUA EXISTINDO, por outro motivo (pool sem `queue_config`):
# `_persist_queued_contact` decide `attended` lendo a config do pool. O que morreu foi
# só o gatilho "overflow de C", junto com o parâmetro `force_mute` que o servia.


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
         resources; close_reason carries the cause. Fatia 3 (2026-08-02): a causa
         é sempre `quota` (teto de IA) — `reservation_full`/`shared_full`/`queue_full`
         saíram com o pote misto e o overflow, e não têm mais produtor.
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
            # Fila de sistema: causa queue_full tem mensagem própria (fila cheia
            # ≠ sem atendentes — orienta o cliente a re-tentar mais tarde).
            "farewell_text": routing_config.get(
                "msg_queue_full" if decision.cause == "queue_full" else "msg_outage_rejection"
            ),
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

    # Limpa o estado de fila SEM emitir segmento — este caminho emite o seu
    # próprio sintético (passo 3).
    # ⚠️ LACUNA NOMEADA (D12, 2026-08-28): o emissor do passo 3 só cobre o tier
    # MUDO, então `max_wait_exceeded` na fila ATENDIDA continua sem segmento de
    # espera. Unificar os dois emissores é fatia à parte — trocar para
    # `emit_segment=True` aqui, sem remover o passo 3, produziria emissão DUPLA
    # no mesmo relatório que este arco existe para consertar.
    try:
        await mute_queue.resolve_queue_exit(
            redis_client, producer, tenant_id, pool_id, session_id,
            "abandoned", emit_segment=False,
        )
    except Exception:
        pass

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
    admission:         "AdmissionController | None" = None,
) -> None:
    """
    Stores contact in the pool queue sorted set and notifies the customer.
    Full original event is preserved so it can be re-published verbatim when
    an agent becomes available (drain-on-ready).

    Fila de sistema (system-queue.md Fase A): quando a fila é MUDA (**pool sem
    `queue_config`** — única origem desde a fatia 3), a sessão é isenta de C
    (admission.release) e marcada em {t}:queue:unadmitted; a espera real é
    preservada através de re-enfileiramentos (first_queued NX vira o score).

    O parâmetro `force_mute` saiu com o `_try_overflow_enqueue` (fatia 3): era o único
    chamador, e o overflow que ele servia ficou sem entrada possível quando o pote misto
    deixou de recusar sessão humana.
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

    # Tier da fila: atendida (`queue_config` no pool) × muda (sem).
    attended     = False
    mute_requeue = False
    try:
        raw_cfg = await redis_client.get(f"{event.tenant_id}:pool_config:{pool_id}")
        if raw_cfg and (json.loads(raw_cfg) or {}).get("queue_config"):
            attended = True
    except Exception:
        pass

    if not attended:
        # Canal não aceita fila muda (max_wait 0, ex. voice) → encerra gracioso
        # imediatamente (max_wait_exceeded com espera zero), nunca dead air.
        if mute_queue.channel_max_wait_s(settings, event.channel) <= 0:
            logger.warning(
                "mute queue not allowed for channel=%s — immediate graceful close "
                "session=%s pool=%s", event.channel, event.session_id, pool_id,
            )
            await _emit_queue_timeout(
                redis_client, producer, settings,
                event.tenant_id, pool_id, event.session_id, now_ms,
            )
            return
        # Isenção de C (tier gratuito): libera os slots de admissão (no-op no
        # overflow — nunca foi admitida) e marca no buffer total. O score do
        # ZSET preserva o PRIMEIRO enqueue: re-enfileiramentos (re-admissão
        # negada no drain) não resetam posição nem relógio de espera.
        if admission is not None:
            try:
                await admission.release(event.tenant_id, event.session_id)
            except Exception as exc:
                logger.warning(
                    "mute queue: admission release failed session=%s — %s",
                    event.session_id, exc,
                )
        first_ms = await mute_queue.mark_mute_queued(
            redis_client, event.tenant_id, event.session_id, now_ms
        )
        mute_requeue = first_ms < now_ms   # re-enfileiramento: não re-avisa o cliente
        now_ms = first_ms

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

    # `queue.position_updated` → Kafka (analytics `queue_events`; futuros
    # subscribers de canal). Publicado AQUI, depois do `add_queued_contact`, pela
    # mesma razão do ContextStore acima: antes do enqueue a fila não contém esta
    # sessão e a posição sai 0. (Era o defeito do Router._publish_queue_position,
    # que rodava concorrente ao enqueue — ver CHANGELOG.)
    asyncio.create_task(
        _publish_queue_position(
            producer, redis_client, event, pool_id, instance_registry, settings,
        )
    )

    # Notify customer via conversations.outbound so channel-gateway delivers
    # a "waiting" message to the customer WebSocket while they're in queue.
    # Only send on first enqueue — suppress on periodic drain re-attempts to
    # avoid spamming the customer with repeated "waiting" messages.
    if not newly_added:
        return
    # Fila de sistema (Fase A): re-enfileiramento (re-admissão negada no drain)
    # re-entra no ZSET como "novo", mas o cliente JÁ recebeu o aviso na primeira
    # espera — não spamar (dedupe pela chave first_queued).
    if mute_requeue:
        return
    # Render v2 (queue-attended-model): com o webchat entregando mensagens de
    # sistema via WS, o aviso de espera duplicaria a saudação do agente de fila.
    # Suprimir quando a fila é atendida (o flow fala); fila muda — inclusive
    # overflow — mantém o aviso (única resposta que o cliente recebe).
    if attended:
        logger.debug(
            "waiting message suppressed (attended queue): session=%s pool=%s",
            event.session_id, pool_id,
        )
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


async def _publish_queue_position(
    producer:          AIOKafkaProducer,
    redis_client:      aioredis.Redis,
    event:             ConversationInboundEvent,
    pool_id:           str,
    instance_registry: InstanceRegistry,
    settings,
) -> None:
    """
    Publica `queue.position_updated` (schema `QueuePositionUpdatedEventSchema`).
    Chamado APÓS a persistência na fila — ver `_queue_position_and_eta`.

    `queue_position` = posição deste contato · `queue_length` = tamanho da fila:
    dois fatos distintos, ambos no payload (o consumer de analytics grava a posição
    em `queue_events.queue_position`). Fire-and-forget: falha logada, nunca propaga.

    NOTE: o producer tem `value_serializer=json.dumps().encode` — passe dict, NUNCA
    bytes (o duplo-encode era o bug que mantinha `queue_events` vazia).
    """
    try:
        position, eta_ms, sla_target_ms, queue_length = await _queue_position_and_eta(
            redis_client, event.tenant_id, event.session_id, pool_id, instance_registry,
        )
        # F5 — `available_agents` REMOVIDO do payload (§3.1 do desenho de capacidade
        # compartilhada). Vinha de `get_available_count` = `SCARD(pool:instances)`:
        # contagem de PERTENCIMENTO, o modelo abandonado quando `max_concurrent > 1`
        # passou a existir. Três defeitos empilhados, e o pior não era o viés:
        #   · modelo errado — conta instância lotada como disponível;
        #   · valor AMBÍGUO — os 126 registros com valor 1 (de 142 não-nulos em 2 meses)
        #     podiam significar filtro de canal, pool `dispatch_mode: pull` (onde fila é
        #     o caminho normal), ou o defeito. Viés se corrige; ambiguidade não;
        #   · ausência disfarçada de zero — o outro produtor de `queue_events` escrevia
        #     `None` hardcoded (77% de nulos), e o leitor convertia para 0.
        # Não havia o que corrigir, só o que REDEFINIR — e redefinir não backfilla.
        # Substituto honesto, se a série for desejada: amostragem por relógio da
        # ocupação do rollup de tenant (`{t}:capacity:snapshot`), que é deduplicado.
        await producer.send(
            settings.kafka_topic_queue_positions,
            value={
                "event":              "queue.position_updated",
                "tenant_id":          event.tenant_id,
                "session_id":         event.session_id,
                "pool_id":            pool_id,
                "queue_position":     position,
                "queue_length":       queue_length,
                "estimated_wait_ms":  eta_ms,
                "sla_target_ms":      sla_target_ms,
                "published_at":       datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception as exc:
        logger.warning(
            "Failed to publish queue.position_updated for session %s: %s",
            event.session_id, exc,
        )


async def _queue_position_and_eta(
    redis_client:      aioredis.Redis,
    tenant_id:         str,
    session_id:        str,
    pool_id:           str,
    instance_registry: InstanceRegistry,
) -> tuple[int, int, int, int]:
    """
    FONTE ÚNICA da posição na fila. Retorna `(position, eta_ms, sla_target_ms,
    queue_length)`.

    `position` = posição 1-based DESTE contato, por `ZRANK` no ZSET da fila (score =
    queued_at_ms, então o rank é a ordem de chegada). Fallback para o comprimento da
    fila quando o rank não resolve (sessão ainda não visível): o contato entra na
    cauda, logo length == sua posição.

    **Só é verdadeiro DEPOIS do `add_queued_contact`** — antes disso o ZSET não contém
    esta sessão e a conta dá 0. Era exatamente esse o defeito do
    `Router._publish_queue_position`, que rodava concorrente ao enqueue e publicava
    `queue_length=0`/`eta=0` (ver CHANGELOG). Por isso o cálculo vive AQUI, no ponto
    pós-persistência, e serve tanto o ContextStore quanto o Kafka — um fato, um lugar.
    """
    position = 0
    try:
        rank = await instance_registry.get_queue_rank(tenant_id, pool_id, session_id)
        if rank is not None:
            position = rank + 1
    except Exception:
        position = 0

    queue_length = int(await instance_registry.get_queue_length(tenant_id, pool_id) or 0)
    if position <= 0:
        position = max(queue_length, 1)

    sla_target_ms = 0
    raw = await redis_client.get(f"{tenant_id}:pool_config:{pool_id}")
    if raw:
        sla_target_ms = int(json.loads(raw).get("sla_target_ms", 0) or 0)
    avg_handle_ms = int(sla_target_ms * 0.7)

    return position, position * avg_handle_ms, sla_target_ms, queue_length


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

    Position/ETA vêm de `_queue_position_and_eta` — a MESMA conta que alimenta o
    `queue.position_updated` no Kafka (antes eram duas implementações do mesmo fato,
    e a do Kafka rodava cedo demais).

    Refreshed on every enqueue attempt (drain re-attempts included), so the
    queue skill-flow always reads a current-ish position. Fire-and-forget.
    """
    try:
        ctx_key = f"{tenant_id}:ctx:{session_id}"
        now_str = datetime.now(timezone.utc).isoformat()

        position, eta_ms, _sla, _qlen = await _queue_position_and_eta(
            redis_client, tenant_id, session_id, pool_id, instance_registry,
        )
        avg_handle_ms = eta_ms // position if position else 0

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
            mapping["session.queue.eta_ms"] = _entry(eta_ms)

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
    @ctx.session.pool.mentionable_pools, @ctx.session.pool.max_reply_time_ms, and
    @ctx.session.pool.llm_account_ids without querying the agent-registry.

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
        llm_account_ids:   list[str]   = []
        raw = await redis_client.get(f"{tenant_id}:pool_config:{pool_id}")
        if raw:
            pool_cfg          = json.loads(raw)
            channel_types     = pool_cfg.get("channel_types", [])
            mentionable_pools = pool_cfg.get("mentionable_pools") or None
            agent_groups      = pool_cfg.get("agent_groups") or []
            max_reply_time_ms = pool_cfg.get("max_reply_time_ms") or None
            llm_account_ids   = pool_cfg.get("llm_account_ids") or []

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
        if llm_account_ids:
            mapping["session.pool.llm_account_ids"] = _entry(llm_account_ids)

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
    admission:    "AdmissionController | None" = None,
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
                    _pool_cfg: dict = {}
                    try:
                        attended_wait_s = 0
                        raw_cfg = await redis_client.get(
                            f"{tenant_id}:pool_config:{pool_id}"
                        )
                        if raw_cfg:
                            _pool_cfg = json.loads(raw_cfg) or {}
                            # Frente 1: pools pull não são auto-drenados (o agente
                            # puxa via work_task_claim) — pula o pool inteiro neste ciclo.
                            #
                            # O `continue` fica ANTES da varredura de propósito, e não
                            # é lugar errado (avaliado 2026-08-05, TODO lacuna 3). Item
                            # de fila pull JÁ TEM teto: o `timeout_hours` do delegate,
                            # via `work_task_expire` — que é o árbitro e trata
                            # nomeadamente o nunca-reivindicado (`router.py` §1107-1112),
                            # apagando a lease e devolvendo a VAGA.
                            #
                            # Deixar a varredura alcançar pool pull faria três estragos:
                            #   (a) `_emit_queue_timeout` fecha um CONTATO — cortesia ao
                            #       cliente, `session.closed`, `outcome=abandoned` e
                            #       segmento sintético `role=queue`. Num item de wrap-up
                            #       ou aprovação não há cliente: seria contato abandonado
                            #       falso no ledger;
                            #   (b) sem `queue_config.max_wait_s` o pool cai no ramo da
                            #       fila muda e herda `queue_max_wait_default_s` (1800 s)
                            #       — teto que ninguém configurou para itens de trabalho;
                            #   (c) duas autoridades de expiração sobre o mesmo item, e
                            #       esta não apaga o ledger `work_task` nem devolve vaga.
                            #
                            # Se um dia a fila pull precisar de teto PRÓPRIO, ele é de
                            # visibilidade/SLA (o item aparece há tempo demais na inbox),
                            # não de abandono de contato — e o produtor seria o árbitro,
                            # não esta varredura.
                            if _pool_cfg.get("dispatch_mode") == "pull":
                                continue
                            _qc = _pool_cfg.get("queue_config") or {}
                            attended_wait_s = int(_qc.get("max_wait_s") or 0)
                        _now_ms = int(
                            datetime.now(timezone.utc).timestamp() * 1000
                        )
                        if attended_wait_s > 0:
                            # Fila ATENDIDA: teto único do pool (comportamento Fase E).
                            cutoff  = _now_ms - attended_wait_s * 1000
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
                        else:
                            # Fila MUDA (system-queue.md): teto POR CANAL.
                            # Varre candidatos acima do menor teto configurado e
                            # refina por entrada (canal está no contact JSON).
                            default_s = int(getattr(settings, "queue_max_wait_default_s", 1800))
                            by_ch     = routing_config.get("queue_max_wait_by_channel") or {}
                            positive  = [int(v) for v in by_ch.values()
                                         if isinstance(v, (int, float)) and int(v) > 0]
                            min_wait  = min(positive + [default_s])
                            cutoff    = _now_ms - min_wait * 1000
                            for exp_sid in await redis_client.zrangebyscore(key, "-inf", cutoff):
                                score = await redis_client.zscore(key, exp_sid)
                                if score is None:
                                    continue   # drained concurrently
                                channel = ""
                                raw_c = await redis_client.get(
                                    f"{tenant_id}:queue_contact:{exp_sid}"
                                )
                                if raw_c:
                                    try:
                                        channel = (json.loads(raw_c) or {}).get("channel") or ""
                                    except Exception:
                                        pass
                                limit_s = mute_queue.channel_max_wait_s(settings, channel)
                                if limit_s > 0 and (_now_ms - int(score)) < limit_s * 1000:
                                    continue   # dentro do teto do canal
                                removed = await redis_client.zrem(key, exp_sid)
                                if not removed:
                                    continue
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
                        # Cliente desistiu esperando → segmento de abandono
                        # (ledger Fase D). D12: vale nos dois tiers.
                        try:
                            await mute_queue.resolve_queue_exit(
                                redis_client, producer, tenant_id, pool_id,
                                session_id, "abandoned",
                            )
                        except Exception:
                            pass
                        logger.info(
                            "Periodic drain: skipped closed session=%s pool=%s reason=%s",
                            session_id, pool_id,
                            closed_marker if isinstance(closed_marker, str) else closed_marker.decode(),
                        )
                        continue

                    # Fila de sistema (Fase A): sessão NÃO-ADMITIDA (fila muda/
                    # overflow) só é re-publicada com vaga no CONTRATO — sem
                    # isso o ciclo rejeita→re-enfileira a cada 5s (churn) e o
                    # cliente recebe avisos repetidos. Agente pronto + contrato
                    # cheio = continua esperando.
                    if admission is not None:
                        try:
                            unadm = await redis_client.sismember(
                                mute_queue.unadmitted_key(tenant_id), session_id
                            )
                            # `session_reservation` saiu do parâmetro na fatia 3 junto
                            # com os baldes reservados — só o teto de IA é consultável.
                            if unadm and not await admission.has_headroom(
                                tenant_id, pool_id,
                                agent_kind=_pool_cfg.get("agent_kind"),
                            ):
                                continue
                        except Exception:
                            pass   # fail-open: segue o fluxo normal

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


# ── Fase 2 — occupancy FLUSHER (concurrency peak per minute → pool.occupancy) ──
#
# **P1 (2026-08-02): o pico por pool deixou de ser AMOSTRADO.** Pico é o máximo de uma
# função escada, e qualquer intervalo de amostra pode cair inteiro entre duas subidas —
# não é questão de escolher um intervalo menor. O valor passou a ser gravado nas
# TRANSIÇÕES (watermark `{t}:pool:{p}:peak:{minuto}`, ver `registry.record_pool_peak`),
# e este laço só (a) lê e publica o minuto que fechou, (b) semeia o bucket novo com a
# ocupação corrente — a carga carregada, que não tem transição para gravá-la.
#
# O que AINDA é amostrado, de propósito:
#   · `__total__` do tenant — `max` de SOMAS ≠ soma de `max`, então o watermark por
#     pool não o produz (comprovado na série de 2026-08-02: quatro pools com pico 1 no
#     mesmo minuto e `__total__` 2, porque os picos foram em instantes diferentes).
#     Fica exato no P2 (ZSET + contador reconciliado).
#   · agregados de admissão (item 7b) — outra grandeza, com outras chaves (baldes de
#     `C`), fora do semáforo. Não é dívida deste arco.

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


async def _read_pool_watermarks(
    redis_client: "aioredis.Redis",
    pools:        set,
    minute,
) -> tuple[dict, dict]:
    """Lê o watermark do minuto FECHADO: `{(tenant, pool): pico}` + `{…: capacidade}`.

    A capacidade vem da chave-irmã, gravada no MESMO instante do pico (achado 1 de
    2026-08-02): antes, `_pool_capacity` era consultada na virada do minuto enquanto o
    pico vinha do minuto que passou, e a série registrou `peak 1 / provisioned 0` —
    impossível por construção, com `headroom`/`utilization` derivados de dois momentos
    diferentes. Sem a chave-irmã, `None`: o chamador degrada para a leitura ao vivo
    **e loga**, para que a linha enviesada não se confunda com uma medida boa.

    Watermark AUSENTE é registrado como pico 0 e logado. Desde o **seed por primeira
    vista** (2026-08-02) a única causa legítima que sobra é o minuto do BOOT: o seed e a
    união `seen_pools` passaram a ter o mesmo gatilho, então pool que nasce no meio do
    minuto já entra semeado. Antes disso, todo login de humano produzia uma rajada de
    AUSENTE (um por pool do agente) e o log tinha uma desculpa permanente — o que é
    equivalente a não ter alarme. Publicar 0 mantém a série sem buraco; o log é o que
    impede que ele passe por medição.
    """
    bucket = minute_bucket(minute)
    peaks: dict = {}
    caps:  dict = {}
    for (tenant_id, pool_id) in pools:
        try:
            raw_peak = await redis_client.get(
                _pool_peak_key(tenant_id, pool_id, bucket)
            )
            raw_cap = await redis_client.get(
                _pool_peak_cap_key(tenant_id, pool_id, bucket)
            )
        except Exception as exc:
            logger.warning(
                "watermark: leitura falhou tenant=%s pool=%s bucket=%s — %s",
                tenant_id, pool_id, bucket, exc,
            )
            raw_peak = raw_cap = None
        if raw_peak is None:
            logger.info(
                "watermark AUSENTE tenant=%s pool=%s bucket=%s — publicando 0. "
                "Esperado só quando o seed da virada não rodou (boot no meio do "
                "minuto); recorrente significa que o flusher não está semeando.",
                tenant_id, pool_id, bucket,
            )
        peaks[(tenant_id, pool_id)] = int(raw_peak) if raw_peak is not None else 0
        caps[(tenant_id, pool_id)]  = int(raw_cap) if raw_cap is not None else None
    return peaks, caps


async def _flush_occupancy(
    redis_client: "aioredis.Redis",
    producer:     "AIOKafkaProducer",
    minute,
    peaks:        dict,
    total_peaks:  dict,
    adm_pool_peaks:     dict | None = None,   # (tenant, pool) -> peak admitted
    adm_ai_peaks:       dict | None = None,   # tenant -> peak sessões debitando C_ai
    buffer_peaks:       dict | None = None,   # tenant -> peak fila gratuita
    caps:               dict | None = None,   # (tenant, pool) -> capacidade no pico
    kind_peaks:         dict | None = None,   # (tenant, kind) -> peak `used` do tipo
    kind_caps:          dict | None = None,   # (tenant, kind) -> capacidade no pico
) -> None:
    """
    Emit one pool.occupancy event per (tenant, pool) + per-tenant aggregates.
    Item 7b: cada linha por pool ganha `admitted_peak` (sessões debitando licença
    atribuídas ao pool, via HASH `{t}:admission:ai_pools`), e DUAS linhas agregadas
    espelham o Monitor no histórico:
      __admitted_ai__  peak = sessões debitando C_ai | capacity = C_ai
      __buffer__       peak = fila gratuita usada    | capacity = queue_max_total

    A linha `__reserved__` e a `__shared__` saíram na fatia 3 junto com os baldes que
    mediam (ver o bloco do item 7b, abaixo).

    `caps` (P1) traz a capacidade capturada no instante do pico. Ausente → leitura ao
    vivo, com o viés temporal do achado 1 — logado, nunca silencioso.

    **Defeito C na série — F4c (2026-08-02).** A linha POR POOL continua com a
    capacidade do pool, e está certa: aquele pool alcança mesmo N vagas. O que estava
    errado era o agregado — `__total__.provisioned_capacity` vinha de `Σ` das linhas,
    contando um recurso de 3 vagas em dois pools como 6. Duas correções:

      · `__total__` passa a usar a capacidade DEDUPLICADA (Σ sobre instâncias
        distintas, de todos os tipos), coerente com o seu próprio `peak_concurrency`,
        que já é ocupação de tipos misturados. Serve como número de infra
        ("quantas vagas existem"), NÃO para dimensionar atendimento;
      · linhas novas `__capacity_{kind}__` (human/ai/unknown) trazem a capacidade
        deduplicada POR TIPO — o número de planejamento, que não é derivável do
        `__total__` porque humano e IA não se substituem.

    **Janela de arranque (medida 2026-08-02).** Nos primeiros 1–2 minutos após um
    restart o rollup ainda não foi publicado, e `__total__` cai no `Σ` por pool —
    inflado (362 onde o correto era 356, no demo). A alternativa seria omitir a linha,
    mas isso levaria junto o `peak_concurrency`, que é bom; e publicar capacidade 0
    seria o valor plausível de sempre. Escolha: publicar com log **e um marcador
    consultável** — nesses minutos NÃO existem linhas `__capacity_*`. Logo:

        minuto sem `__capacity_*` ⇒ `__total__.provisioned_capacity` não é confiável

    O marcador vive na própria série, então quem ler o histórico meses depois não
    depende de ter guardado o log. Query de saneamento em `docs/product/
    shared-capacity-pool-as-tag-design.md` §6 (F4c).
    """
    adm_pool_peaks = adm_pool_peaks or {}
    adm_ai_peaks   = adm_ai_peaks or {}
    buffer_peaks   = buffer_peaks or {}
    caps               = caps or {}
    minute_iso = minute.isoformat()
    tenant_cap_total: dict = {}
    tenants: set = set()
    for (tenant_id, pool_id), peak in peaks.items():
        tenants.add(tenant_id)
        cap = caps.get((tenant_id, pool_id))
        if cap is None:
            cap = await _pool_capacity(redis_client, tenant_id, pool_id)
            logger.info(
                "capacidade do pico ausente tenant=%s pool=%s — usando leitura ao "
                "vivo (viés temporal do achado 1: pico e capacidade de instantes "
                "diferentes)", tenant_id, pool_id,
            )
        tenant_cap_total[tenant_id] = tenant_cap_total.get(tenant_id, 0) + cap
        await producer.send_and_wait(_OCCUPANCY_TOPIC, {
            "tenant_id": tenant_id, "pool_id": pool_id, "minute": minute_iso,
            "peak_concurrency": peak, "provisioned_capacity": cap,
            "admitted_peak": adm_pool_peaks.get((tenant_id, pool_id), 0),
        })
    kind_peaks = kind_peaks or {}
    kind_caps  = kind_caps  or {}

    for tenant_id, tot in total_peaks.items():
        tenants.add(tenant_id)
        # F4c — capacidade DEDUPLICADA (Σ sobre instâncias distintas). O `Σ` por pool
        # contava o mesmo recurso uma vez por pool; aqui cada instância entra em
        # exatamente um balde de tipo, então somar os baldes não duplica ninguém.
        dedup = sum(
            c for (t, _k), c in kind_caps.items() if t == tenant_id
        ) if any(t == tenant_id for (t, _k) in kind_caps) else None
        if dedup is None:
            dedup = tenant_cap_total.get(tenant_id, 0)
            logger.info(
                "tenant=%s minuto=%s: sem rollup por tipo — `__total__` publica Σ das "
                "capacidades por pool, que INFLA quando há recurso compartilhado "
                "(defeito C). Valor registrado assim mesmo, para não abrir buraco na série.",
                tenant_id, minute_iso,
            )
        await producer.send_and_wait(_OCCUPANCY_TOPIC, {
            "tenant_id": tenant_id, "pool_id": "__total__", "minute": minute_iso,
            "peak_concurrency": tot, "provisioned_capacity": dedup,
            "admitted_peak": adm_ai_peaks.get(tenant_id, 0),
        })

    # F4c — uma linha por TIPO DE LICENÇA com a capacidade deduplicada. É o número de
    # planejamento, e não é derivável do `__total__`: somar humano com IA responderia
    # "há 356 vagas" para quem precisa saber se há atendente humano.
    for (tenant_id, kind), peak in sorted(kind_peaks.items()):
        tenants.add(tenant_id)
        await producer.send_and_wait(_OCCUPANCY_TOPIC, {
            "tenant_id": tenant_id, "pool_id": f"__capacity_{kind}__",
            "minute": minute_iso,
            "peak_concurrency": peak,
            "provisioned_capacity": kind_caps.get((tenant_id, kind), 0),
            "admitted_peak": 0,
        })

    # ── Item 7b — agregados de admissão (histórico do Monitor) ────────────────
    #
    # **Fatia 3 (2026-08-02) — as linhas foram REAPONTADAS, não renomeadas.**
    #   · `__reserved__` SAIU — sem baldes reservados a linha não tem referente;
    #   · `__shared__`   virou `__admitted_ai__`: o numerador deixou de contar sessão
    #     humana e o denominador passou de `C − Σ reservas` (pote misto, 370) para
    #     `C_ai` (360). Publicar o nome antigo com o limite antigo seria um número
    #     plausível descrevendo um portão que não existe;
    #   · `__buffer__`   FICA — a fila muda tem razão própria (pool sem `queue_config`).
    #
    # Descontinuidade assumida na série: `__admitted_ai__` começa em 2026-08-02 e não é
    # comparável com o histórico de `__shared__`.
    for tenant_id in tenants:
        c_ai = 0
        try:
            raw_ai = await redis_client.get(f"{tenant_id}:quota:capacity:ai_agent")
            if raw_ai:
                c_ai = max(0, int(float(
                    raw_ai.decode() if isinstance(raw_ai, bytes) else raw_ai
                )))
        except Exception:
            pass

        for pool_marker, peak_v, cap_v in (
            ("__admitted_ai__", adm_ai_peaks.get(tenant_id, 0), c_ai),
            ("__buffer__",      buffer_peaks.get(tenant_id, 0), mute_queue.max_queue_total()),
        ):
            await producer.send_and_wait(_OCCUPANCY_TOPIC, {
                "tenant_id": tenant_id, "pool_id": pool_marker, "minute": minute_iso,
                "peak_concurrency": peak_v, "provisioned_capacity": cap_v,
                "admitted_peak": peak_v,
            })


async def _occupancy_sampler(
    redis_client: "aioredis.Redis",
    producer:     "AIOKafkaProducer",
    settings,
) -> None:
    """
    FLUSHER do pico por pool + amostrador do que ainda não é event-driven.

    **P1 — o pico por pool não é mais amostrado.** Ele é escrito nas TRANSIÇÕES
    (`registry.record_pool_peak`, chamado na costura de alocação) e aqui apenas lido e
    publicado no fim do minuto. A diferença é de MÉTODO, não de intervalo: amostrar a
    cada 5 s deixa de fora todo pico que sobe e desce dentro da janela, e reduzir a
    janela não fecha a classe — só a estreita.

    Duas responsabilidades sobram para este laço:

    1. **Virada do minuto** — publica o bucket que fechou e SEMEIA o novo com a
       ocupação corrente. O seed é o que registra carga carregada: um minuto que
       começa alto e só desce (ou sem transição alguma) não tem alocação para gravá-lo,
       e sem semente sairia como zero. O seed simétrico, na LIBERAÇÃO, cobre a janela
       entre a virada do relógio e o despertar deste laço (ver `release_instance`).
    2. **Amostragem do que continua sendo amostra** — `__total__` do tenant (max de
       SOMAS ≠ soma de max; exato só no P2) e os agregados de admissão do item 7b
       (outra grandeza, outras chaves). Ambos declarados, não herdados por inércia.

    **Fatia 2** — a fonte da ocupação deixou de ser o contador
    `{t}:pool:{p}:active_count` (removido: contava por POOL uma capacidade que é do
    RECURSO) e passou a ser `compute_pool_occupancy(...)["used_here"]`, derivado da tag
    do membro do semáforo. Para humano multi-pool o total do tenant deixou de contar o
    mesmo atendimento uma vez por pool. A série antiga e a nova não são comparáveis
    nesse ponto; o degrau na virada é a correção, não uma queda de carga.
    """
    interval = getattr(settings, "occupancy_sample_interval_s", 5)
    if interval <= 0:
        return
    await asyncio.sleep(interval)  # let services start
    _occ_reg = InstanceRegistry(redis_client)

    cur_minute = None
    seen_pools: set = set()       # (tenant_id, pool_id) vistos no minuto corrente
    total_peaks: dict = {}        # tenant_id -> peak total this minute
    adm_pool_peaks: dict = {}     # (tenant_id, pool_id) -> peak admitted (item 7b)
    adm_ai_peaks: dict = {}       # tenant_id -> peak de sessões debitando C_ai
    buffer_peaks: dict = {}       # tenant_id -> peak fila gratuita
    kind_peaks: dict = {}         # (tenant_id, kind) -> peak `used` do tipo (F4c)
    kind_caps: dict = {}          # (tenant_id, kind) -> capacidade NO INSTANTE do pico

    async def _seed_bucket(occ_by_pool: dict, minute) -> None:
        """Carga carregada: `max(bucket novo) := ocupação corrente`, na virada."""
        bucket = minute_bucket(minute)
        for (tenant_id, pool_id), (used, cap) in occ_by_pool.items():
            await _occ_reg.record_pool_peak(
                tenant_id, pool_id, used, cap, bucket=bucket,
            )
        # P2 — o `__total__` tem watermark próprio e precisa do mesmo seed de carga
        # carregada. Vem do contador conferível, não da soma dos pools deste tick.
        for tenant_id in {t for (t, _p) in occ_by_pool}:
            total = await _occ_reg.get_tenant_occupancy(tenant_id)
            if total is not None:
                await _occ_reg.record_pool_peak(
                    tenant_id, "__total__", total, 0,
                    bucket=bucket, write_capacity=False,
                )

    while True:
        try:
            minute = datetime.now(timezone.utc).replace(second=0, microsecond=0)

            # A varredura vem ANTES da virada: o seed do bucket novo precisa da
            # ocupação de AGORA, não da que sobrou do tick anterior.
            cursor = 0
            tenant_totals: dict = {}
            occ_by_pool: dict = {}
            tenants_seen: set = set()
            while True:
                cursor, keys = await redis_client.scan(cursor, match="*:pool:*:instances", count=50)
                for key in keys:
                    parts = key.split(":")
                    if len(parts) < 4 or parts[-1] != "instances" or parts[-3] != "pool":
                        continue
                    tenant_id = parts[0]
                    pool_id   = ":".join(parts[2:-1])
                    if (tenant_id, pool_id) in occ_by_pool:
                        continue
                    tenants_seen.add(tenant_id)
                    # Derivado do semáforo do recurso (fatia 2) — `used_here` é a
                    # projeção pela tag do membro, uma tag por ocupante, logo o
                    # somatório entre pools não conta o mesmo atendimento duas vezes.
                    _occ = await _occ_reg.compute_pool_occupancy(tenant_id, pool_id)
                    c = max(0, int(_occ["used_here"]))
                    occ_by_pool[(tenant_id, pool_id)] = (c, int(_occ["total_capacity"]))
                    tenant_totals[tenant_id] = tenant_totals.get(tenant_id, 0) + c
                if cursor == 0:
                    break

            if cur_minute is None:
                cur_minute = minute
                await _seed_bucket(occ_by_pool, cur_minute)
            elif minute != cur_minute:
                peaks, caps = await _read_pool_watermarks(
                    redis_client, seen_pools, cur_minute
                )
                # P2 — o pico do tenant vem do WATERMARK, não da amostragem. Sem ele o
                # `max` de somas perdia todo pico que subia e descia entre dois ticks,
                # exatamente como acontecia por pool antes do P1. A amostra fica só
                # como fallback, e o fallback se anuncia.
                _bkt = minute_bucket(cur_minute)
                for _t in {t for (t, _p) in seen_pools}:
                    try:
                        _raw = await redis_client.get(_pool_peak_key(_t, "__total__", _bkt))
                    except Exception:
                        _raw = None
                    if _raw is not None:
                        total_peaks[_t] = int(_raw)
                    else:
                        logger.info(
                            "watermark de `__total__` AUSENTE tenant=%s bucket=%s — "
                            "publicando a AMOSTRA do minuto, que perde pico entre ticks. "
                            "Esperado só no minuto do boot.", _t, _bkt,
                        )
                # Reconciliação 1×/min: o contador do total é atalho, o ZSET é a fonte.
                # É esta conferência que separa `{t}:occupancy:total` do `active_count`
                # que este arco removeu — tirá-la devolve o contador àquela condição.
                for _t in {t for (t, _p) in seen_pools}:
                    try:
                        await _occ_reg.reconcile_tenant_occupancy(_t)
                    except Exception as exc:
                        logger.warning(
                            "flusher: reconciliação de ocupação falhou tenant=%s — %s",
                            _t, exc,
                        )
                await _flush_occupancy(
                    redis_client, producer, cur_minute, peaks, total_peaks,
                    adm_pool_peaks, adm_ai_peaks, buffer_peaks,
                    caps=caps, kind_peaks=kind_peaks, kind_caps=kind_caps,
                )
                seen_pools = set()
                total_peaks = {}
                adm_pool_peaks, adm_ai_peaks, buffer_peaks = {}, {}, {}
                kind_peaks, kind_caps = {}, {}
                cur_minute = minute
                await _seed_bucket(occ_by_pool, cur_minute)

            # F4 — mantém o rollup de capacidade fresco para tenant OCIOSO. O gatilho
            # principal é a transição de ocupação (fan-out), mas tenant sem transição
            # nenhuma deixaria o rollup expirar por TTL, e o consumidor leria ausência.
            # Throttled (5 s) — na prática este laço só o renova quando nada mais o fez.
            for tenant_id in tenants_seen:
                try:
                    await _occ_reg.refresh_tenant_capacity(tenant_id)
                    # F4c — amostra a ocupação POR TIPO para a série. Continua sendo
                    # AMOSTRA (o watermark event-driven do P1 é por POOL; ocupação por
                    # tipo é `max` de SOMAS, mesma família do `__total__` que o P2
                    # resolve). A capacidade é capturada quando o pico AVANÇA, não no
                    # flush — a lição do achado 1: `peak > capacity` nasce de medir as
                    # duas grandezas em instantes diferentes.
                    roll = await _occ_reg.get_tenant_capacity(tenant_id)
                    for kind, k in (roll or {}).get("by_kind", {}).items():
                        key  = (tenant_id, kind)
                        used = int(k.get("used") or 0)
                        if used > kind_peaks.get(key, -1):
                            kind_peaks[key] = used
                            kind_caps[key]  = int(k.get("total_capacity") or 0)
                        elif key not in kind_caps:
                            kind_caps[key] = int(k.get("total_capacity") or 0)
                except Exception as exc:
                    logger.warning(
                        "flusher: rollup de capacidade falhou tenant=%s — %s",
                        tenant_id, exc,
                    )

            # A união (não a foto do último tick) decide quais linhas o minuto publica:
            # um pool que existiu no início do minuto e sumiu no fim ainda teve carga.
            #
            # **SEED POR PRIMEIRA VISTA (2026-08-02).** A união e o seed tinham
            # gatilhos DIFERENTES: `_seed_bucket` semeava a foto da VIRADA, mas
            # `seen_pools` cresce durante o minuto inteiro. Pool que aparecia no meio
            # do minuto — que é o que acontece toda vez que um humano faz login, e ele
            # aparece em TODOS os seus pools de uma vez — entrava na união sem
            # watermark, e o flusher publicava 0 para ele com o log "watermark
            # AUSENTE". Zero com cara de medição, justamente na entrada do recurso.
            #
            # Semear na primeira vista fecha o buraco e, de quebra, torna o log um
            # alarme de verdade: com as duas fontes no mesmo gatilho, AUSENTE deixa de
            # ter causa legítima fora do minuto de boot. `record_pool_peak` é max-write,
            # então semear um pool já semeado é no-op — e semear um pool que nasce
            # OCUPADO grava a ocupação real, não o zero.
            new_pools = set(occ_by_pool.keys()) - seen_pools
            if new_pools and cur_minute is not None:
                await _seed_bucket(
                    {k: occ_by_pool[k] for k in new_pools}, cur_minute
                )
            seen_pools |= set(occ_by_pool.keys())
            for tenant_id, tot in tenant_totals.items():
                total_peaks[tenant_id] = max(total_peaks.get(tenant_id, 0), tot)

            # ── Item 7b — amostra a admissão (licença de IA + buffer) ─────────
            # Mesmas chaves que o Monitor lê (HASH de atribuição do 7a) — o
            # histórico espelha o tempo real por construção.
            #
            # Fatia 3: o SET `{t}:admission:shared` e os `…:reserved:{pool}` saíram;
            # `{t}:admission:kind:ai` é o único balde com teto, e o HASH de atribuição
            # virou `{t}:admission:ai_pools`. A soma `reserved + shared` por pool
            # desapareceu junto — havia UMA fonte, não duas.
            for tenant_id in tenants_seen:
                try:
                    ai_hash = await redis_client.hgetall(
                        f"{tenant_id}:admission:ai_pools"
                    )
                    ai_by_pool: dict = {}
                    for p_raw in ai_hash.values():
                        p = p_raw.decode() if isinstance(p_raw, bytes) else str(p_raw)
                        ai_by_pool[p] = ai_by_pool.get(p, 0) + 1
                    ai_used = await redis_client.scard(
                        f"{tenant_id}:admission:kind:ai"
                    )
                    buffer_used = await redis_client.scard(
                        f"{tenant_id}:queue:unadmitted"
                    )

                    for p, admitted in ai_by_pool.items():
                        apk = (tenant_id, p)
                        adm_pool_peaks[apk] = max(adm_pool_peaks.get(apk, 0), admitted)
                    adm_ai_peaks[tenant_id] = max(
                        adm_ai_peaks.get(tenant_id, 0), int(ai_used))
                    buffer_peaks[tenant_id] = max(
                        buffer_peaks.get(tenant_id, 0), int(buffer_used))
                except Exception:
                    pass   # amostra perdida — próximo tick cobre

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
