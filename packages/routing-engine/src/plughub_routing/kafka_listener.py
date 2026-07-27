"""
kafka_listener.py
Kafka consumer that populates the Routing Engine Redis cache.
Spec: PlugHub v24.0 sections 3.3, 4.5

Consumes two topics:

1. agent.registry.events — Agent Registry events (pools, agent types)
   Expected formats:
     { "event": "pool.registered"|"pool.updated", "tenant_id": str, "pool": {...} }
     { "event": "agent_type.registered", "tenant_id": str, "agent_type": {...} }

   Action: updates Redis cache {tenant_id}:pool_config:{pool_id}

2. agent.lifecycle — mcp-server-plughub events (agent_ready, agent_busy, etc.)
   Expected formats:
     { "event": "agent_ready"|"agent_busy"|"agent_paused"|"agent_logout"|"agent_heartbeat"|"agent_done",
       "tenant_id": str, "instance_id": str, "agent_type_id": str,
       "status": str, "current_sessions": int, "pools": [...],
       "max_concurrent_sessions": int,
       "conversation_id": str  (agent_busy and agent_done only) }

   Action: updates {tenant_id}:instance:{instance_id} with TTL 30s
           maintains {tenant_id}:pool:{pool_id}:instances (set of ready instance_ids)
           maintains no-TTL meta and active conversations set (agent_ready/busy/done)
"""

from __future__ import annotations
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import redis.asyncio as aioredis

from .models import AgentInstance, PoolConfig, RoutingExpression
from .registry import InstanceRegistry, PoolRegistry
from .config import get_settings
from .routing_config import routing_config
from . import mute_queue

if TYPE_CHECKING:
    import httpx
    from aiokafka import AIOKafkaProducer
    from .router import Router

logger = logging.getLogger("plughub.routing.kafka_listener")


class ConfigChangedHandler:
    """
    Processes config.changed Kafka events for the routing namespace.

    When the Config API publishes a change to namespace "routing", this handler
    marks the local RoutingConfigCache as stale and schedules a background
    reload so fresh values are picked up without a routing-engine restart.

    Events from other namespaces are silently ignored — each component is
    responsible for the namespaces it cares about.
    """

    def __init__(self, config_api_url: str, http_client: "httpx.AsyncClient") -> None:
        import httpx as _httpx  # local import to keep top-level imports clean
        self._config_api_url = config_api_url
        self._http_client    = http_client

    async def handle(self, event: dict) -> None:
        namespace = event.get("namespace", "")
        if namespace != "routing":
            logger.debug("config.changed ignored: namespace=%s", namespace)
            return

        key       = event.get("key", "<unknown>")
        tenant_id = event.get("tenant_id", "<unknown>")
        operation = event.get("operation", "<unknown>")

        logger.info(
            "config.changed received: namespace=routing key=%s tenant=%s op=%s — invalidating cache",
            key, tenant_id, operation,
        )
        routing_config.invalidate()
        # Reload in background so we don't block the consumer loop.
        asyncio.create_task(
            routing_config.reload(self._config_api_url, self._http_client)
        )


class SessionClosedEventHandler:
    """
    Processes ContactClosedEvent messages from conversations.events.

    On every session close (disconnect, timeout, agent_done) this handler:
      1. Sets session:{session_id}:closed = reason  (TTL 7d) so that
         _drain_queue_for_agent skips re-routing stale sessions.
      2. If the session is still in a pool queue (i.e. the client disconnected
         before an agent was allocated), removes it from the ZSET and deletes
         the stored contact JSON, so orphan sessions never appear in the UI.

    No-op for events other than contact_closed.
    """

    _CLOSED_TTL_S = 7 * 24 * 3600  # 7 days — same as drain_queue comment

    def __init__(self, instance_registry: InstanceRegistry, admission=None) -> None:
        self._instances = instance_registry
        self._admission = admission

    async def handle(self, event: dict) -> None:
        if event.get("event_type") != "contact_closed":
            return

        session_id = event.get("session_id", "")
        tenant_id  = event.get("tenant_id", "")
        reason     = event.get("reason", "unknown")

        if not session_id or not tenant_id:
            return

        # 1. Mark session as closed — _drain_queue_for_agent checks this key
        await self._instances._redis.set(
            f"session:{session_id}:closed",
            reason,
            ex=self._CLOSED_TTL_S,
        )

        # Fila de sistema (Fase A): libera o slot de admissão IMEDIATAMENTE no
        # fechamento (event-driven). Antes a liberação era só do reconciler
        # (~60s) — aceitável quando a admissão era apenas gauge, mas o drain da
        # fila muda depende do headroom: cliente esperava até 60s por uma vaga
        # já livre. Reconciler permanece como backstop (crash/evento perdido).
        if self._admission is not None:
            try:
                await self._admission.release(tenant_id, session_id)
            except Exception as exc:
                logger.warning(
                    "admission eager release failed session=%s — %s (reconciler cobre)",
                    session_id, exc,
                )

        # 2. Remove from queue if still present.
        #    The stored contact JSON ({tenant}:queue_contact:{session_id}) carries
        #    pool_id — use it to target the correct ZSET.
        try:
            raw = await self._instances._redis.get(
                f"{tenant_id}:queue_contact:{session_id}"
            )
            if raw:
                import json as _json
                contact_data = _json.loads(raw)
                pool_id = contact_data.get("pool_id", "")
                if pool_id:
                    await self._instances.remove_queued_contact(
                        tenant_id, pool_id, session_id
                    )
                    logger.info(
                        "Queue cleanup: removed session=%s pool=%s reason=%s",
                        session_id, pool_id, reason,
                    )
        except Exception as exc:
            logger.warning(
                "Queue cleanup: could not remove session=%s from queue: %s",
                session_id, exc,
            )


class RegistryEventHandler:
    """
    Processes Agent Registry events and populates the Redis pool config cache.
    """

    def __init__(self, pool_registry: PoolRegistry) -> None:
        self._pools = pool_registry

    async def handle(self, event: dict) -> None:
        event_type = event.get("event", "")
        tenant_id  = event.get("tenant_id", "")

        if event_type in ("pool.registered", "pool.updated"):
            await self._handle_pool_event(tenant_id, event.get("pool", {}))
        else:
            logger.debug("Registry event ignored: %s", event_type)

    async def _handle_pool_event(self, tenant_id: str, pool_data: dict) -> None:
        if not pool_data or not pool_data.get("pool_id"):
            return
        try:
            expr_data = pool_data.get("routing_expression") or {}
            config = PoolConfig(
                pool_id              = pool_data["pool_id"],
                tenant_id            = tenant_id,
                channel_types        = pool_data.get("channel_types", []),
                sla_target_ms        = pool_data.get("sla_target_ms", 480_000),
                max_reply_time_ms    = pool_data.get("max_reply_time_ms") or None,
                routing_expression   = RoutingExpression(**expr_data),
                is_human_pool        = bool(pool_data.get("supervisor_config")),
                mentionable_pools    = pool_data.get("mentionable_pools") or None,
                agent_groups              = pool_data.get("agent_groups") or [],
                context_visibility        = pool_data.get("context_visibility") or None,
                # Arc 19: webhook pool endpoint skill and capacity ceiling
                webhook_skill_id         = pool_data.get("webhook_skill_id") or None,
                max_concurrent_sessions  = pool_data.get("max_concurrent_sessions") or None,
                # Fase B: hybrid session admission reservation
                session_reservation      = pool_data.get("session_reservation") or None,
                # Fase E: queue treatment passthrough (max_wait_s enforcement)
                queue_config             = pool_data.get("queue_config") or None,
                # Capacity-governance item 2: tipagem do pool (gate por tipo)
                agent_kind               = pool_data.get("agent_kind") or None,
                # Frente 1: modo de despacho da fila (push default | pull)
                dispatch_mode            = pool_data.get("dispatch_mode") or "push",
                # Camada C (detach de hooks): ACW como regra de agent_ready
                acw_gate                 = pool_data.get("acw_gate") or "none",
            )
            await self._pools.save_pool_config(config)
            logger.info(
                "Pool cache updated: tenant=%s pool=%s channels=%s mentionable_pools=%s "
                "agent_groups=%s webhook_skill_id=%s max_concurrent_sessions=%s dispatch_mode=%s",
                tenant_id, config.pool_id, config.channel_types,
                list(config.mentionable_pools.keys()) if config.mentionable_pools else [],
                config.agent_groups,
                config.webhook_skill_id,
                config.max_concurrent_sessions,
                config.dispatch_mode,
            )
        except Exception as exc:
            logger.error("Error processing pool event: %s — %s", pool_data, exc)


class LifecycleEventHandler:
    """
    Processes agent.lifecycle events and maintains instance state in Redis.
    Key: {tenant_id}:instance:{instance_id}  TTL: 30s (spec 4.5).

    When router + producer + pool_registry are provided, automatically drains
    the pool queue when an agent transitions to ready (Scenario 2 — spec 3.3b).
    """

    def __init__(
        self,
        instance_registry:  InstanceRegistry,
        router:             "Router | None"         = None,
        producer:           "AIOKafkaProducer | None" = None,
        pool_registry:      PoolRegistry | None     = None,
        kafka_topic_inbound: str                    = "conversations.inbound",
        admission           = None,   # AdmissionController | None (fila de sistema)
    ) -> None:
        self._instances      = instance_registry
        self._router         = router
        self._producer       = producer
        self._pools          = pool_registry
        self._topic_inbound  = kafka_topic_inbound
        self._admission      = admission

    async def handle(self, event: dict) -> None:
        event_type = event.get("event", "")
        tenant_id  = event.get("tenant_id", "")
        instance_id= event.get("instance_id", "")

        if not tenant_id or not instance_id:
            return

        if event_type == "agent_ready":
            resolved = await self._upsert_instance(
                tenant_id, instance_id, event, event_type
            )
            # F1 — o meta espelha o que o registro EFETIVAMENTE guarda, não o que o
            # evento propôs. Para humano os dois divergem (a identidade do evento é
            # a da conexão que o emitiu), e um meta divergente reaparece como pool
            # errado no `remove_conversation`/`crash_detector`.
            _meta_pools, _meta_type = resolved if resolved else (
                event.get("pools") or [], event.get("agent_type_id", ""),
            )
            await self._instances.update_instance_meta(
                tenant_id, instance_id,
                pools         = _meta_pools,
                agent_type_id = _meta_type,
            )
            # Refresh pool snapshots immediately on agent_ready so Monitor
            # shows the pool from login time, not only after the first routing
            # event triggers write_pool_snapshot().
            #
            # Always do a full write_pool_snapshot() (via _refresh_pool_snapshots
            # with no delta).  Full recount is idempotent: multiple calls give the
            # same correct result.  The reconciliation loop sends agent_ready for
            # ALL AI agents every 15 s — a fast-patch delta approach would compound
            # with each cycle and cause massive overcounting.  Full recount avoids
            # this because it sums actual ready-instance capacity each time.
            #
            # remove_conversation() already patches the snapshot +1 for immediate
            # UI feedback; this full recount confirms (and matches) that value.
            if self._pools:
                asyncio.create_task(
                    self._refresh_pool_snapshots(tenant_id, event.get("pools") or [])
                )
            # Drain queue — if an agent becomes ready and there are contacts
            # waiting in any of its pools, dequeue the highest-priority one
            # and re-publish it to conversations.inbound for re-routing.
            if self._router and self._producer and self._pools:
                asyncio.create_task(
                    self._drain_queue_for_agent(tenant_id, instance_id, event)
                )
        elif event_type in ("agent_busy", "agent_heartbeat"):
            await self._upsert_instance(tenant_id, instance_id, event, event_type)
            if event_type == "agent_busy":
                conversation_id = event.get("conversation_id", "")
                if conversation_id:
                    await self._instances.add_conversation(tenant_id, instance_id, conversation_id)
        elif event_type == "agent_done":
            conversation_id = event.get("conversation_id", "")
            fallback_pools  = event.get("pools") or []
            # Phase 2 (hand-off da vaga): o BRIDGE carimba este flag quando o pool do
            # contato tem wrap-up `side=agent` com `dispatch: inline` seguindo — a vaga
            # é SEGURADA (swap para hold) em vez de liberada, e o auto-claim do wrap-up
            # a herda. O routing NÃO consulta hooks de pool (invariante): a decisão vem
            # pronta no evento. Ausente/false = release normal (retrocompat).
            keep_slot = bool(event.get("keep_slot_for_wrapup"))
            logger.info(
                "agent_done received: tenant=%s instance=%s conv=%s fallback_pools=%s "
                "keep_slot_for_wrapup=%s",
                tenant_id, instance_id, conversation_id, fallback_pools, keep_slot,
            )
            if conversation_id:
                # Pass fallback_pools from the event payload so that human agents
                # (which never publish agent_ready and therefore have no instance_meta)
                # still trigger the pool active_count DECR correctly.
                await self._instances.remove_conversation(
                    tenant_id, instance_id, conversation_id,
                    fallback_pools=fallback_pools,
                    hold_for_wrapup=keep_slot,
                    hold_ttl_s=int(routing_config.get("wrapup_hold_ttl_s", 90)),
                )
            else:
                logger.warning(
                    "agent_done: missing conversation_id — skipping remove_conversation "
                    "tenant=%s instance=%s pools=%s",
                    tenant_id, instance_id, fallback_pools,
                )
        elif event_type in ("agent_paused", "agent_logout"):
            await self._deactivate_instance(tenant_id, instance_id, event)
        else:
            logger.debug("Lifecycle event ignored: %s", event_type)

    @staticmethod
    def _is_human_instance(instance_id: str, existing: dict | None) -> bool:
        """Humano = `source: "human_login"` no registro vivo. O prefixo `human-`
        é o fallback para quando o registro ainda não existe (mesmo teste que o
        `crash_detector` já usa)."""
        if existing and existing.get("source") == "human_login":
            return True
        return instance_id.startswith("human-")

    async def _upsert_instance(
        self, tenant_id: str, instance_id: str, event: dict,
        event_type: str = "agent_ready",
    ) -> tuple[list[str], str] | None:
        """
        Creates or updates an instance in Redis with TTL 30s.
        Devolve `(pools, agent_type_id)` efetivamente gravados — o chamador usa
        isso para manter o `instance_meta` de acordo com o registro (em vez de
        gravar de novo o que veio no evento, que para humano é o valor oscilante).
        Called on agent_ready and agent_busy — spec: "TTL renewed on each agent_ready or agent_busy".

        F1 do ADR `adr-human-agent-pool-scoped-identity` — **liveness ≠ identidade**.

        Um humano tem UMA instância (`human-{userId}`) e N conexões WebSocket (uma
        por pool selecionado no Console). Cada conexão emite seu próprio pong a
        cada 15 s, e este método reconstruía o registro INTEIRO a partir do evento
        — então `pools[]` e `agent_type_id` da instância oscilavam entre os pools
        conforme qual conexão pingou por último. Isso já roteou contato com o
        `agent_type_id` errado (o bridge roda o skill que aquele id resolver) e
        removia o humano de pools onde ele seguia logado.

        Regra: o evento de liveness prova apenas que o recurso está vivo. Os fatos
        de RECURSO (`agent_type_id`, `max_concurrent`, `execution_model`, `user_*`,
        ocupação) são preservados do registro vivo, e a MEMBERSHIP (`pools[]`) só
        muda em evento autoritativo — `agent_ready`, que é o único emitido por quem
        conhece o conjunto completo (login manda `mergedPools`, logout parcial manda
        `remainingPools`).

        Instâncias de IA seguem inalteradas: são criadas e mantidas pelo
        reconciliador por-pool, e o `pools[]` delas é genuinamente unitário.
        """
        try:
            existing      = await self._instances.get_instance_raw(tenant_id, instance_id)
            is_human      = self._is_human_instance(instance_id, existing)
            authoritative = event_type == "agent_ready"

            ev_pools = list(event.get("pools") or [])
            ev_type  = event.get("agent_type_id", "") or ""
            dropped_pools: list[str] = []

            if is_human and existing:
                # ── Fatos de recurso: o registro vivo manda, o evento não opina ──
                agent_type_id   = existing.get("agent_type_id", "") or ev_type
                max_concurrent  = existing.get(
                    "max_concurrent", event.get("max_concurrent_sessions", 1)
                )
                execution_model = (
                    existing.get("execution_model")
                    or event.get("execution_model", "stateless")
                )
                user_id     = existing.get("user_id", "")    or event.get("user_id", "")
                user_login  = existing.get("user_login", "") or event.get("user_login", "")
                # Ocupação: a fonte de verdade é o SCARD do semáforo, espelhado no
                # registro por mark_busy/remove_conversation. O `current_sessions`
                # do pong conta só as sessões DAQUELA conexão — nunca vale a escrita.
                current_sessions = int(existing.get("current_sessions", 0) or 0)

                existing_pools = list(existing.get("pools") or [])
                if authoritative and ev_pools:
                    pools         = ev_pools
                    dropped_pools = sorted(set(existing_pools) - set(pools))
                    if dropped_pools:
                        # Legítimo APENAS no logout parcial. Em qualquer outro
                        # caminho é o sintoma de B2 voltando — por isso loga sempre.
                        logger.warning(
                            "human instance membership SHRANK: instance=%s dropped=%s "
                            "before=%s after=%s (evento autoritativo=%s) — esperado só "
                            "em logout parcial",
                            instance_id, dropped_pools, existing_pools, pools, event_type,
                        )
                else:
                    pools = existing_pools
                    if ev_pools and set(ev_pools) != set(existing_pools):
                        logger.info(
                            "liveness event carrying membership IGNORED: instance=%s "
                            "event=%s event_pools=%s kept=%s (produtor legado — "
                            "membership só muda em agent_ready)",
                            instance_id, event_type, ev_pools, existing_pools,
                        )
                if ev_type and agent_type_id and ev_type != agent_type_id:
                    logger.warning(
                        "human instance agent_type_id divergence IGNORED: instance=%s "
                        "event=%s event_value=%s kept=%s — identidade por-pool não mora "
                        "no registro do recurso (ADR adr-human-agent-pool-scoped-identity)",
                        instance_id, event_type, ev_type, agent_type_id,
                    )

            elif is_human and not authoritative:
                # ── Registro ausente + evento de liveness: NÃO recria ────────────
                # Criação de instância humana é do LOGIN (`agent_ready`), não do
                # pong. Recriar aqui seria pior que o problema: uma aba esquecida
                # continua pingando depois do logout completo (que faz DEL da
                # chave) e ressuscitaria um agente FANTASMA — presente para o
                # roteamento, ausente para o humano; os contatos alocados a ele
                # não aparecem em Console nenhum. Falhar visível (agente some até
                # dar refresh) é melhor que falhar invisível (contato some).
                # Mesma regra que vale para `_restore_instance` no bridge.
                logger.warning(
                    "human instance ABSENT on %s — NOT recreating: instance=%s "
                    "heartbeat_pool=%s. Criação é do login WS (agent_ready); um pong "
                    "de aba obsoleta pós-logout criaria agente fantasma. Se o agente "
                    "está de fato conectado, investigar quem apagou %s.",
                    event_type, instance_id,
                    event.get("heartbeat_pool", "") or "?", instance_id,
                )
                return None

            else:
                # ── Evento constrói o registro ──────────────────────────────────
                # Dois casos: (a) instância de IA — comportamento inalterado, é
                # criada e mantida pelo reconciliador por-pool e seu `pools[]` é
                # genuinamente unitário; (b) PRIMEIRO `agent_ready` de um humano
                # (login), que é justamente o evento autoritativo — o mcp-server
                # manda `mergedPools` lido do próprio registro.
                pools            = ev_pools
                agent_type_id    = ev_type
                max_concurrent   = event.get("max_concurrent_sessions", 1)
                execution_model  = event.get("execution_model", "stateless")
                user_id          = event.get("user_id", "")
                user_login       = event.get("user_login", "")
                current_sessions = event.get("current_sessions", 0)

            status = event.get("status", "ready")
            # Map mcp-server status to internal state
            internal_state = _map_status_to_state(status)

            instance = AgentInstance(
                instance_id      = instance_id,
                agent_type_id    = agent_type_id,
                tenant_id        = tenant_id,
                pool_id          = (pools or [""])[0],
                pools            = pools,
                execution_model  = execution_model,
                max_concurrent   = max_concurrent,
                current_sessions = current_sessions,
                state            = internal_state,
                last_seen        = event.get("timestamp"),
                registered_at    = event.get("timestamp", ""),
                user_id          = user_id,
                user_login       = user_login,
            )
            await self._instances.set_instance(instance)
            # `set_instance` só percorre os pools que a instância AINDA declara —
            # o pool do qual ela saiu ficaria no SET de roteamento e ela seguiria
            # alocável nele. Quem limpava era o `unregisterHumanAgent` por escrita
            # direta no Redis; aqui a limpeza passa a acompanhar o próprio evento.
            if dropped_pools:
                await self._instances.remove_from_pool_sets(
                    tenant_id, instance_id, dropped_pools
                )
            logger.debug(
                "Instance updated: tenant=%s instance=%s state=%s sessions=%d pools=%s",
                tenant_id, instance_id, internal_state, instance.current_sessions, pools,
            )
            return list(pools), agent_type_id
        except Exception as exc:
            logger.error(
                "Error updating instance: tenant=%s instance=%s — %s",
                tenant_id, instance_id, exc,
            )
            return None

    async def _drain_queue_for_agent(
        self, tenant_id: str, instance_id: str, event: dict
    ) -> None:
        """
        Scenario 2 (spec 3.3b): agent becomes ready → check all its pools for
        queued contacts, dequeue the highest-priority compatible one, and
        re-publish it to conversations.inbound so the Routing Engine allocates
        it in the next loop iteration.

        Only one contact is dequeued per agent activation — the routing engine
        will run again for that contact and allocate it to this agent or another.
        """
        pools = event.get("pools") or []
        if not pools:
            return

        now_ms   = int(datetime.now(timezone.utc).timestamp() * 1000)
        instance = await self._instances.get_instance(tenant_id, instance_id)
        if not instance or instance.state != "ready":
            return

        for pool_id in pools:
            assert self._pools is not None
            pool = await self._pools.get_pool(tenant_id, pool_id)
            if not pool:
                continue
            # Frente 1: pools pull NÃO são auto-drenados — o agente puxa o contato
            # explicitamente (work_task_claim). O drain por agent_ready só vale push.
            if getattr(pool, "dispatch_mode", "push") == "pull":
                continue

            assert self._router is not None
            contact = await self._router.dequeue(instance, pool, now_ms)
            if not contact:
                continue

            # Check if the session was already closed while waiting in queue.
            # The orchestrator-bridge sets session:{id}:closed (TTL 7d) for every
            # close reason so we can skip re-routing stale sessions and avoid
            # delivering "ghost contacts" to reconnecting human agents.
            try:
                closed_marker = await self._instances._redis.get(
                    f"session:{contact.session_id}:closed"
                )
            except Exception:
                closed_marker = None

            if closed_marker:
                logger.info(
                    "Queue drain: session=%s closed (reason=%s) — removing from queue",
                    contact.session_id,
                    closed_marker.decode() if isinstance(closed_marker, bytes) else closed_marker,
                )
                await self._instances.remove_queued_contact(
                    tenant_id, pool_id, contact.session_id
                )
                # Fila de sistema (Fase A): desistência em fila muda → segmento
                # sintético de abandono (ledger Fase D).
                try:
                    if self._producer is not None:
                        await mute_queue.resolve_mute_exit(
                            self._instances._redis, self._producer,
                            tenant_id, pool_id, contact.session_id, "abandoned",
                        )
                except Exception:
                    pass
                continue

            # Fila de sistema (Fase A): sessão NÃO-ADMITIDA só é re-publicada
            # com vaga no CONTRATO — agente pronto + C cheio = segue esperando
            # (sem churn rejeita→re-enfileira e sem avisos repetidos).
            if self._admission is not None:
                try:
                    unadm = await self._instances._redis.sismember(
                        mute_queue.unadmitted_key(tenant_id), contact.session_id
                    )
                    if unadm and not await self._admission.has_headroom(
                        tenant_id, pool_id,
                        session_reservation = pool.session_reservation,
                        agent_kind          = pool.agent_kind,
                    ):
                        continue
                except Exception:
                    pass   # fail-open

            # Retrieve the full event dict that was stored when the contact was queued
            full_data = await self._instances.get_full_queued_contact(
                tenant_id, contact.session_id
            )
            if not full_data:
                # Stale sorted set entry — remove and continue
                await self._instances.remove_queued_contact(
                    tenant_id, pool_id, contact.session_id
                )
                continue

            # Remove from queue before signalling/re-publishing — prevents double-routing
            await self._instances.remove_queued_contact(
                tenant_id, pool_id, contact.session_id
            )

            # Check whether a Queue Agent is currently active for this session.
            # If so, signal the agent via LPUSH '__agent_available__' to unblock its
            # menu:result BLPOP — the queue agent's skill flow then executes an
            # escalate step to hand over to the now-available human agent.
            # If not, re-publish to conversations.inbound so the Routing Engine
            # allocates it directly (original drain behaviour).
            queue_agent_key   = f"queue:agent_active:{contact.session_id}"
            queue_agent_active = await self._instances._redis.get(queue_agent_key)

            assert self._producer is not None
            if queue_agent_active:
                # Signal the queue agent's menu step to proceed to escalation
                await self._instances._redis.lpush(
                    f"menu:result:{contact.session_id}", "__agent_available__"
                )
                logger.info(
                    "Queue drain: signalled queue agent for session=%s pool=%s tenant=%s "
                    "(agent=%s became ready)",
                    contact.session_id, pool_id, tenant_id, instance_id,
                )
            else:
                # No active queue agent — re-publish directly to conversations.inbound
                await self._producer.send(self._topic_inbound, value=full_data)
                logger.info(
                    "Queue drain: re-routing session=%s to pool=%s tenant=%s "
                    "(agent=%s became ready, no queue agent active)",
                    contact.session_id, pool_id, tenant_id, instance_id,
                )
            # One contact per agent activation — stop here; if the agent has
            # capacity for more, subsequent agent_ready/agent_busy cycles will
            # trigger additional drains.
            return

    async def _refresh_pool_snapshots(
        self, tenant_id: str, pool_ids: list[str], delta: int | None = None
    ) -> None:
        """
        Refreshes pool snapshots for each pool_id.

        Called fire-and-forget on agent_ready so Monitor reflects the pool's
        available/busy counts immediately.

        Two modes:
        - delta is None (initial login / snapshot absent): always do a full
          write_pool_snapshot() which sums capacity across all ready instances.
          This is correct at login time when the pool transitions from 0 → N.
        - delta is set (one agent returned from a session): try a fast-patch
          first (+delta to existing snapshot). If the snapshot has expired
          (TTL 3600s normally keeps it alive; rare miss), fall back to a full
          recount so the counter is never permanently lost.

        This prevents hook pools (wrapup_ia, nps_ia) with 400 agents from
        showing available += 400 every time a single agent finishes a session.
        """
        assert self._pools is not None
        for pool_id in pool_ids:
            try:
                if delta is not None:
                    patched = await self._instances.patch_pool_snapshot_available(
                        tenant_id, pool_id, delta
                    )
                    if patched:
                        logger.debug(
                            "Pool snapshot fast-patched on agent_ready: "
                            "tenant=%s pool=%s delta=%+d",
                            tenant_id, pool_id, delta,
                        )
                        continue
                    # Snapshot absent — fall through to full recount below
                    logger.debug(
                        "Pool snapshot absent on agent_ready fast-patch, "
                        "falling back to full recount: tenant=%s pool=%s",
                        tenant_id, pool_id,
                    )
                pool = await self._pools.get_pool(tenant_id, pool_id)
                if pool:
                    await self._instances.write_pool_snapshot(
                        tenant_id=               tenant_id,
                        pool_id=                 pool_id,
                        sla_target_ms=           pool.sla_target_ms,
                        channel_types=           pool.channel_types,
                        max_reply_time_ms=       pool.max_reply_time_ms,
                        # Arc 19: forward webhook pool fields
                        webhook_skill_id=        pool.webhook_skill_id,
                        max_concurrent_sessions= pool.max_concurrent_sessions,
                    )
                    logger.debug(
                        "Pool snapshot (full recount) written on agent_ready: "
                        "tenant=%s pool=%s",
                        tenant_id, pool_id,
                    )
            except Exception as exc:
                logger.warning(
                    "Failed to refresh pool snapshot on agent_ready: pool=%s — %s",
                    pool_id, exc,
                )

    async def _deactivate_instance(
        self, tenant_id: str, instance_id: str, event: dict
    ) -> None:
        """
        Removes instance from all pool sets (paused/logout) and refreshes
        pool snapshots so the Monitor reflects the change immediately.

        For agent_logout the mcp-server may already have DEL'd the instance key
        before the Kafka event arrives.  In that case get_instance() returns None
        but we still need to clean up pool sets and snapshots.  We fall back to
        the pool list carried in the event payload (populated since the fix that
        sends pools=[allPools] on full logout).
        """
        event_type = event.get("event", "")
        new_state  = "paused" if event_type == "agent_paused" else "logged_out"

        # Determine which pools need cleanup.
        # Primary source: instance key in Redis (most accurate).
        # Fallback: pools list in the Kafka event (set by mcp-server on logout).
        affected_pools: list[str] = []
        try:
            instance = await self._instances.get_instance(tenant_id, instance_id)
            if instance:
                instance.state = new_state
                await self._instances.set_instance(instance)
                affected_pools = list(instance.pools)
                logger.info(
                    "[deactivate] Instance found and deactivated: "
                    "tenant=%s instance=%s state=%s pools=%s",
                    tenant_id, instance_id, new_state, affected_pools,
                )
            else:
                # Instance key already deleted (mcp-server DEL'd before Kafka delivery).
                # Use the pool list from the event payload to clean up pool sets.
                affected_pools = event.get("pools") or []
                logger.info(
                    "[deactivate] Instance already deleted; using event pools=%s "
                    "tenant=%s instance=%s",
                    affected_pools, tenant_id, instance_id,
                )
                if affected_pools:
                    # Remove from both ready and busy sets for each pool.
                    # NOTE: LifecycleEventHandler has no self._redis; use the
                    # redis client that lives on the InstanceRegistry instead.
                    _redis = self._instances._redis
                    for pool_id in affected_pools:
                        r1 = await _redis.srem(
                            f"{tenant_id}:pool:{pool_id}:instances", instance_id
                        )
                        r2 = await _redis.srem(
                            f"{tenant_id}:pool:{pool_id}:busy_instances", instance_id
                        )
                        logger.info(
                            "[deactivate] SREM pool=%s instances=%s busy_instances=%s",
                            pool_id, r1, r2,
                        )
        except Exception as exc:
            logger.error(
                "Error deactivating instance: tenant=%s instance=%s — %s",
                tenant_id, instance_id, exc,
            )
            return

        # Refresh pool snapshots for all affected pools so the Monitor
        # immediately reflects the agent going offline.
        # refresh_pool_snapshot lives on InstanceRegistry, not PoolRegistry.
        # self._instances is always set; self._pools is optional (used only for
        # pool config lookups like get_pool()).
        if affected_pools:
            for pool_id in affected_pools:
                try:
                    await self._instances.refresh_pool_snapshot(tenant_id, pool_id)
                    logger.info(
                        "[deactivate] Pool snapshot refreshed: "
                        "tenant=%s pool=%s instance=%s",
                        tenant_id, pool_id, instance_id,
                    )
                except Exception as exc:
                    logger.warning(
                        "[deactivate] Could not refresh pool snapshot: tenant=%s pool=%s — %s",
                        tenant_id, pool_id, exc,
                    )


def _map_status_to_state(status: str) -> str:
    """Normalises mcp-server status to internal routing-engine state."""
    mapping = {
        "login":   "login",
        "ready":   "ready",
        "busy":    "busy",
        "paused":  "paused",
        "logout":  "logged_out",
        "draining":"logged_out",
    }
    return mapping.get(status, status)


async def run_listeners(
    redis_client:              aioredis.Redis,
    instance_registry:         InstanceRegistry,
    pool_registry:             PoolRegistry,
    kafka_topic_lifecycle:     str,
    kafka_topic_registry:      str,
    kafka_brokers:             str,
    kafka_group_id:            str,
    # Optional: when provided, enables queue-drain on agent_ready (Scenario 2)
    router:                    "Router | None"          = None,
    kafka_producer:            "AIOKafkaProducer | None" = None,
    kafka_topic_inbound:       str                      = "conversations.inbound",
    # Optional: when provided, subscribes to config.changed and refreshes routing cache
    kafka_topic_config_changed: str | None              = None,
    config_api_url:            str                      = "http://localhost:3600",
    http_client:               "httpx.AsyncClient | None" = None,
    # Optional: channel gateway session close events — cleans up orphan queue entries
    kafka_topic_events:        str | None               = None,
    # Optional: AdmissionController — fila de sistema (drain só com headroom de contrato)
    admission                  = None,
) -> None:
    """
    Starts Kafka consumers for agent.lifecycle, agent.registry.events,
    and optionally config.changed and conversations.events.
    Called by main.py during Routing Engine startup.

    When router + kafka_producer are supplied, agent_ready events trigger an
    automatic queue drain (Scenario 2 — spec 3.3b).

    When kafka_topic_config_changed + http_client are supplied, config.changed
    events for the "routing" namespace invalidate and reload the local
    RoutingConfigCache (spec: config.changed → routing-engine cache refresh).

    When kafka_topic_events is supplied, contact_closed events from the channel
    gateway set session:{id}:closed in Redis and remove orphan queue entries,
    preventing ghost sessions from appearing in the operational pools UI.
    """
    import httpx as _httpx
    from aiokafka import AIOKafkaConsumer

    registry_handler  = RegistryEventHandler(pool_registry)
    lifecycle_handler = LifecycleEventHandler(
        instance_registry   = instance_registry,
        router              = router,
        producer            = kafka_producer,
        pool_registry       = pool_registry,
        kafka_topic_inbound = kafka_topic_inbound,
        admission           = admission,
    )

    _http_client = http_client or _httpx.AsyncClient()
    config_handler = ConfigChangedHandler(
        config_api_url = config_api_url,
        http_client    = _http_client,
    )

    session_closed_handler = SessionClosedEventHandler(instance_registry, admission=admission)

    topics = [kafka_topic_lifecycle, kafka_topic_registry]
    if kafka_topic_config_changed:
        topics.append(kafka_topic_config_changed)
    if kafka_topic_events:
        topics.append(kafka_topic_events)

    consumer = AIOKafkaConsumer(
        *topics,
        bootstrap_servers = kafka_brokers,
        group_id          = kafka_group_id + "-listener",
        value_deserializer= lambda v: json.loads(v.decode("utf-8")),
        auto_offset_reset = "latest",
        # Low-latency tuning: agent_ready events trigger queue drain —
        # reducing fetch_max_wait_ms ensures fast pickup of ready agents.
        fetch_max_wait_ms = 100,
        fetch_min_bytes   = 1,
    )
    await consumer.start()
    logger.info(
        "Kafka listeners started: topics=%s",
        ", ".join(topics),
    )

    try:
        async for msg in consumer:
            payload = msg.value
            topic   = msg.topic
            asyncio.create_task(
                _dispatch(payload, topic, registry_handler, lifecycle_handler,
                          config_handler, session_closed_handler,
                          kafka_topic_config_changed, kafka_topic_events)
            )
    finally:
        await consumer.stop()


async def _dispatch(
    payload:                    dict,
    topic:                      str,
    registry_handler:           RegistryEventHandler,
    lifecycle_handler:          LifecycleEventHandler,
    config_handler:             ConfigChangedHandler,
    session_closed_handler:     SessionClosedEventHandler,
    kafka_topic_config_changed: str | None,
    kafka_topic_events:         str | None,
) -> None:
    try:
        settings = get_settings()
        if topic == settings.kafka_topic_registry:
            await registry_handler.handle(payload)
        elif topic == settings.kafka_topic_lifecycle:
            await lifecycle_handler.handle(payload)
        elif kafka_topic_config_changed and topic == kafka_topic_config_changed:
            await config_handler.handle(payload)
        elif kafka_topic_events and topic == kafka_topic_events:
            await session_closed_handler.handle(payload)
    except Exception as exc:
        logger.error("Error in Kafka dispatch: topic=%s — %s", topic, exc)
