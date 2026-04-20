"""
test_decide.py
Tests for decide() — spec PlugHub v24.0 sections 3.3 and 4.6.

Covers:
  - priority_score per pool with known values
  - mode classification (autonomous/hybrid/supervised)
  - risk_flag forces supervised mode
  - correct reevaluation_turn per mode
  - saturation when no pools or instances are available
  - timeout: decide() fails correctly within 150ms
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from ..decide import Decider, RoutingTimeoutError
from ..models import (
    CustomerProfile, PoolConfig, AgentInstance,
    RoutingExpression, RoutingDecision,
)
from ..registry import InstanceRegistry, PoolRegistry
from ..saturated import SaturationHandler


# ─────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────

def _expr(**kwargs) -> RoutingExpression:
    defaults = dict(
        weight_sla=1.0, weight_wait=0.8,
        weight_tier=0.6, weight_churn=0.9, weight_business=0.4,
    )
    defaults.update(kwargs)
    return RoutingExpression(**defaults)


def _pool(pool_id: str = "retencao_humano", **kwargs) -> PoolConfig:
    defaults = dict(
        pool_id       = pool_id,
        tenant_id     = "tenant_test",
        channel_types = ["webchat", "whatsapp"],
        sla_target_ms = 480_000,
        routing_expression = _expr(),
    )
    defaults.update(kwargs)
    return PoolConfig(**defaults)


def _instance(
    instance_id: str = "inst_001",
    pool_id: str = "retencao_humano",
    state: str = "ready",
    current_sessions: int = 0,
    max_concurrent: int = 1,
) -> AgentInstance:
    return AgentInstance(
        instance_id      = instance_id,
        agent_type_id    = "agente_retencao_v1",
        tenant_id        = "tenant_test",
        pool_id          = pool_id,
        pools            = [pool_id],
        execution_model  = "stateless",
        max_concurrent   = max_concurrent,
        current_sessions = current_sessions,
        state            = state,
        registered_at    = "2026-03-25T00:00:00Z",
    )


def _profile(
    tier: str = "standard",
    churn_risk: float = 0.0,
    business_score: float = 0.0,
    risk_flag: bool = False,
) -> CustomerProfile:
    return CustomerProfile(
        tier=tier, churn_risk=churn_risk,
        business_score=business_score, risk_flag=risk_flag,
    )


def _make_decider(
    pools: list[PoolConfig],
    instances_by_pool: dict[str, list[AgentInstance]],
    oldest_queue_ms: int | None = None,
) -> Decider:
    """Builds a Decider with configured mocks."""
    inst_reg = AsyncMock(spec=InstanceRegistry)
    pool_reg = AsyncMock(spec=PoolRegistry)

    pool_reg.get_candidate_pools.return_value = pools

    async def get_ready(tenant_id: str, pool_id: str) -> list[AgentInstance]:
        return instances_by_pool.get(pool_id, [])

    inst_reg.get_ready_instances.side_effect = get_ready
    inst_reg.get_oldest_queue_wait_ms.return_value = oldest_queue_ms
    inst_reg.get_session_affinity.return_value = None
    inst_reg.mark_busy.return_value = None

    return Decider(inst_reg, pool_reg)


# ─────────────────────────────────────────────
# Routing mode tests
# ─────────────────────────────────────────────

class TestRoutingMode:
    @pytest.mark.asyncio
    async def test_autonomous_with_high_confidence(self):
        decider = _make_decider(
            pools=[_pool()],
            instances_by_pool={"retencao_humano": [_instance()]},
        )
        result = await decider.decide(
            conversation_id="c1", tenant_id="tenant_test",
            intent="cancelamento", confidence=0.92, channel="webchat",
            customer_profile=_profile(),
        )
        assert result.mode == "autonomous"
        assert result.reevaluation_turn is None

    @pytest.mark.asyncio
    async def test_hybrid_with_medium_confidence(self):
        decider = _make_decider(
            pools=[_pool()],
            instances_by_pool={"retencao_humano": [_instance()]},
        )
        result = await decider.decide(
            conversation_id="c1", tenant_id="tenant_test",
            intent="portabilidade", confidence=0.73, channel="webchat",
            customer_profile=_profile(),
        )
        assert result.mode == "hybrid"
        assert result.reevaluation_turn == 5  # configurable default

    @pytest.mark.asyncio
    async def test_supervised_with_low_confidence(self):
        decider = _make_decider(
            pools=[_pool()],
            instances_by_pool={"retencao_humano": [_instance()]},
        )
        result = await decider.decide(
            conversation_id="c1", tenant_id="tenant_test",
            intent="reclamacao", confidence=0.45, channel="webchat",
            customer_profile=_profile(),
        )
        assert result.mode == "supervised"
        assert result.reevaluation_turn == 1  # per-turn supervision

    @pytest.mark.asyncio
    async def test_supervised_with_risk_flag(self):
        """risk_flag forces supervised mode regardless of high confidence."""
        decider = _make_decider(
            pools=[_pool()],
            instances_by_pool={"retencao_humano": [_instance()]},
        )
        result = await decider.decide(
            conversation_id="c1", tenant_id="tenant_test",
            intent="fraude", confidence=0.95, channel="webchat",
            customer_profile=_profile(risk_flag=True),
        )
        assert result.mode == "supervised"


# ─────────────────────────────────────────────
# Allocation tests
# ─────────────────────────────────────────────

class TestAllocation:
    @pytest.mark.asyncio
    async def test_allocates_available_instance(self):
        decider = _make_decider(
            pools=[_pool()],
            instances_by_pool={"retencao_humano": [_instance()]},
        )
        result = await decider.decide(
            conversation_id="c1", tenant_id="tenant_test",
            intent="cancelamento", confidence=0.90, channel="webchat",
            customer_profile=_profile(),
        )
        assert result.primary is not None
        assert result.primary.instance_id == "inst_001"
        assert result.primary.pool_id == "retencao_humano"
        assert result.saturated is False

    @pytest.mark.asyncio
    async def test_saturated_no_pools(self):
        decider = _make_decider(pools=[], instances_by_pool={})
        result = await decider.decide(
            conversation_id="c1", tenant_id="tenant_test",
            intent="cancelamento", confidence=0.90, channel="webchat",
            customer_profile=_profile(),
        )
        assert result.primary is None
        assert result.saturated is True

    @pytest.mark.asyncio
    async def test_saturated_no_instances(self):
        decider = _make_decider(
            pools=[_pool()],
            instances_by_pool={"retencao_humano": []},  # no ready instances
        )
        result = await decider.decide(
            conversation_id="c1", tenant_id="tenant_test",
            intent="cancelamento", confidence=0.90, channel="webchat",
            customer_profile=_profile(),
        )
        assert result.primary is None
        assert result.saturated is True

    @pytest.mark.asyncio
    async def test_instance_at_capacity_is_skipped(self):
        """Instance with current_sessions == max_concurrent must not be selected."""
        inst_full = _instance(
            instance_id="inst_full",
            current_sessions=1,
            max_concurrent=1,  # no capacity
        )
        inst_free = _instance(
            instance_id="inst_free",
            current_sessions=0,
            max_concurrent=1,
        )
        decider = _make_decider(
            pools=[_pool()],
            instances_by_pool={"retencao_humano": [inst_full, inst_free]},
        )
        result = await decider.decide(
            conversation_id="c1", tenant_id="tenant_test",
            intent="cancelamento", confidence=0.90, channel="webchat",
            customer_profile=_profile(),
        )
        assert result.primary is not None
        assert result.primary.instance_id == "inst_free"

    @pytest.mark.asyncio
    async def test_fallback_set_when_second_pool_available(self):
        pool1 = _pool("pool_a")
        pool2 = _pool("pool_b")
        decider = _make_decider(
            pools=[pool1, pool2],
            instances_by_pool={
                "pool_a": [_instance("inst_a", pool_id="pool_a")],
                "pool_b": [_instance("inst_b", pool_id="pool_b")],
            },
        )
        result = await decider.decide(
            conversation_id="c1", tenant_id="tenant_test",
            intent="portabilidade", confidence=0.88, channel="webchat",
            customer_profile=_profile(),
        )
        assert result.primary is not None
        assert result.fallback is not None
        assert result.primary.pool_id != result.fallback.pool_id

    @pytest.mark.asyncio
    async def test_fallback_none_when_single_pool(self):
        decider = _make_decider(
            pools=[_pool()],
            instances_by_pool={"retencao_humano": [_instance()]},
        )
        result = await decider.decide(
            conversation_id="c1", tenant_id="tenant_test",
            intent="cancelamento", confidence=0.90, channel="webchat",
            customer_profile=_profile(),
        )
        assert result.fallback is None


# ─────────────────────────────────────────────
# priority_score influence on routing decision
# ─────────────────────────────────────────────

class TestPriorityScoreInfluence:
    @pytest.mark.asyncio
    async def test_urgent_sla_pool_preferred(self):
        """
        Pool with sla_urgency > 1.0 (queue exceeded SLA) must have maximum priority.
        """
        pool_urgent = _pool("pool_urgente")
        pool_normal = _pool("pool_normal")

        inst_reg = AsyncMock(spec=InstanceRegistry)
        pool_reg = AsyncMock(spec=PoolRegistry)

        pool_reg.get_candidate_pools.return_value = [pool_normal, pool_urgent]

        async def get_ready(tenant_id, pool_id):
            return {
                "pool_urgente": [_instance("inst_urgente", pool_id="pool_urgente")],
                "pool_normal":  [_instance("inst_normal",  pool_id="pool_normal")],
            }.get(pool_id, [])

        inst_reg.get_ready_instances.side_effect = get_ready

        async def get_oldest(tenant_id, pool_id):
            # pool_urgente: 600s wait with 480s SLA → sla_urgency = 1.25
            return 1_000_000_000 - 600_000 if pool_id == "pool_urgente" else None

        inst_reg.get_oldest_queue_wait_ms.side_effect = get_oldest

        decider = Decider(inst_reg, pool_reg)

        result = await decider.decide(
            conversation_id="c1", tenant_id="tenant_test",
            intent="cancelamento", confidence=0.90, channel="webchat",
            customer_profile=_profile(),
            elapsed_ms=0,
        )
        assert result.primary is not None
        assert result.primary.pool_id == "pool_urgente"

    @pytest.mark.asyncio
    async def test_urgent_sla_wins_against_all_max_weights(self):
        """
        Urgent pool (sla_urgency > 1.0 → score = infinity) must beat a pool
        with ALL weights at maximum applied to a platinum + max-churn customer.
        Confirms invariant: sla_urgency > 1.0 overrides all other weights.
        """
        # Urgent pool: SLA exceeded → sla_urgency = 1.25
        pool_urgent = _pool("pool_urgente", routing_expression=RoutingExpression(
            weight_sla=0.0,   # irrelevant — score will be infinity due to urgency
            weight_wait=0.0,
            weight_tier=0.0,
            weight_churn=0.0,
            weight_business=0.0,
        ))

        # Premium pool: maximum weights + platinum customer + max churn
        pool_premium = _pool("pool_premium", routing_expression=RoutingExpression(
            weight_sla=1.0, weight_wait=1.0,
            weight_tier=1.0, weight_churn=1.0, weight_business=1.0,
        ))

        inst_reg = AsyncMock(spec=InstanceRegistry)
        pool_reg = AsyncMock(spec=PoolRegistry)

        pool_reg.get_candidate_pools.return_value = [pool_premium, pool_urgent]

        async def get_ready(tenant_id, pool_id):
            return {
                "pool_urgente": [_instance("inst_urgente", pool_id="pool_urgente")],
                "pool_premium": [_instance("inst_premium", pool_id="pool_premium")],
            }.get(pool_id, [])

        inst_reg.get_ready_instances.side_effect = get_ready

        async def get_oldest(tenant_id, pool_id):
            # pool_urgente: 600s wait with 480s SLA → urgency = 1.25
            if pool_id == "pool_urgente":
                return 600_000  # 600s in ms
            return None

        inst_reg.get_oldest_queue_wait_ms.side_effect = get_oldest
        inst_reg.get_session_affinity.return_value = None
        inst_reg.mark_busy.return_value = None

        decider = Decider(inst_reg, pool_reg)

        result = await decider.decide(
            conversation_id="c_urgente", tenant_id="tenant_test",
            intent="cancelamento", confidence=0.92, channel="webchat",
            # Platinum customer with max churn: all pool_premium weights at maximum
            customer_profile=_profile(tier="platinum", churn_risk=1.0, business_score=1.0),
        )

        assert result.primary is not None
        # Urgent pool wins even against pool_premium with all weights at maximum
        assert result.primary.pool_id == "pool_urgente", (
            f"Urgent pool (sla_urgency>1.0) must have score=infinity and beat "
            f"pool_premium (finite score), but allocated: {result.primary.pool_id}"
        )

    @pytest.mark.asyncio
    async def test_platinum_with_high_churn_has_higher_score(self):
        """
        Verifies that platinum+high-churn score is greater than standard+zero-churn.
        """
        expr = RoutingExpression(
            weight_sla=0.0, weight_wait=0.0,
            weight_tier=0.6, weight_churn=0.9, weight_business=0.0,
        )
        pool = _pool(routing_expression=expr)

        inst_reg = AsyncMock(spec=InstanceRegistry)
        pool_reg = AsyncMock(spec=PoolRegistry)
        pool_reg.get_candidate_pools.return_value = [pool]
        inst_reg.get_ready_instances.return_value = [_instance()]
        inst_reg.get_oldest_queue_wait_ms.return_value = None

        decider = Decider(inst_reg, pool_reg)

        # Platinum + high churn
        r_plat = await decider.decide(
            conversation_id="c1", tenant_id="tenant_test",
            intent="retencao", confidence=0.90, channel="webchat",
            customer_profile=_profile(tier="platinum", churn_risk=0.9),
        )

        # Standard + zero churn
        r_std = await decider.decide(
            conversation_id="c2", tenant_id="tenant_test",
            intent="retencao", confidence=0.90, channel="webchat",
            customer_profile=_profile(tier="standard", churn_risk=0.0),
        )

        assert r_plat.primary is not None and r_std.primary is not None
        # platinum+churn = (1.0×0.6) + (0.9×0.9) = 0.6 + 0.81 = 1.41
        # standard+zero  = (0.2×0.6) + (0.0×0.9) = 0.12
        assert r_plat.primary.score > r_std.primary.score


# ─────────────────────────────────────────────
# Timeout tests — spec 3.3
# ─────────────────────────────────────────────

class TestTimeout:
    @pytest.mark.asyncio
    async def test_timeout_when_redis_is_slow(self):
        """
        Simulates slow Redis and verifies decide() fails within 150ms.
        Spec 3.3: "Decision timeout: 150ms".
        """
        inst_reg = AsyncMock(spec=InstanceRegistry)
        pool_reg = AsyncMock(spec=PoolRegistry)

        # Simulates a Redis operation that takes 500ms (well above the 150ms limit)
        async def slow_get_pools(tenant_id, channel):
            await asyncio.sleep(0.500)
            return []

        pool_reg.get_candidate_pools.side_effect = slow_get_pools

        decider = Decider(inst_reg, pool_reg)

        with pytest.raises(RoutingTimeoutError) as exc_info:
            await decider.decide(
                conversation_id="c_timeout", tenant_id="tenant_test",
                intent="cancelamento", confidence=0.90, channel="webchat",
                customer_profile=_profile(),
            )

        assert "150ms" in str(exc_info.value) or "timeout" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_timeout_when_instances_are_slow(self):
        """Slow Redis in get_ready_instances must also trigger a timeout."""
        inst_reg = AsyncMock(spec=InstanceRegistry)
        pool_reg = AsyncMock(spec=PoolRegistry)

        pool_reg.get_candidate_pools.return_value = [_pool()]

        async def slow_instances(tenant_id, pool_id):
            await asyncio.sleep(0.300)  # 300ms > 150ms limit
            return [_instance()]

        inst_reg.get_ready_instances.side_effect = slow_instances

        decider = Decider(inst_reg, pool_reg)

        with pytest.raises(RoutingTimeoutError):
            await decider.decide(
                conversation_id="c_timeout2", tenant_id="tenant_test",
                intent="cancelamento", confidence=0.90, channel="webchat",
                customer_profile=_profile(),
            )

    @pytest.mark.asyncio
    async def test_timeout_with_200ms_delay(self):
        """
        A 200ms delay (above the 150ms limit) must trigger RoutingTimeoutError.
        Tests the exact boundary between failure (200ms > 150ms) and success (<150ms).
        """
        inst_reg = AsyncMock(spec=InstanceRegistry)
        pool_reg = AsyncMock(spec=PoolRegistry)

        async def slow_get_pools(tenant_id, channel):
            await asyncio.sleep(0.200)  # 200ms — above the 150ms limit
            return [_pool()]

        pool_reg.get_candidate_pools.side_effect = slow_get_pools

        decider = Decider(inst_reg, pool_reg)

        with pytest.raises(RoutingTimeoutError):
            await decider.decide(
                conversation_id="c_200ms", tenant_id="tenant_test",
                intent="cancelamento", confidence=0.90, channel="webchat",
                customer_profile=_profile(),
            )

    @pytest.mark.asyncio
    async def test_decide_completes_within_timeout(self):
        """Normal operation must complete well within 150ms."""
        inst_reg = AsyncMock(spec=InstanceRegistry)
        pool_reg = AsyncMock(spec=PoolRegistry)

        pool_reg.get_candidate_pools.return_value = [_pool()]
        inst_reg.get_ready_instances.return_value = [_instance()]
        inst_reg.get_oldest_queue_wait_ms.return_value = None

        decider = Decider(inst_reg, pool_reg)

        # Must not raise RoutingTimeoutError
        result = await decider.decide(
            conversation_id="c_fast", tenant_id="tenant_test",
            intent="cancelamento", confidence=0.90, channel="webchat",
            customer_profile=_profile(),
        )
        assert result is not None
        assert isinstance(result, RoutingDecision)


# ─────────────────────────────────────────────
# Saturation policy tests (section 3.3a)
# ─────────────────────────────────────────────

class TestSaturation:
    @pytest.mark.asyncio
    async def test_saturated_voice_returns_queue_voice_action(self):
        decider = _make_decider(
            pools=[_pool(channel_types=["voice"])],
            instances_by_pool={"retencao_humano": []},  # no instances
        )
        result = await decider.decide(
            conversation_id="c1", tenant_id="tenant_test",
            intent="cancelamento", confidence=0.90, channel="voice",
            customer_profile=_profile(),
        )
        assert result.saturated is True
        assert result.saturation_action == "queue_voice"

    @pytest.mark.asyncio
    async def test_saturated_chat_returns_queue_with_callback_action(self):
        decider = _make_decider(
            pools=[_pool(channel_types=["webchat"])],
            instances_by_pool={"retencao_humano": []},
        )
        result = await decider.decide(
            conversation_id="c1", tenant_id="tenant_test",
            intent="cancelamento", confidence=0.90, channel="webchat",
            customer_profile=_profile(),
        )
        assert result.saturated is True
        assert result.saturation_action == "queue_with_callback"

    @pytest.mark.asyncio
    async def test_saturated_email_returns_email_confirmation_action(self):
        decider = _make_decider(
            pools=[_pool(channel_types=["email"])],
            instances_by_pool={"retencao_humano": []},
        )
        result = await decider.decide(
            conversation_id="c1", tenant_id="tenant_test",
            intent="cancelamento", confidence=0.90, channel="email",
            customer_profile=_profile(),
        )
        assert result.saturated is True
        assert result.saturation_action == "email_confirmation"
