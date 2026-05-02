"""
test_instance_bootstrap.py
Unit tests for InstanceBootstrap — pool config divergence detection and
_write_instance safety guard.

Coverage:
  - B3-02: _pool_config_diverged detects changes to scoring fields
            (routing_expression, competency_weights, aging_factor, breach_factor,
             remote_sites)
  - B3-04: _write_instance does not overwrite a busy instance with status=ready;
            marks pending_update instead
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from plughub_orchestrator_bridge.instance_bootstrap import (
    InstanceBootstrap,
    _pool_config_diverged,
)


# ─── Helpers ──────────────────────────────────────────────────────────────────

TENANT = "tenant_demo"
INST   = "agente_demo_ia_v1-001"


def make_bootstrap(redis_mock) -> InstanceBootstrap:
    return InstanceBootstrap(
        redis=redis_mock,
        registry_url="http://agent-registry:3200",
        tenant_ids=[TENANT],
    )


# ─── TestPoolConfigDiverged (B3-02) ──────────────────────────────────────────

class TestPoolConfigDiverged:
    def test_identical_configs_not_diverged(self):
        cfg = {
            "pool_id": "pool_a",
            "channel_types": ["webchat"],
            "sla_target_ms": 60000,
            "routing_expression": {"weight_sla": 1.0},
        }
        assert _pool_config_diverged(cfg, cfg) is False

    def test_detects_routing_expression_change(self):
        existing = {"pool_id": "pool_a", "routing_expression": {"weight_sla": 1.0}}
        desired  = {"pool_id": "pool_a", "routing_expression": {"weight_sla": 1.5}}
        assert _pool_config_diverged(existing, desired) is True

    def test_detects_competency_weights_change(self):
        existing = {"pool_id": "pool_a", "competency_weights": {"billing": 0.5}}
        desired  = {"pool_id": "pool_a", "competency_weights": {"billing": 0.8}}
        assert _pool_config_diverged(existing, desired) is True

    def test_detects_aging_factor_change(self):
        existing = {"pool_id": "pool_a", "aging_factor": 0.4}
        desired  = {"pool_id": "pool_a", "aging_factor": 0.7}
        assert _pool_config_diverged(existing, desired) is True

    def test_detects_breach_factor_change(self):
        existing = {"pool_id": "pool_a", "breach_factor": 0.8}
        desired  = {"pool_id": "pool_a", "breach_factor": 1.2}
        assert _pool_config_diverged(existing, desired) is True

    def test_detects_remote_sites_change(self):
        existing = {"pool_id": "pool_a", "remote_sites": []}
        desired  = {"pool_id": "pool_a", "remote_sites": ["site-br-sp"]}
        assert _pool_config_diverged(existing, desired) is True

    def test_detects_channel_types_change(self):
        existing = {"pool_id": "pool_a", "channel_types": ["webchat"]}
        desired  = {"pool_id": "pool_a", "channel_types": ["webchat", "whatsapp"]}
        assert _pool_config_diverged(existing, desired) is True

    def test_detects_sla_change(self):
        existing = {"pool_id": "pool_a", "sla_target_ms": 60000}
        desired  = {"pool_id": "pool_a", "sla_target_ms": 30000}
        assert _pool_config_diverged(existing, desired) is True

    def test_ignores_timestamp_fields(self):
        """Fields not in MANAGED set (timestamps, audit) must not trigger divergence."""
        existing = {"pool_id": "pool_a", "sla_target_ms": 60000, "created_at": "2026-01-01"}
        desired  = {"pool_id": "pool_a", "sla_target_ms": 60000, "created_at": "2026-06-01"}
        assert _pool_config_diverged(existing, desired) is False


# ─── TestWriteInstanceGuard (B3-04) ──────────────────────────────────────────

class TestWriteInstanceGuard:
    @pytest.mark.asyncio
    async def test_write_instance_normal_path(self):
        """When no existing key, writes normally."""
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=None)
        redis.set = AsyncMock()
        redis.sadd = AsyncMock()

        bootstrap = make_bootstrap(redis)
        payload = {
            "instance_id": INST,
            "status": "ready",
            "pools": ["pool_demo"],
        }
        await bootstrap._write_instance(TENANT, INST, payload)

        redis.set.assert_called_once()
        written_key   = redis.set.call_args[0][0]
        written_value = json.loads(redis.set.call_args[0][1])

        assert written_key == f"{TENANT}:instance:{INST}"
        assert written_value["status"] == "ready"
        assert "pending_update" not in written_value

    @pytest.mark.asyncio
    async def test_write_instance_does_not_overwrite_busy(self):
        """Bootstrap must not overwrite a busy instance — marks pending_update instead."""
        existing = {
            "instance_id": INST,
            "status": "busy",
            "current_sessions": 1,
            "session_id": "sess_xyz",
        }
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=json.dumps(existing))
        redis.set = AsyncMock()
        redis.sadd = AsyncMock()

        bootstrap = make_bootstrap(redis)
        desired_payload = {
            "instance_id": INST,
            "status": "ready",
            "pools": ["pool_demo"],
        }
        await bootstrap._write_instance(TENANT, INST, desired_payload)

        # Must have written exactly once (the patch, not the desired payload)
        redis.set.assert_called_once()
        written_value = json.loads(redis.set.call_args[0][1])

        assert written_value["status"] == "busy",   "status must be preserved (busy)"
        assert written_value["pending_update"] is True, "pending_update must be set"
        # Pool SADDs must NOT happen (return early)
        redis.sadd.assert_not_called()

    @pytest.mark.asyncio
    async def test_write_instance_does_not_overwrite_paused(self):
        """Bootstrap must not overwrite a paused instance — same guard applies."""
        existing = {"instance_id": INST, "status": "paused"}
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=json.dumps(existing))
        redis.set = AsyncMock()
        redis.sadd = AsyncMock()

        bootstrap = make_bootstrap(redis)
        await bootstrap._write_instance(TENANT, INST, {"status": "ready", "pools": []})

        written = json.loads(redis.set.call_args[0][1])
        assert written["status"] == "paused"
        assert written["pending_update"] is True
        redis.sadd.assert_not_called()

    @pytest.mark.asyncio
    async def test_write_instance_overwrites_ready(self):
        """An existing ready instance CAN be overwritten (normal update path)."""
        existing = {"instance_id": INST, "status": "ready", "pools": ["pool_old"]}
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=json.dumps(existing))
        redis.set = AsyncMock()
        redis.sadd = AsyncMock()

        bootstrap = make_bootstrap(redis)
        new_payload = {"instance_id": INST, "status": "ready", "pools": ["pool_new"]}
        await bootstrap._write_instance(TENANT, INST, new_payload)

        written = json.loads(redis.set.call_args[0][1])
        assert written["pools"] == ["pool_new"]
        assert "pending_update" not in written

    @pytest.mark.asyncio
    async def test_write_instance_handles_corrupt_json(self):
        """Corrupt existing JSON must not block the write — safe to overwrite."""
        redis = AsyncMock()
        redis.get = AsyncMock(return_value=b"not valid json{{{")
        redis.set = AsyncMock()
        redis.sadd = AsyncMock()

        bootstrap = make_bootstrap(redis)
        payload = {"instance_id": INST, "status": "ready", "pools": []}
        await bootstrap._write_instance(TENANT, INST, payload)

        # Should proceed with the normal write despite corrupt JSON
        redis.set.assert_called_once()
        written = json.loads(redis.set.call_args[0][1])
        assert written["status"] == "ready"
