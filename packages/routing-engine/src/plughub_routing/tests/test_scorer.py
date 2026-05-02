"""
test_scorer.py
Routing Engine scorer tests.
Spec: PlugHub v24.0 sections 3.3b and 4.6
"""

import pytest
from ..scorer import (
    score_resource,
    score_contact_in_queue,
    instance_has_capacity,
    determine_routing_mode,
    compute_priority_score,
)
from ..models import (
    ConversationInboundEvent, CustomerProfile, PoolConfig,
    AgentInstance, QueuedContact, RoutingExpression,
)


def make_pool(**kwargs) -> PoolConfig:
    defaults = dict(
        pool_id="p1", tenant_id="t1", channel_types=["webchat"],
        sla_target_ms=480_000, competency_weights={},
        aging_factor=0.4, breach_factor=0.8,
    )
    defaults.update(kwargs)
    return PoolConfig(**defaults)


def make_instance(profile: dict) -> AgentInstance:
    return AgentInstance(
        instance_id="i1", agent_type_id="a1", tenant_id="t1",
        pool_id="p1", execution_model="stateless",
        max_concurrent=1, current_sessions=0, state="ready",
        registered_at="2026-03-16T00:00:00Z", profile=profile,
    )


def make_event(requirements: dict, confidence: float = 0.9) -> ConversationInboundEvent:
    return ConversationInboundEvent(
        session_id="s1", tenant_id="t1", customer_id="c1",
        channel="webchat", confidence=confidence,
        customer_profile=CustomerProfile(),
        requirements=requirements,
        started_at="2026-03-16T14:00:00Z",
    )


def make_queued(tier: str, queued_at_ms: int, requirements: dict = {}) -> QueuedContact:
    return QueuedContact(
        session_id="s1", tenant_id="t1", pool_id="p1",
        tier=tier, queued_at_ms=queued_at_ms, requirements=requirements,
    )


# ── resource_scorer ───────────────────────────

class TestResourceScorer:
    def test_pool_with_no_requirements_accepts_any_resource(self):
        pool = make_pool(competency_weights={})
        inst = make_instance({})
        event = make_event({})
        assert score_resource(event, inst, pool) == 1.0

    def test_hard_filter_when_resource_lacks_required_competency(self):
        pool  = make_pool(competency_weights={"ingles": 2.0})
        inst  = make_instance({"ingles": 0})
        event = make_event({"ingles": 1})
        assert score_resource(event, inst, pool) == -1.0

    def test_score_1_when_resource_exceeds_requirement(self):
        pool  = make_pool(competency_weights={"portabilidade": 1.0})
        inst  = make_instance({"portabilidade": 3})
        event = make_event({"portabilidade": 2})
        assert score_resource(event, inst, pool) == 1.0

    def test_partial_score_when_resource_below_requirement(self):
        pool  = make_pool(competency_weights={"portabilidade": 1.0})
        inst  = make_instance({"portabilidade": 1})
        event = make_event({"portabilidade": 2})
        score = score_resource(event, inst, pool)
        assert 0 < score < 1.0

    def test_senior_resource_scores_higher_than_basic(self):
        """
        Resource below requirement receives partial score.
        Resource that meets or exceeds requirement receives score 1.0.
        Spec: proportional score when resource < requirement.
        """
        pool       = make_pool(competency_weights={"retencao": 3.0})
        inst_basic = make_instance({"retencao": 1})  # below requirement of 3
        inst_sr    = make_instance({"retencao": 3})  # meets requirement exactly
        event      = make_event({"retencao": 3})     # requires level 3
        # inst_sr meets requirement → 1.0; inst_basic is below → partial
        assert score_resource(event, inst_sr, pool) > score_resource(event, inst_basic, pool)
        assert score_resource(event, inst_sr, pool) == 1.0
        assert 0.0 < score_resource(event, inst_basic, pool) < 1.0

    def test_contact_without_requirement_scores_max_for_key(self):
        pool  = make_pool(competency_weights={"ingles": 2.0, "retencao": 1.0})
        inst  = make_instance({"ingles": 0, "retencao": 3})
        event = make_event({"retencao": 1})  # does not require ingles
        score = score_resource(event, inst, pool)
        assert score > 0

    def test_multiple_competencies_weighted_average(self):
        pool  = make_pool(competency_weights={"ingles": 2.0, "retencao": 3.0})
        inst  = make_instance({"ingles": 2, "retencao": 3})
        event = make_event({"ingles": 2, "retencao": 2})
        score = score_resource(event, inst, pool)
        assert score == pytest.approx(1.0)


# ── queue_scorer ──────────────────────────────

class TestQueueScorer:
    def test_base_priority_by_tier(self):
        pool = make_pool(sla_target_ms=480_000, aging_factor=0.0, breach_factor=0.0)
        now  = 1_000_000_000
        plat = make_queued("platinum", now)
        gold = make_queued("gold",     now)
        std  = make_queued("standard", now)
        assert score_contact_in_queue(plat, pool, now) == pytest.approx(1.0)
        assert score_contact_in_queue(gold, pool, now) == pytest.approx(0.6)
        assert score_contact_in_queue(std,  pool, now) == pytest.approx(0.2)

    def test_aging_grows_with_wait_time(self):
        pool = make_pool(sla_target_ms=480_000, aging_factor=0.4, breach_factor=0.0)
        queued_at  = 1_000_000_000
        now_start  = queued_at
        now_mid    = queued_at + 240_000  # 50% of SLA
        now_sla    = queued_at + 480_000  # 100% of SLA
        contact    = make_queued("standard", queued_at)

        s_start = score_contact_in_queue(contact, pool, now_start)
        s_mid   = score_contact_in_queue(contact, pool, now_mid)
        s_sla   = score_contact_in_queue(contact, pool, now_sla)

        assert s_start < s_mid < s_sla

    def test_standard_reaches_platinum_after_breach(self):
        """Standard in breach must reach platinum's base priority."""
        pool = make_pool(
            sla_target_ms=480_000, aging_factor=0.4, breach_factor=0.8
        )
        queued_at   = 1_000_000_000
        now_breach  = queued_at + 720_000  # 1.5× SLA
        contact_std = make_queued("standard", queued_at)

        score_std_breach = score_contact_in_queue(contact_std, pool, now_breach)
        # base_priority(standard)=0.2 + aging=0.4 + breach=0.4 = 1.0
        assert score_std_breach == pytest.approx(1.0, rel=0.05)

    def test_breach_factor_accelerates_after_sla(self):
        pool    = make_pool(sla_target_ms=480_000, aging_factor=0.4, breach_factor=0.8)
        contact = make_queued("standard", 1_000_000_000)
        s_no_breach = score_contact_in_queue(contact, pool, 1_000_480_000)
        s_breach    = score_contact_in_queue(contact, pool, 1_000_960_000)
        assert s_breach > s_no_breach

    def test_aging_capped_at_1_before_breach(self):
        """Aging must not exceed aging_factor before breach."""
        pool    = make_pool(sla_target_ms=480_000, aging_factor=0.4, breach_factor=0.8)
        contact = make_queued("standard", 1_000_000_000)
        s_long  = score_contact_in_queue(contact, pool, 1_004_800_000)
        assert s_long > score_contact_in_queue(contact, pool, 1_000_480_000)


# ── instance_has_capacity ─────────────────────

class TestCapacity:
    def test_ready_with_capacity(self):
        inst = make_instance({})
        inst.state = "ready"; inst.current_sessions = 0; inst.max_concurrent = 1
        assert instance_has_capacity(inst) is True

    def test_busy(self):
        inst = make_instance({})
        inst.state = "busy"; inst.current_sessions = 1; inst.max_concurrent = 1
        assert instance_has_capacity(inst) is False

    def test_ready_at_capacity(self):
        inst = make_instance({})
        inst.state = "ready"; inst.current_sessions = 5; inst.max_concurrent = 5
        assert instance_has_capacity(inst) is False

    def test_human_with_multiple_sessions(self):
        inst = make_instance({})
        inst.state = "ready"; inst.current_sessions = 3; inst.max_concurrent = 5
        assert instance_has_capacity(inst) is True


# ── determine_routing_mode ────────────────────

class TestRoutingMode:
    def test_autonomous_above_threshold(self):
        assert determine_routing_mode(0.90) == "autonomous"

    def test_autonomous_exactly_at_threshold(self):
        assert determine_routing_mode(0.85) == "autonomous"

    def test_hybrid_between_thresholds(self):
        assert determine_routing_mode(0.75) == "hybrid"

    def test_supervised_below_threshold(self):
        assert determine_routing_mode(0.50) == "supervised"

    def test_supervised_with_risk_flag(self):
        """risk_flag forces supervised mode regardless of confidence."""
        assert determine_routing_mode(0.92, risk_flag=True) == "supervised"

    def test_hybrid_boundary_exactly_at_0_60(self):
        assert determine_routing_mode(0.60) == "hybrid"

    def test_supervised_just_below_0_60(self):
        assert determine_routing_mode(0.599) == "supervised"


# ── compute_priority_score ────────────────────
# Spec 4.6 — tests with known values

class TestPriorityScore:
    """
    Priority score tests with known values.
    Formula:
        score = (sla_urgency    × weight_sla)
              + (wait_time_norm  × weight_wait)
              + (customer_tier   × weight_tier)
              + (churn_risk      × weight_churn)
              + (business_score  × weight_business)

    Weights from the spec example (retencao_humano):
        weight_sla=1.0, weight_wait=0.8, weight_tier=0.6,
        weight_churn=0.9, weight_business=0.4
    """

    def _expr(self, **kwargs) -> RoutingExpression:
        defaults = dict(
            weight_sla=1.0, weight_wait=0.8,
            weight_tier=0.6, weight_churn=0.9, weight_business=0.4,
        )
        defaults.update(kwargs)
        return RoutingExpression(**defaults)

    def test_sla_urgency_above_1_returns_infinity(self):
        """sla_urgency > 1.0 → absolute maximum priority (inf)."""
        score = compute_priority_score(
            routing_expr   = self._expr(),
            sla_urgency    = 1.1,
            wait_time_norm = 0.5,
            customer_tier  = "standard",
            churn_risk     = 0.5,
            business_score = 0.5,
        )
        assert score == float("inf")

    def test_sla_urgency_exactly_1_is_not_infinity(self):
        """sla_urgency == 1.0 must calculate normally (threshold not inclusive)."""
        score = compute_priority_score(
            routing_expr   = self._expr(),
            sla_urgency    = 1.0,
            wait_time_norm = 0.0,
            customer_tier  = "standard",
            churn_risk     = 0.0,
            business_score = 0.0,
        )
        # score = 1.0×1.0 + 0×0.8 + 0.2×0.6 + 0×0.9 + 0×0.4 = 1.0 + 0.12 = 1.12
        assert score == pytest.approx(1.12, rel=1e-4)

    def test_platinum_high_churn_scores_more_than_standard_zero_churn(self):
        """Platinum with high churn must have higher score than standard with no churn."""
        expr = self._expr()
        score_plat = compute_priority_score(
            routing_expr   = expr,
            sla_urgency    = 0.0,
            wait_time_norm = 0.0,
            customer_tier  = "platinum",
            churn_risk     = 0.9,
            business_score = 0.0,
        )
        score_std = compute_priority_score(
            routing_expr   = expr,
            sla_urgency    = 0.0,
            wait_time_norm = 0.0,
            customer_tier  = "standard",
            churn_risk     = 0.0,
            business_score = 0.0,
        )
        assert score_plat > score_std

    def test_exact_calculation_with_known_values(self):
        """
        Known values — manual calculation:
          sla_urgency=0.5, wait_time_norm=0.3, tier=gold, churn_risk=0.8, business_score=0.6
          score = (0.5×1.0) + (0.3×0.8) + (0.6×0.6) + (0.8×0.9) + (0.6×0.4)
                = 0.50 + 0.24 + 0.36 + 0.72 + 0.24
                = 2.06
        """
        score = compute_priority_score(
            routing_expr   = self._expr(),
            sla_urgency    = 0.5,
            wait_time_norm = 0.3,
            customer_tier  = "gold",
            churn_risk     = 0.8,
            business_score = 0.6,
        )
        assert score == pytest.approx(2.06, rel=1e-4)

    def test_all_zeros_returns_minimum_tier_score(self):
        """No urgency, no wait, no relevant tier — score must be minimum tier."""
        score = compute_priority_score(
            routing_expr   = self._expr(),
            sla_urgency    = 0.0,
            wait_time_norm = 0.0,
            customer_tier  = "standard",  # tier_score = 0.2
            churn_risk     = 0.0,
            business_score = 0.0,
        )
        # score = 0 + 0 + (0.2×0.6) + 0 + 0 = 0.12
        assert score == pytest.approx(0.12, rel=1e-4)

    def test_zero_weights_do_not_contribute(self):
        """Zero weight neutralises the corresponding dimension."""
        expr = RoutingExpression(
            weight_sla=0.0, weight_wait=0.0, weight_tier=0.0,
            weight_churn=1.0, weight_business=0.0,
        )
        score = compute_priority_score(
            routing_expr   = expr,
            sla_urgency    = 0.9,        # high, but weight=0
            wait_time_norm = 0.9,        # high, but weight=0
            customer_tier  = "platinum", # high, but weight=0
            churn_risk     = 0.7,        # only this contributes
            business_score = 0.9,       # high, but weight=0
        )
        # score = 0 + 0 + 0 + (0.7×1.0) + 0 = 0.7
        assert score == pytest.approx(0.70, rel=1e-4)

    def test_platinum_beats_gold_beats_standard(self):
        """Tier hierarchy must be reflected in the score."""
        expr = RoutingExpression(
            weight_sla=0.0, weight_wait=0.0, weight_tier=1.0,
            weight_churn=0.0, weight_business=0.0,
        )
        s_plat = compute_priority_score(expr, 0, 0, "platinum", 0, 0)
        s_gold = compute_priority_score(expr, 0, 0, "gold",     0, 0)
        s_std  = compute_priority_score(expr, 0, 0, "standard", 0, 0)
        assert s_plat > s_gold > s_std
        assert s_plat == pytest.approx(1.0)
        assert s_gold == pytest.approx(0.6)
        assert s_std  == pytest.approx(0.2)

    def test_sla_urgency_between_0_and_1_does_not_cause_max_priority(self):
        """sla_urgency <= 1.0 must return a normal float, not inf."""
        for urgency in [0.0, 0.5, 0.99, 1.0]:
            score = compute_priority_score(
                routing_expr   = self._expr(),
                sla_urgency    = urgency,
                wait_time_norm = 0.0,
                customer_tier  = "standard",
                churn_risk     = 0.0,
                business_score = 0.0,
            )
            assert score != float("inf"), f"urgency={urgency} should not return inf"

    def test_high_business_score_increases_priority(self):
        """business_score contributes proportionally to its weight."""
        expr = RoutingExpression(
            weight_sla=0.0, weight_wait=0.0, weight_tier=0.0,
            weight_churn=0.0, weight_business=1.0,
        )
        s_high = compute_priority_score(expr, 0, 0, "standard", 0, business_score=0.9)
        s_low  = compute_priority_score(expr, 0, 0, "standard", 0, business_score=0.1)
        assert s_high > s_low
        assert s_high == pytest.approx(0.9)
        assert s_low  == pytest.approx(0.1)


# ── Arc 7d — Performance Blending ─────────────────────────────────────────────

class TestResourceScorerPerformanceBlending:
    """
    Arc 7d: score_resource() with performance_score + performance_score_weight.

    Formula: final = (1 - w) × competency_score + w × performance_score
    Hard filter (-1.0) must be preserved regardless of performance_score.
    """

    def test_zero_weight_ignores_performance(self):
        """weight=0.0 → pure competency (backward-compatible default)."""
        pool = make_pool(competency_weights={"ingles": 1.0})
        inst = make_instance({"ingles": 1})
        event = make_event({"ingles": 1})

        score_no_perf = score_resource(event, inst, pool)
        score_w_perf  = score_resource(
            event, inst, pool,
            performance_score=0.9,
            performance_score_weight=0.0,
        )
        assert score_no_perf == score_w_perf

    def test_high_performance_boosts_score(self):
        """High perf score with weight=0.3 raises final above competency alone."""
        pool  = make_pool(competency_weights={"ingles": 1.0})
        inst  = make_instance({"ingles": 1})
        event = make_event({"ingles": 2})  # partial match → competency=0.5

        base_score = score_resource(event, inst, pool)
        # 0.5 competency, weight=0, no blending → 0.5
        assert base_score == pytest.approx(0.5, abs=1e-4)

        blended = score_resource(
            event, inst, pool,
            performance_score=1.0,       # perfect history
            performance_score_weight=0.3,
        )
        # final = 0.7 × 0.5 + 0.3 × 1.0 = 0.35 + 0.30 = 0.65
        assert blended == pytest.approx(0.65, abs=1e-4)

    def test_low_performance_lowers_score(self):
        """Low perf score with weight=0.3 lowers final below competency alone."""
        pool  = make_pool(competency_weights={"ingles": 1.0})
        inst  = make_instance({"ingles": 2})
        event = make_event({"ingles": 1})  # full match → competency=1.0

        blended = score_resource(
            event, inst, pool,
            performance_score=0.0,       # worst history
            performance_score_weight=0.3,
        )
        # final = 0.7 × 1.0 + 0.3 × 0.0 = 0.70
        assert blended == pytest.approx(0.70, abs=1e-4)

    def test_hard_filter_preserved_with_high_performance(self):
        """Hard filter (-1.0) is never overridden by a high performance_score."""
        pool  = make_pool(competency_weights={"ingles": 2.0})
        inst  = make_instance({"ingles": 0})  # lacks required skill
        event = make_event({"ingles": 1})

        result = score_resource(
            event, inst, pool,
            performance_score=1.0,
            performance_score_weight=1.0,
        )
        assert result == -1.0

    def test_neutral_default_performance_no_bias(self):
        """performance_score=0.5 (neutral) produces same ordering as no blending."""
        pool   = make_pool(competency_weights={"ingles": 1.0})
        inst_a = make_instance({"ingles": 2})   # better match → competency 1.0
        inst_b = make_instance({"ingles": 1})   # weaker match → competency 0.5
        event  = make_event({"ingles": 2})

        w = 0.3
        # With neutral 0.5 for both, relative ordering should still hold
        score_a = score_resource(event, inst_a, pool, performance_score=0.5, performance_score_weight=w)
        score_b = score_resource(event, inst_b, pool, performance_score=0.5, performance_score_weight=w)
        assert score_a > score_b

    def test_no_requirements_pool_blends_correctly(self):
        """Pool with no competency_weights (competency=1.0) still blends."""
        pool  = make_pool(competency_weights={})
        inst  = make_instance({})
        event = make_event({})

        blended = score_resource(
            event, inst, pool,
            performance_score=0.4,
            performance_score_weight=0.5,
        )
        # final = 0.5 × 1.0 + 0.5 × 0.4 = 0.5 + 0.2 = 0.7
        assert blended == pytest.approx(0.70, abs=1e-4)
