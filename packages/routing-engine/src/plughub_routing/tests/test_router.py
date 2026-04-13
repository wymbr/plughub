"""
test_router.py
Tests for the routing flow.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock
from ..router import Router
from ..models import (
    ConversationInboundEvent, CustomerProfile,
    AgentInstance, PoolConfig, RoutingExpression,
)


def make_event(**kwargs) -> ConversationInboundEvent:
    defaults = {
        "session_id":  "session-001",
        "tenant_id":   "tenant_test",
        "customer_id": "customer-001",
        "channel":     "chat",
        "confidence":  0.90,
        "customer_profile": CustomerProfile(),
        "started_at":  "2026-03-16T14:00:00Z",
    }
    defaults.update(kwargs)
    return ConversationInboundEvent(**defaults)


def make_instance(**kwargs) -> AgentInstance:
    defaults = {
        "instance_id":     "inst_001",
        "agent_type_id":   "agente_retencao_v1",
        "tenant_id":       "tenant_test",
        "pool_id":         "retencao_humano",
        "execution_model": "stateless",
        "max_concurrent":  1,
        "current_sessions": 0,
        "state":           "ready",
        "registered_at":   "2026-03-16T13:00:00Z",
    }
    defaults.update(kwargs)
    return AgentInstance(**defaults)


def make_pool() -> PoolConfig:
    return PoolConfig(
        pool_id="retencao_humano",
        tenant_id="tenant_test",
        channel_types=["chat", "whatsapp"],
        sla_target_ms=480_000,
        routing_expression=RoutingExpression(),
    )


@pytest.mark.asyncio
async def test_allocates_available_instance():
    inst_reg  = AsyncMock()
    pool_reg  = AsyncMock()
    inst_reg.get_session_affinity.return_value = None
    pool_reg.get_candidate_pools.return_value  = [make_pool()]
    inst_reg.get_ready_instances.return_value  = [make_instance()]
    inst_reg.mark_busy.return_value            = None

    router = Router(inst_reg, pool_reg)
    result = await router.route(make_event())

    assert result.allocated is True
    assert result.instance_id == "inst_001"
    assert result.pool_id     == "retencao_humano"
    assert result.priority_score >= 0


@pytest.mark.asyncio
async def test_queues_when_no_instances():
    inst_reg = AsyncMock()
    pool_reg = AsyncMock()
    inst_reg.get_session_affinity.return_value = None
    pool_reg.get_candidate_pools.return_value  = [make_pool()]
    inst_reg.get_ready_instances.return_value  = []  # no instances

    router = Router(inst_reg, pool_reg)
    result = await router.route(make_event())

    assert result.allocated is False
    assert result.queued    is True


@pytest.mark.asyncio
async def test_queues_when_no_pools():
    inst_reg = AsyncMock()
    pool_reg = AsyncMock()
    inst_reg.get_session_affinity.return_value = None
    pool_reg.get_candidate_pools.return_value  = []  # no pools

    router = Router(inst_reg, pool_reg)
    result = await router.route(make_event())

    assert result.allocated is False
    assert result.queued    is True


@pytest.mark.asyncio
async def test_routing_mode_autonomous_high_confidence():
    inst_reg = AsyncMock()
    pool_reg = AsyncMock()
    inst_reg.get_session_affinity.return_value = None
    pool_reg.get_candidate_pools.return_value  = [make_pool()]
    inst_reg.get_ready_instances.return_value  = [make_instance()]
    inst_reg.mark_busy.return_value            = None

    router = Router(inst_reg, pool_reg)
    result = await router.route(make_event(confidence=0.92))

    assert result.routing_mode == "autonomous"


@pytest.mark.asyncio
async def test_routing_mode_supervised_low_confidence():
    inst_reg = AsyncMock()
    pool_reg = AsyncMock()
    inst_reg.get_session_affinity.return_value = None
    pool_reg.get_candidate_pools.return_value  = [make_pool()]
    inst_reg.get_ready_instances.return_value  = [make_instance()]
    inst_reg.mark_busy.return_value            = None

    router = Router(inst_reg, pool_reg)
    result = await router.route(make_event(confidence=0.40))

    assert result.routing_mode == "supervised"


@pytest.mark.asyncio
async def test_session_affinity_registered_for_stateful():
    inst_reg = AsyncMock()
    pool_reg = AsyncMock()
    inst_reg.get_session_affinity.return_value = None
    pool_reg.get_candidate_pools.return_value  = [make_pool()]
    stateful_instance = make_instance(execution_model="stateful")
    inst_reg.get_ready_instances.return_value  = [stateful_instance]
    inst_reg.mark_busy.return_value            = None
    inst_reg.set_session_affinity              = AsyncMock()

    router = Router(inst_reg, pool_reg)
    await router.route(make_event())

    # Must have registered session affinity
    inst_reg.set_session_affinity.assert_called_once()
