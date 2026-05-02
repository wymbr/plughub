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

        if not pools:
            return self._build_queued_result(event, now)

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
                tenant_id=     tenant_id,
                pool_id=       pool_id,
                sla_target_ms= pool.sla_target_ms,
                channel_types= pool.channel_types,
            )
        except Exception as exc:
            logger.warning(
                "Failed to write pool snapshot for pool %s: %s", pool_id, exc
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

        # Calculate resource_score for each available instance
        best_instance: AgentInstance | None = None
        best_pool:     PoolConfig   | None = None
        best_score:    float               = -1.0

        # Arc 7d — performance_score_weight is dynamically overridable via
        # Config API namespace "routing" key "performance_score_weight".
        # Falls back to env-var setting when Config API is unavailable.
        perf_weight = routing_config.get(
            "performance_score_weight",
            self._settings.performance_score_weight,
        )

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
                if rscore > best_score:
                    best_score    = rscore
                    best_instance = inst
                    best_pool     = pool

        if not best_instance or not best_pool:
            return RoutingResult(
                session_id=event.session_id, tenant_id=event.tenant_id,
                allocated=False, routed_at=now,
            )

        await self._instances.mark_busy(
            event.tenant_id, best_pool.pool_id, best_instance.instance_id
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
                await self._instances.mark_busy(
                    event.tenant_id, pool.pool_id, inst.instance_id
                )
                return RoutingResult(
                    session_id=event.session_id, tenant_id=event.tenant_id,
                    allocated=True, instance_id=inst.instance_id,
                    agent_type_id=inst.agent_type_id, pool_id=pool.pool_id,
                    resource_score=rscore, routing_mode="autonomous",
                    allocated_site=self._local_site, routed_at=now,
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
