"""
scorer.py
Routing Engine scorers — two scenarios with distinct responsibilities.
Spec: PlugHub v24.0 sections 3.3b and 4.6

SCENARIO 1 — resource_scorer
  Contact arrives, resources are available.
  Question: which resource is the best match for this contact?
  Dominant criterion: profile/competency compatibility.

SCENARIO 2 — queue_scorer
  Resource becomes available, queue is non-empty.
  Question: which contact will this resource serve first?
  Dominant criterion: effective priority = tier + priority aging.

priority_aging:
  effective_priority(t) = base_priority(tier)
                        + aging_factor × min(t/sla_target, 1.0)
                        + breach_factor × max((t/sla_target) - 1.0, 0)

PRIORITY SCORE (spec 4.6) — decide() per pool:
  score = (sla_urgency    × weight_sla)
        + (wait_time_norm  × weight_wait)
        + (customer_tier   × weight_tier)
        + (churn_risk      × weight_churn)
        + (business_score  × weight_business)

  sla_urgency = oldest_wait_ms / sla_target_ms
  sla_urgency > 1.0 → absolute maximum priority (returns inf)
"""

from __future__ import annotations
from .models import (
    ConversationInboundEvent,
    QueuedContact,
    PoolConfig,
    AgentInstance,
    RoutingExpression,
)

# ─────────────────────────────────────────────
# Conversion tables
# ─────────────────────────────────────────────

TIER_BASE_PRIORITY = {
    "platinum": 1.0,
    "gold":     0.6,
    "standard": 0.2,
}

# Explicit alias for use in priority_score
TIER_SCORE = TIER_BASE_PRIORITY


# ─────────────────────────────────────────────
# SCENARIO 1 — Resource Scorer
# ─────────────────────────────────────────────

def score_resource(
    contact:                 ConversationInboundEvent,
    resource:                AgentInstance,
    pool:                    PoolConfig,
    performance_score:       float = 0.5,
    performance_score_weight: float = 0.0,
) -> float:
    """
    Calculates contact × resource compatibility.

    For each key in pool.competency_weights:
      - If resource.profile[key] == 0 and contact requires > 0 → hard filter (returns -1.0)
      - Otherwise: proportional score based on resource level vs required level

    Returns -1.0 if resource is incompatible (hard filter).
    Returns float [0.0, 1.0] if compatible.

    Arc 7d — performance blending (optional):
      When performance_score_weight > 0.0, the final score blends historical
      agent performance with the competency score:

        final = (1 - w) × competency_score + w × performance_score

      w = performance_score_weight (0.0–1.0)
        0.0 = pure competency (default — backward-compatible, no Redis reads)
        0.3 = 70% competency + 30% historical performance (recommended in prod)

      performance_score: float [0.0, 1.0] — fetched from Redis by the caller.
        0.5 = neutral default (no data yet → no bias).

      Hard filter (-1.0) is preserved regardless of performance_score.
    """
    weights = pool.competency_weights
    if not weights:
        # Pool with no requirements → any resource qualifies.
        # Still apply performance blending if requested.
        competency_score = 1.0
    else:
        total_weight     = sum(weights.values())
        competency_score = 0.0

        for key, weight in weights.items():
            resource_level  = resource.profile.get(key, 0)
            required_level  = contact.requirements.get(key, 0)

            if required_level == 0:
                # Contact does not require this competency → maximum score
                competency_score += weight * 1.0
            elif resource_level == 0:
                # Resource lacks required competency → hard filter
                return -1.0
            else:
                # Proportional score — resource with level ≥ required scores 1.0
                match = min(resource_level / max(required_level, 1), 1.0)
                competency_score += weight * match

        competency_score = competency_score / max(total_weight, 1)

    # Arc 7d — blend in historical performance when weight > 0
    if performance_score_weight > 0.0:
        w = min(max(performance_score_weight, 0.0), 1.0)
        p = min(max(performance_score, 0.0), 1.0)
        final = (1.0 - w) * competency_score + w * p
    else:
        final = competency_score

    return round(final, 4)


def instance_has_capacity(instance: AgentInstance) -> bool:
    """Returns True if the instance has capacity to receive a new conversation."""
    return (
        instance.state == "ready"
        and instance.current_sessions < instance.max_concurrent
    )


def determine_routing_mode(
    confidence:             float,
    threshold_autonomous:   float = 0.85,
    threshold_hybrid:       float = 0.60,
    risk_flag:              bool  = False,
) -> str:
    """
    Determines routing mode from intent confidence.
    Spec 3.3:
      confidence > 0.85  → autonomous
      0.60 to 0.85       → hybrid
      < 0.60 or risk_flag → supervised (forces human mode)
    """
    if risk_flag or confidence < threshold_hybrid:
        return "supervised"
    if confidence >= threshold_autonomous:
        return "autonomous"
    return "hybrid"


# ─────────────────────────────────────────────
# SCENARIO 2 — Queue Scorer
# ─────────────────────────────────────────────

def score_contact_in_queue(
    contact:    QueuedContact,
    pool:       PoolConfig,
    now_ms:     int,
) -> float:
    """
    Calculates the effective priority of a contact in queue.

    effective_priority(t) = base_priority(tier)
                          + aging_factor  × min(t/sla, 1.0)
                          + breach_factor × max((t/sla) - 1.0, 0)

    t       = wait time in ms
    aging   = growth up to the SLA deadline
    breach  = acceleration after SLA breach — no contact waits forever
    """
    elapsed_ms = now_ms - contact.queued_at_ms
    sla_ratio  = elapsed_ms / max(pool.sla_target_ms, 1)

    base_priority = TIER_BASE_PRIORITY.get(contact.tier, 0.2)
    aging         = pool.aging_factor  * min(sla_ratio, 1.0)
    breach_bonus  = pool.breach_factor * max(sla_ratio - 1.0, 0.0)

    return round(base_priority + aging + breach_bonus, 4)


# ─────────────────────────────────────────────
# PRIORITY SCORE — spec 4.6 (used in decide())
# ─────────────────────────────────────────────

def compute_priority_score(
    routing_expr:   RoutingExpression,
    sla_urgency:    float,
    wait_time_norm: float,
    customer_tier:  str,
    churn_risk:     float,
    business_score: float,
) -> float:
    """
    Computes priority_score for a pool in the context of a specific contact.
    Spec 4.6:
        score = (sla_urgency    × weight_sla)
              + (wait_time_norm  × weight_wait)
              + (customer_tier   × weight_tier)
              + (churn_risk      × weight_churn)
              + (business_score  × weight_business)

        sla_urgency = oldest_wait_ms / sla_target_ms
        sla_urgency > 1.0 → absolute maximum priority (other weights ignored)

    Args:
        routing_expr:   pool weights (routing_expression)
        sla_urgency:    oldest_wait_ms / sla_target_ms for this pool
        wait_time_norm: current contact wait time normalised (0–1)
        customer_tier:  customer tier ("platinum"|"gold"|"standard")
        churn_risk:     customer churn risk (0–1)
        business_score: customer business score (0–1)

    Returns:
        float — pool score. inf when sla_urgency > 1.0 (maximum priority).
    """
    if sla_urgency > 1.0:
        # Absolute maximum priority — other weights ignored
        return float("inf")

    tier_score = TIER_SCORE.get(customer_tier, 0.2)

    score = (
        sla_urgency    * routing_expr.weight_sla      +
        wait_time_norm * routing_expr.weight_wait     +
        tier_score     * routing_expr.weight_tier     +
        churn_risk     * routing_expr.weight_churn    +
        business_score * routing_expr.weight_business
    )
    return round(score, 6)
