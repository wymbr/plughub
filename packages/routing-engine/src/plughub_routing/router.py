"""
router.py
Main routing logic — two scenarios with distinct scorers.
Spec: PlugHub v24.0 section 3.3b
"""

from __future__ import annotations
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from .models import (
    ConversationInboundEvent,
    ConversationRoutedEvent,
    QueuedContact,
    AgentInstance,
    PoolConfig,
    RoutingResult,
)
from .scorer import (
    score_resource,
    score_contact_in_queue,
    instance_has_capacity,
    determine_routing_mode,
    compute_priority_score,
)
from .registry import InstanceRegistry, PoolRegistry
from .config import get_settings
from .routing_config import routing_config

if TYPE_CHECKING:
    from aiokafka import AIOKafkaProducer

logger = logging.getLogger("plughub.routing.router")


class Router:
    def __init__(
        self,
        instance_registry: InstanceRegistry,
        pool_registry:     PoolRegistry,
        local_site:        str = "site_local",
        kafka_producer:    "AIOKafkaProducer | None" = None,
        kafka_topic_queue_positions: str = "queue.position_updated",
    ) -> None:
        self._instances       = instance_registry
        self._pools           = pool_registry
        self._local_site      = local_site
        self._settings        = get_settings()
        self._producer        = kafka_producer
        self._topic_positions = kafka_topic_queue_positions

    # ─────────────────────────────────────────────
    # SCENARIO 1 — Contact arrives
    # ─────────────────────────────────────────────

    async def route(
        self,
        event:      ConversationInboundEvent,
        elapsed_ms: int = 0,
    ) -> RoutingResult:
        """
        Routes a contact to the most compatible resource.
        Tries local site first, remote sites as fallback.
        Total timeout: 150ms per site.
        """
        now = datetime.now(timezone.utc).isoformat()

        # When pool_id is explicit (set by entry point config or escalation target),
        # restrict routing to that pool only — no scanning of all tenant pools.
        # This is the expected path for all contacts: the channel entry point
        # declares the service pool, so the routing engine never needs to infer it.
        if event.pool_id:
            pool = await self._pools.get_pool(event.tenant_id, event.pool_id)
            pools = [pool] if pool else []
        else:
            # Legacy fallback: scan all pools compatible with the channel.
            # Only reached if the event was published without pool_id
            # (e.g. manual test events, external integrations not yet updated).
            pools = await self._pools.get_candidate_pools(event.tenant_id, event.channel)
            # Arc 19: webhook sessions carry no pool_id — the skill_id is the
            # endpoint (DNIS). Resolve deterministically by matching it against
            # each pool's webhook_skill_id, so multiple webhook pools route
            # correctly (not just the first/scored candidate). Falls back to the
            # unfiltered scan when no pool declares webhook_skill_id (backward-compat
            # with a single webhook pool).
            if event.channel == "webhook" and event.skill_id:
                matched = [
                    p for p in pools
                    if getattr(p, "webhook_skill_id", None) == event.skill_id
                ]
                if len(matched) > 1:
                    # S4 — endereço AMBÍGUO: o mesmo skill está deployado em N pools
                    # (regime legítimo — é o desenho do survey: um skill outbound em três
                    # pools, um por grão, cada um com `config_json` diferente). Escolher
                    # por score seria rodar um deploy que o chamador não pediu, em
                    # silêncio. O endereço canônico é o POOL (`pool_id` no evento).
                    logger.error(
                        "Webhook endpoint AMBÍGUO: skill_id=%s casa %d pools (%s) — "
                        "sessão %s NÃO será roteada. O endereço canônico é o POOL: use "
                        "workflow_trigger(pool_id=...) / POST /v1/channels/webhook/pool/{pool_id}. "
                        "skill_id só é endereço enquanto UM pool o declara.",
                        event.skill_id, len(matched),
                        ",".join(p.pool_id for p in matched), event.session_id,
                    )
                    pools = []
                elif matched:
                    pools = matched
                elif any(getattr(p, "webhook_skill_id", None) for p in pools):
                    # Regime multi-pool determinístico: veio um skill_id mas NENHUM pool
                    # webhook o declara → REJEITA (não misroteia para um pool alheio).
                    # pools=[] cai no no_resource/queued abaixo. O fallback não-filtrado
                    # fica só para o legado single-pool (nenhum pool declara webhook_skill_id).
                    pools = []

        if not pools:
            return self._build_queued_result(event, now)

        # ── Frente 1: dispatch pull ───────────────────────────────────────────
        # Pools em dispatch_mode "pull" NÃO auto-alocam: o contato é parqueado na
        # fila e um agente logado o retira explicitamente (work_task_claim, F1.2).
        # Pula o _allocate e segue o MESMO caminho de "queued" (release do pool de
        # origem + queue.position) que uma alocação que falha.
        if event.pool_id and getattr(pools[0], "dispatch_mode", "push") == "pull":
            # F1.3: re-parque limpa qualquer claim lease anterior desta sessão — um
            # contato re-enfileirado (ex.: agente desconectou → bridge re-roteia →
            # aqui) não pode ficar com lease órfã apontando a um claim que acabou.
            asyncio.create_task(
                self._instances.delete_claim_lease(
                    event.tenant_id, event.pool_id, event.session_id
                )
            )
            queued_result = self._build_queued_result(event, now)
            asyncio.create_task(
                self._release_session_from_pool(
                    event.tenant_id, event.session_id, event.pool_id
                )
            )
            _pull_pool = pools[0]
            asyncio.create_task(self._publish_queue_position(event, _pull_pool))
            asyncio.create_task(
                self._write_snapshot(event.tenant_id, _pull_pool.pool_id, _pull_pool)
            )
            return queued_result

        # Try local site
        try:
            result = await asyncio.wait_for(
                self._allocate(event, pools, elapsed_ms, self._local_site),
                timeout=self._settings.routing_timeout_ms / 1000,
            )
            if result.allocated:
                # Fire-and-forget snapshot update after successful allocation
                if pools:
                    _matched = next((p for p in pools if p.pool_id == result.pool_id), pools[0])
                    asyncio.create_task(
                        self._write_snapshot(event.tenant_id, _matched.pool_id, _matched)
                    )
                return result
        except asyncio.TimeoutError:
            pass

        # Try remote sites (cross-site)
        remote_sites = {s for p in pools for s in p.remote_sites}
        for site in remote_sites:
            try:
                result = await asyncio.wait_for(
                    self._allocate(event, pools, elapsed_ms, site),
                    timeout=0.300,  # 300ms per remote site
                )
                if result.allocated:
                    result.cross_site     = True
                    result.allocated_site = site
                    return result
            except asyncio.TimeoutError:
                continue

        # Contact could not be allocated — queued
        queued_result = self._build_queued_result(event, now)

        # Release active-count from the session's previous pool.
        # Covers the escalation case: AI segment ends, session moves to a human
        # pool's queue.  agent_done fires only on full session close, so without
        # this call the origin pool's busy counter would stay elevated until the
        # session eventually closes.
        asyncio.create_task(
            self._release_session_from_pool(
                event.tenant_id, event.session_id, event.pool_id
            )
        )

        # Publish queue.position_updated so subscribers (UI, channel-gateway)
        # can inform the customer of their queue position and estimated wait time.
        if event.pool_id and pools:
            _pool = next((p for p in pools if p.pool_id == event.pool_id), pools[0])
            asyncio.create_task(self._publish_queue_position(event, _pool))
            asyncio.create_task(self._write_snapshot(event.tenant_id, _pool.pool_id, _pool))

        return queued_result

    async def _publish_queue_position(
        self,
        event: ConversationInboundEvent,
        pool:  PoolConfig,
    ) -> None:
        """
        Publishes queue.position_updated to Kafka after a contact is queued.
        Subscribers: channel-gateway (to inform customer), rules-engine, analytics.
        """
        if not self._producer:
            return
        try:
            queue_length = await self._instances.get_queue_length(
                event.tenant_id, pool.pool_id
            )
            available = await self._instances.get_available_count(
                event.tenant_id, pool.pool_id
            )
            avg_handle_ms    = int(pool.sla_target_ms * 0.7)
            estimated_wait_ms = queue_length * avg_handle_ms

            payload = {
                "event":              "queue.position_updated",
                "tenant_id":          event.tenant_id,
                "session_id":         event.session_id,
                "pool_id":            pool.pool_id,
                "queue_length":       queue_length,
                "available_agents":   available,
                "estimated_wait_ms":  estimated_wait_ms,
                "sla_target_ms":      pool.sla_target_ms,
                "published_at":       datetime.now(timezone.utc).isoformat(),
            }
            await self._producer.send_and_wait(
                self._topic_positions,
                value=json.dumps(payload).encode(),
            )
        except Exception as exc:
            logger.warning(
                "Failed to publish queue.position_updated for session %s: %s",
                event.session_id, exc,
            )

    async def _write_snapshot(
        self,
        tenant_id: str,
        pool_id:   str,
        pool:      PoolConfig,
    ) -> None:
        """Writes pool operational snapshot to Redis after a routing event."""
        try:
            await self._instances.write_pool_snapshot(
                tenant_id=               tenant_id,
                pool_id=                 pool_id,
                sla_target_ms=           pool.sla_target_ms,
                channel_types=           pool.channel_types,
                max_reply_time_ms=       pool.max_reply_time_ms,
                # Arc 19: forward webhook pool fields so snapshot includes them
                webhook_skill_id=        pool.webhook_skill_id,
                max_concurrent_sessions= pool.max_concurrent_sessions,
            )
        except Exception as exc:
            logger.warning(
                "Failed to write pool snapshot for pool %s: %s", pool_id, exc
            )

    async def _release_session_from_pool(
        self,
        tenant_id:   str,
        session_id:  str,
        new_pool_id: str | None = None,
    ) -> None:
        """
        Fire-and-forget wrapper: releases the session's active-count from its
        current pool when it moves to a queue without an agent being allocated.
        See InstanceRegistry.release_session_from_pool for full semantics.
        """
        try:
            await self._instances.release_session_from_pool(
                tenant_id, session_id, new_pool_id
            )
        except Exception as exc:
            logger.warning(
                "Failed to release session %s from previous pool: %s",
                session_id, exc,
            )

    async def _allocate(
        self,
        event:   ConversationInboundEvent,
        pools:   list[PoolConfig],
        elapsed: int,
        site:    str,
    ) -> RoutingResult:
        now = datetime.now(timezone.utc).isoformat()

        # Check session affinity (stateful)
        affinity_id = await self._instances.get_session_affinity(event.session_id)
        if affinity_id:
            result = await self._try_affinity(event, affinity_id, pools, now)
            if result:
                return result

        # Calculate resource_score for each available instance.
        # Router atomic allocation (Fatia B): coletamos TODOS os candidatos pontuados
        # e, em seguida, tentamos um CLAIM atômico do melhor; se ele falhar (perdeu a
        # corrida de concorrência / instância lotada), caímos no próximo best. Isso
        # elimina a sobre-alocação do antigo select→mark_busy não-atômico.
        # Arc 7d — performance_score_weight is dynamically overridable via
        # Config API namespace "routing" key "performance_score_weight".
        # Falls back to env-var setting when Config API is unavailable.
        perf_weight = routing_config.get(
            "performance_score_weight",
            self._settings.performance_score_weight,
        )

        candidates: list[tuple[float, PoolConfig, AgentInstance]] = []
        for pool in pools:
            instances = await self._instances.get_ready_instances(
                event.tenant_id, pool.pool_id
            )
            for inst in instances:
                if not instance_has_capacity(inst):
                    continue
                # Conference: only allocate instances of the requested agent type.
                # Prevents assigning a generic pool instance when the supervisor
                # explicitly invited a specific AI agent type.
                if event.agent_type_id and inst.agent_type_id != event.agent_type_id:
                    continue

                # Arc 7d — fetch historical performance score when weight > 0.
                # Falls back to 0.5 (neutral) when no data is available.
                if perf_weight > 0.0:
                    perf_score = await self._instances.get_agent_performance_score(
                        event.tenant_id, inst.agent_type_id
                    )
                else:
                    perf_score = 0.5  # unused — weight is 0.0

                rscore = score_resource(
                    event, inst, pool,
                    performance_score=perf_score,
                    performance_score_weight=perf_weight,
                )
                if rscore < 0:
                    continue  # hard filter
                candidates.append((rscore, pool, inst))

        # Maior score primeiro; o claim atômico arbitra alocações concorrentes.
        candidates.sort(key=lambda c: c[0], reverse=True)

        # claim_instance usa occupant "{session_id}::{conference_id}": duas confs da MESMA
        # sessão (conference_ids distintos) NÃO dividem vaga → a 2ª recebe -1 e re-seleciona
        # outra instância; re-rota da mesma sessão (mesmo occupant) é idempotente. Quem
        # recebe claim=-1 (lotada / tomada por chamador concorrente) tenta o próximo best.
        best_instance: AgentInstance | None = None
        best_pool:     PoolConfig   | None = None
        best_score:    float               = -1.0
        for rscore, pool, inst in candidates:
            claimed = await self._instances.claim_instance(
                event.tenant_id, inst.instance_id,
                event.session_id, event.conference_id, inst.max_concurrent,
            )
            if claimed >= 1:
                best_instance, best_pool, best_score = inst, pool, rscore
                break
            logger.info(
                "router.claim: instance=%s busy/taken (claim=-1) — re-selecting next best: "
                "session=%s pool=%s", inst.instance_id, event.session_id, pool.pool_id,
            )

        if not best_instance or not best_pool:
            return RoutingResult(
                session_id=event.session_id, tenant_id=event.tenant_id,
                allocated=False, routed_at=now,
            )

        # Determine the session_id to pass to mark_busy:
        # - Normal events: always pass session_id (enables same-pool guard + cross-pool DECR).
        # - Conference events (hooks, specialists): normally pass None to avoid cross-pool
        #   DECR for independent parallel participants.
        # - EXCEPTION: if the conference event targets a pool that already owns the session
        #   (e.g. a conference specialist escalating back to the primary pool), pass the
        #   session_id so the same-pool re-entry guard fires and prevents a double INCR.
        #   Without this, active_count[target_pool] would go 1→2 and never come back to 0.
        mark_busy_session_id: str | None
        if not event.conference_id:
            mark_busy_session_id = event.session_id
        else:
            current_serving = await self._instances.get_session_serving_pool(
                event.tenant_id, event.session_id
            )
            if current_serving and current_serving == best_pool.pool_id:
                # Conference specialist is escalating back to the pool that already
                # owns the session.  Pass session_id → same-pool guard returns early.
                mark_busy_session_id = event.session_id
                logger.info(
                    "router.mark_busy: conference event targets already-serving pool=%s "
                    "session=%s — passing session_id to trigger same-pool guard",
                    best_pool.pool_id, event.session_id,
                )
            else:
                # Fresh conference invite (hook, new specialist).  Pass None to avoid
                # inheriting the cross-pool DECR chain from the primary contact.
                mark_busy_session_id = None

        await self._instances.mark_busy(
            event.tenant_id, best_pool.pool_id, best_instance.instance_id,
            session_id=mark_busy_session_id,
        )
        if best_instance.execution_model == "stateful":
            await self._instances.set_session_affinity(
                event.session_id, best_instance.instance_id
            )

        mode = determine_routing_mode(
            event.confidence or 0.0,
            self._settings.routing_confidence_autonomous,
            self._settings.routing_confidence_hybrid,
            getattr(event.customer_profile, "risk_flag", False),
        )

        # Compute priority_score (spec 4.6) for the selected pool
        prio_score = compute_priority_score(
            routing_expr   = best_pool.routing_expression,
            sla_urgency    = elapsed / max(best_pool.sla_target_ms, 1),
            wait_time_norm = min(elapsed / max(best_pool.sla_target_ms, 1), 1.0),
            customer_tier  = event.customer_profile.tier,
            churn_risk     = event.customer_profile.churn_risk,
            business_score = event.customer_profile.business_score,
        )

        return RoutingResult(
            session_id=event.session_id,
            tenant_id=event.tenant_id,
            allocated=True,
            instance_id=best_instance.instance_id,
            agent_type_id=best_instance.agent_type_id,
            pool_id=best_pool.pool_id,
            resource_score=best_score,
            priority_score=prio_score if prio_score != float("inf") else 9999.0,
            routing_mode=mode,   # type: ignore[arg-type]
            allocated_site=self._local_site,
            sla_target_ms=best_pool.sla_target_ms,
            routed_at=now,
            conference_id=event.conference_id,       # None for regular contacts
            channel_identity=event.channel_identity, # None for regular contacts
        )

    # ─────────────────────────────────────────────
    # SCENARIO 2 — Resource becomes available
    # ─────────────────────────────────────────────

    async def dequeue(
        self,
        instance:  AgentInstance,
        pool:      PoolConfig,
        now_ms:    int,
        top_n:     int = 10,
    ) -> QueuedContact | None:
        """
        Selects the highest-effective-priority queued contact
        that is compatible with this resource.

        Spec 3.3b Scenario 2:
          1. Load top_n contacts from Redis Sorted Set by current score
          2. Recalculate queue_scorer with now_ms for the top_n
          3. Check resource compatibility with each contact
          4. Allocate the highest-priority compatible contact
        """
        queued = await self._instances.get_queued_contacts(
            pool.tenant_id, pool.pool_id, top_n
        )
        if not queued:
            return None

        # Recalculate scores and sort
        scored = [
            (contact, score_contact_in_queue(contact, pool, now_ms))
            for contact in queued
        ]
        scored.sort(key=lambda x: x[1], reverse=True)

        # Return first contact compatible with this resource
        for contact, _ in scored:
            rscore = score_resource(
                # Build minimal event with contact requirements
                _contact_to_event(contact),
                instance,
                pool,
            )
            if rscore >= 0:
                return contact

        return None

    async def _try_affinity(
        self,
        event:      ConversationInboundEvent,
        instance_id: str,
        pools:       list[PoolConfig],
        now:         str,
    ) -> RoutingResult | None:
        for pool in pools:
            instances = await self._instances.get_ready_instances(
                event.tenant_id, pool.pool_id
            )
            for inst in instances:
                if inst.instance_id != instance_id:
                    continue
                if not instance_has_capacity(inst):
                    continue
                rscore = score_resource(event, inst, pool)
                if rscore < 0:
                    continue
                # Same guard as the main route() path: for conference events,
                # only pass None if the target pool is NOT already serving the session.
                dequeue_sid: str | None
                if not event.conference_id:
                    dequeue_sid = event.session_id
                else:
                    cur_sp = await self._instances.get_session_serving_pool(
                        event.tenant_id, event.session_id
                    )
                    dequeue_sid = event.session_id if (cur_sp and cur_sp == pool.pool_id) else None
                # Router atomic allocation (Fatia B): reserva atômica da instância de
                # afinidade antes do mark_busy. Se lotada (claim=-1), cai no roteamento
                # normal (return None) em vez de sobre-alocar.
                _aff_claimed = await self._instances.claim_instance(
                    event.tenant_id, inst.instance_id,
                    event.session_id, event.conference_id, inst.max_concurrent,
                )
                if _aff_claimed < 1:
                    logger.info(
                        "router.affinity: affinity instance %s busy (claim=-1) — "
                        "falling back to normal routing: session=%s",
                        inst.instance_id, event.session_id,
                    )
                    return None
                await self._instances.mark_busy(
                    event.tenant_id, pool.pool_id, inst.instance_id,
                    session_id=dequeue_sid,
                )
                return RoutingResult(
                    session_id=event.session_id, tenant_id=event.tenant_id,
                    allocated=True, instance_id=inst.instance_id,
                    agent_type_id=inst.agent_type_id, pool_id=pool.pool_id,
                    resource_score=rscore, routing_mode="autonomous",
                    allocated_site=self._local_site, routed_at=now,
                    sla_target_ms=pool.sla_target_ms,
                    conference_id=event.conference_id,       # propagate conference context
                    channel_identity=event.channel_identity, # propagate channel identity
                )
        return None

    def _build_queued_result(
        self, event: ConversationInboundEvent, now: str
    ) -> RoutingResult:
        return RoutingResult(
            session_id=event.session_id, tenant_id=event.tenant_id,
            allocated=False, queued=True, routing_mode="supervised", routed_at=now,
            pool_id=event.pool_id,
        )

    # ─────────────────────────────────────────────
    # Frente 1 — Dispatch pull: claim / release
    # Operações expostas como tools/API na F2; aqui são as FUNÇÕES do routing —
    # o Routing Engine continua o único árbitro (ZREM/claim/mark_busy/lease/routed
    # acontecem DENTRO dele; a Console só solicita).
    # ─────────────────────────────────────────────

    @property
    def _claim_lease_s(self) -> int:
        # Config API namespace `routing` (cache routing_config); default 180.
        return int(routing_config.get("claim_lease_s", 180))

    async def work_task_claim(
        self,
        tenant_id:     str,
        pool_id:       str,
        session_id:    str,
        instance_id:   str,
        conference_id: str = "",
    ) -> dict:
        """
        Pull: retirada explícita de um contato da fila por um agente logado.
        Atômica e composta:
          1. valida a instância do agente;
          2. lê o pacote do contato (para routed/rollback);
          3. ZREM atômico da fila (um vencedor) — perdeu → already_claimed;
          4. reserva a vaga no semáforo do RECURSO (push+pull) — −1 (sem capacidade)
             → ROLLBACK re-enfileira → no_capacity;
          5. mark_busy + grava lease do claim;
          6. publica conversations.routed → reusa bridge/Console (vira atendimento).
        """
        now = datetime.now(timezone.utc).isoformat()

        inst = await self._instances.get_instance(tenant_id, instance_id)
        if inst is None:
            return {"claimed": False, "reason": "instance_not_found"}

        contact = await self._instances.get_full_queued_contact(tenant_id, session_id)
        if contact is None:
            return {"claimed": False, "reason": "not_in_queue"}

        # 3 — atomic win
        won = await self._instances.atomic_claim_dequeue(tenant_id, pool_id, session_id)
        if not won:
            return {"claimed": False, "reason": "already_claimed"}

        # 4 — reserva a vaga do recurso (semáforo compartilhado push+pull)
        occ = await self._instances.claim_instance(
            tenant_id, instance_id, session_id,
            conference_id or None, int(inst.max_concurrent),
        )
        if occ == -1:
            # rollback — re-enfileira (JSON ainda armazenado)
            await self._instances.add_queued_contact(
                tenant_id, pool_id, session_id, contact,
                int(contact.get("queued_at_ms")
                    or int(datetime.now(timezone.utc).timestamp() * 1000)),
            )
            logger.info(
                "work_task_claim: no capacity — rolled back: session=%s instance=%s pool=%s",
                session_id, instance_id, pool_id,
            )
            return {"claimed": False, "reason": "no_capacity"}

        # 5 — mark_busy + lease
        await self._instances.mark_busy(tenant_id, pool_id, instance_id, session_id)
        try:
            await self._instances.write_claim_lease(
                tenant_id, pool_id, session_id, instance_id, self._claim_lease_s,
            )
        except Exception as exc:
            logger.warning(
                "work_task_claim: could not write lease session=%s — %s", session_id, exc
            )

        # 6 — publica conversations.routed (reusa todo o downstream)
        # Bug B fix: propagate the conference the caller opened (carried in the
        # queued contact and passed by the claimer) so the bridge attaches the
        # human as the conference PARTICIPANT of the suspended delegate — not a
        # bare primary. Without this the routed event omits the conference, the
        # occupant is "{session}::" (empty conf), and the Console cannot
        # (re-)attach the approval package on claim/re-claim (P2).
        result = RoutingResult(
            session_id=session_id, tenant_id=tenant_id, allocated=True,
            instance_id=instance_id, agent_type_id=inst.agent_type_id,
            pool_id=pool_id, routing_mode="supervised",
            allocated_site=self._local_site, routed_at=now,
            conference_id=conference_id or None,
            channel_identity=contact.get("channel_identity"),
        )
        if self._producer:
            await self._producer.send(
                self._settings.kafka_topic_routed,
                value=ConversationRoutedEvent(
                    session_id=session_id, tenant_id=tenant_id,
                    result=result, routed_at=now,
                ).model_dump(),
            )
        logger.info(
            "work_task_claim: claimed session=%s instance=%s pool=%s occ=%d",
            session_id, instance_id, pool_id, occ,
        )
        return {
            "claimed": True, "instance_id": instance_id,
            "pool_id": pool_id, "contact": contact,
        }

    async def work_task_release(
        self,
        tenant_id:   str,
        pool_id:     str,
        session_id:  str,
        instance_id: str,
    ) -> dict:
        """
        Pull: devolve um contato claimado à fila — remove a lease, libera a vaga do
        recurso (release_instance) e re-enfileira pelos critérios do routing
        (add_queued_contact, NÃO preserva posição). O agente desistiu da task.
        """
        await self._instances.delete_claim_lease(tenant_id, pool_id, session_id)
        remaining = await self._instances.release_instance(
            tenant_id, instance_id, session_id
        )

        contact = await self._instances.get_full_queued_contact(tenant_id, session_id)
        if contact is not None:
            await self._instances.add_queued_contact(
                tenant_id, pool_id, session_id, contact,
                int(datetime.now(timezone.utc).timestamp() * 1000),   # re-ordena
            )
        logger.info(
            "work_task_release: released session=%s instance=%s pool=%s remaining=%d requeued=%s",
            session_id, instance_id, pool_id, remaining, contact is not None,
        )
        return {"released": True, "requeued": contact is not None}


def _contact_to_event(contact: QueuedContact) -> ConversationInboundEvent:
    """Builds a minimal ConversationInboundEvent from a QueuedContact."""
    return ConversationInboundEvent(
        session_id=contact.session_id,
        tenant_id=contact.tenant_id,
        customer_id="",
        channel="webchat",
        requirements=contact.requirements,
        started_at="",
    )
