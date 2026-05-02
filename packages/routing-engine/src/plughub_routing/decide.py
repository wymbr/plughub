"""
decide.py
Main routing decision function.
Spec: PlugHub v24.0 sections 3.3 and 4.6

decide() flow:
  1. Classify service zone (autonomous/hybrid/supervised)
  2. Identify candidate pools: supported channel + ready instances in Redis
  3. Filter available instances: status==ready AND current_sessions < max
  4. Compute priority_score per pool (spec 4.6)
  5. Select primary agent (pool with highest score) and fallback
  6. Apply saturation policy if no pool is available (spec 3.3a)
  7. Return RoutingDecision with mode and reevaluation_turn

Total timeout: 150ms (asyncio.wait_for). Spec 3.3.
"""

from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone

import redis.asyncio as aioredis

from .models import (
    CustomerProfile,
    PoolConfig,
    AgentInstance,
    AllocatedAgent,
    RoutingDecision,
    RoutingMode,
)
from .scorer import (
    compute_priority_score,
    determine_routing_mode,
    instance_has_capacity,
)
from .registry import InstanceRegistry, PoolRegistry
from .saturated import SaturationHandler
from .config import get_settings

logger = logging.getLogger("plughub.routing.decide")


class RoutingTimeoutError(Exception):
    """150ms timeout exceeded in decide()."""


class Decider:
    """
    Routing arbiter. Every conversation passes through decide().
    No component routes without going through here.
    """

    def __init__(
        self,
        instance_registry: InstanceRegistry,
        pool_registry:     PoolRegistry,
        saturation_handler: SaturationHandler | None = None,
    ) -> None:
        self._instances   = instance_registry
        self._pools       = pool_registry
        self._saturation  = saturation_handler or SaturationHandler()
        self._settings    = get_settings()

    async def decide(
        self,
        conversation_id:  str,
        tenant_id:        str,
        intent:           str | None,
        confidence:       float,
        channel:          str,
        customer_profile: CustomerProfile,
        elapsed_ms:       int = 0,
    ) -> RoutingDecision:
        """
        Makes a routing decision within 150ms.
        Spec 3.3: total timeout 150ms — uses asyncio.wait_for().

        Args:
            conversation_id:  conversation ID
            tenant_id:        tenant that originated the contact
            intent:           intent detected by the AI Gateway
            confidence:       intent confidence (0.0–1.0)
            channel:          origin channel (chat|whatsapp|voice|email|sms|webrtc)
            customer_profile: customer profile (tier, churn_risk, etc.)
            elapsed_ms:       time the contact has already been waiting (0 = new)

        Returns:
            RoutingDecision with primary, fallback, mode and reevaluation_turn.

        Raises:
            RoutingTimeoutError: if the decision exceeds 150ms.
        """
        try:
            return await asyncio.wait_for(
                self._decide_inner(
                    conversation_id, tenant_id, intent, confidence,
                    channel, customer_profile, elapsed_ms,
                ),
                timeout=self._settings.routing_timeout_ms / 1000.0,
            )
        except asyncio.TimeoutError:
            raise RoutingTimeoutError(
                f"decide() exceeded {self._settings.routing_timeout_ms}ms "
                f"for conversation_id={conversation_id}"
            )

    async def _decide_inner(
        self,
        conversation_id:  str,
        tenant_id:        str,
        intent:           str | None,
        confidence:       float,
        channel:          str,
        customer_profile: CustomerProfile,
        elapsed_ms:       int,
    ) -> RoutingDecision:
        now = datetime.now(timezone.utc).isoformat()

        # ── 1. Classify service zone ─────────────────────────────────────────
        mode = _classify_mode(
            confidence,
            customer_profile.risk_flag,
            self._settings.routing_confidence_autonomous,
            self._settings.routing_confidence_hybrid,
        )

        # ── 2. Identify candidate pools ──────────────────────────────────────
        # Filter: supported channel + pool with ready instances in Redis
        candidate_pools = await self._pools.get_candidate_pools(tenant_id, channel)

        if not candidate_pools:
            return RoutingDecision(
                conversation_id  = conversation_id,
                tenant_id        = tenant_id,
                mode             = mode,
                saturated        = True,
                saturation_action= "no_pools_configured",
                decided_at       = now,
            )

        # ── 3. For each pool, collect available instances ─────────────────────
        # instances with status==ready AND current_sessions < max_concurrent
        pool_candidates: list[tuple[PoolConfig, list[AgentInstance]]] = []
        for pool in candidate_pools:
            instances = await self._instances.get_ready_instances(tenant_id, pool.pool_id)
            available = [i for i in instances if instance_has_capacity(i)]
            if available:
                pool_candidates.append((pool, available))

        # ── 4. Saturation policy if all pools are unavailable ────────────────
        if not pool_candidates:
            # Use the first candidate pool as reference for the policy
            ref_pool     = candidate_pools[0]
            oldest_ms    = await self._instances.get_oldest_queue_wait_ms(
                tenant_id, ref_pool.pool_id
            )
            sla_urgency  = _compute_sla_urgency(oldest_ms, elapsed_ms, ref_pool.sla_target_ms)
            sat_action   = self._saturation.handle(
                channel, sla_urgency, ref_pool, customer_profile
            )
            logger.warning(
                "All pools saturated: tenant=%s channel=%s action=%s",
                tenant_id, channel, sat_action.action_type,
            )
            return RoutingDecision(
                conversation_id  = conversation_id,
                tenant_id        = tenant_id,
                mode             = mode,
                saturated        = True,
                saturation_action= sat_action.action_type,
                decided_at       = now,
            )

        # ── 5. Compute priority_score per pool ───────────────────────────────
        # Spec 4.6: score combines pool urgency and customer profile
        scored_pools: list[tuple[float, PoolConfig, AgentInstance]] = []

        for pool, instances in pool_candidates:
            oldest_ms   = await self._instances.get_oldest_queue_wait_ms(
                tenant_id, pool.pool_id
            )
            sla_urgency = _compute_sla_urgency(oldest_ms, elapsed_ms, pool.sla_target_ms)
            wait_norm   = min(elapsed_ms / max(pool.sla_target_ms, 1), 1.0)

            score = compute_priority_score(
                routing_expr   = pool.routing_expression,
                sla_urgency    = sla_urgency,
                wait_time_norm = wait_norm,
                customer_tier  = customer_profile.tier,
                churn_risk     = customer_profile.churn_risk,
                business_score = customer_profile.business_score,
            )

            # Select the best available instance in the pool
            best_instance = instances[0]  # already filtered by availability
            scored_pools.append((score, pool, best_instance))

        # Sort by: 1) descending score, 2) ascending queue_length (tie-breaker),
        # 3) pool_id (deterministic last resort when queue_length is also equal).
        scored_pools.sort(
            key=lambda x: (
                -(x[0] if x[0] != float("inf") else 1e18),  # descending score
                getattr(x[1], "queue_length", 0),            # ascending queue_length
                x[1].pool_id,                                 # deterministic last resort
            )
        )

        # ── 6. Primary agent and fallback ────────────────────────────────────
        first_score, first_pool, first_instance = scored_pools[0]
        primary = AllocatedAgent(
            instance_id   = first_instance.instance_id,
            agent_type_id = first_instance.agent_type_id,
            pool_id       = first_pool.pool_id,
            score         = first_score if first_score != float("inf") else 9999.0,
        )

        fallback: AllocatedAgent | None = None
        if len(scored_pools) > 1:
            fb_score, fb_pool, fb_instance = scored_pools[1]
            fallback = AllocatedAgent(
                instance_id   = fb_instance.instance_id,
                agent_type_id = fb_instance.agent_type_id,
                pool_id       = fb_pool.pool_id,
                score         = fb_score if fb_score != float("inf") else 9999.0,
            )

        # ── 7. Determine re-evaluation turn ──────────────────────────────────
        reevaluation_turn = _reevaluation_turn(
            mode,
            self._settings.reevaluation_turn_hybrid,
            self._settings.reevaluation_turn_supervised,
        )

        logger.info(
            "Routing decided: tenant=%s conversation=%s mode=%s pool=%s instance=%s score=%.4f",
            tenant_id, conversation_id, mode,
            first_pool.pool_id, first_instance.instance_id,
            primary.score,
        )

        return RoutingDecision(
            conversation_id  = conversation_id,
            tenant_id        = tenant_id,
            mode             = mode,
            primary          = primary,
            fallback         = fallback,
            reevaluation_turn= reevaluation_turn,
            decided_at       = now,
        )


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _classify_mode(
    confidence:           float,
    risk_flag:            bool,
    threshold_autonomous: float,
    threshold_hybrid:     float,
) -> RoutingMode:
    """
    Classifies the service zone.
    Spec 3.3:
      confidence > 0.85  → autonomous
      0.60 to 0.85       → hybrid
      < 0.60 or risk_flag → supervised (forces human mode)
    """
    mode = determine_routing_mode(confidence, threshold_autonomous, threshold_hybrid, risk_flag)
    return mode  # type: ignore[return-value]


def _compute_sla_urgency(
    oldest_queue_ms: int | None,
    elapsed_ms:      int,
    sla_target_ms:   int,
) -> float:
    """
    sla_urgency = oldest_wait_ms / sla_target_ms
    If the queue is empty, uses elapsed_ms of the current contact.
    """
    wait_ms = oldest_queue_ms if oldest_queue_ms is not None else elapsed_ms
    return wait_ms / max(sla_target_ms, 1)


def _reevaluation_turn(
    mode:                    RoutingMode,
    hybrid_turn:             int,
    supervised_turn:         int,
) -> int | None:
    """
    Re-evaluation turn per mode.
      autonomous  → None (AI manages)
      hybrid      → hybrid_turn (default: 5)
      supervised  → supervised_turn (default: 1)
    """
    if mode == "autonomous":
        return None
    if mode == "hybrid":
        return hybrid_turn
    return supervised_turn  # supervised
