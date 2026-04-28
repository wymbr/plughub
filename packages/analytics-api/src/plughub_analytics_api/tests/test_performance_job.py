"""
test_performance_job.py
Arc 7d — Unit tests for analytics-api performance_job.

Tests:
  TestComputePerformanceScore (6)  — formula correctness + edge cases
  TestRunPerformanceSync     (6)  — Redis writes, error handling, return values
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from plughub_analytics_api.performance_job import (
    compute_performance_score,
    run_performance_sync,
    PERF_KEY_TTL,
    LOOKBACK_DAYS,
    MIN_SESSIONS,
)


# ─── TestComputePerformanceScore ──────────────────────────────────────────────

class TestComputePerformanceScore:
    """Score formula: resolution_rate × (1 − min(escalation_rate, 1.0))"""

    def test_perfect_agent(self):
        """resolution=1.0, escalation=0.0 → 1.0"""
        assert compute_performance_score(1.0, 0.0) == 1.0

    def test_typical_agent(self):
        """resolution=0.8, escalation=0.2 → 0.64"""
        score = compute_performance_score(0.8, 0.2)
        assert score == pytest.approx(0.64, abs=1e-4)

    def test_never_resolves(self):
        """resolution=0.0, escalation=any → 0.0"""
        assert compute_performance_score(0.0, 0.5) == 0.0
        assert compute_performance_score(0.0, 0.0) == 0.0

    def test_always_escalates(self):
        """escalation=1.0, resolution=any → 0.0"""
        assert compute_performance_score(1.0, 1.0) == 0.0
        assert compute_performance_score(0.9, 1.0) == 0.0

    def test_escalation_capped_at_1(self):
        """escalation > 1.0 is clamped to 1.0 → score still 0.0"""
        assert compute_performance_score(0.8, 1.5) == 0.0

    def test_result_in_range(self):
        """Result is always within [0.0, 1.0]"""
        for res, esc in [(0.5, 0.3), (0.99, 0.01), (0.1, 0.9)]:
            s = compute_performance_score(res, esc)
            assert 0.0 <= s <= 1.0, f"Out of range for res={res}, esc={esc}"


# ─── TestRunPerformanceSync ───────────────────────────────────────────────────

class TestRunPerformanceSync:
    """Tests for run_performance_sync() — ClickHouse query + Redis writes."""

    def _make_store(self, rows: list) -> MagicMock:
        store = MagicMock()
        store._database = "analytics"
        result = MagicMock()
        result.result_rows = rows
        store._client.query = MagicMock(return_value=result)
        return store

    def _make_redis(self) -> AsyncMock:
        redis = AsyncMock()
        redis.setex = AsyncMock(return_value=True)
        return redis

    @pytest.mark.asyncio
    async def test_writes_redis_key_per_row(self):
        """One Redis key written per (tenant, agent_type) row."""
        rows = [
            ("tenant_demo", "agente_sac_v1", 100, 0.85, 0.05),
            ("tenant_demo", "agente_billing_v1", 50, 0.70, 0.10),
        ]
        store = self._make_store(rows)
        redis = self._make_redis()

        result = await run_performance_sync(store, redis)

        assert result["updated"] == 2
        assert result["errors"] == 0
        assert redis.setex.call_count == 2

    @pytest.mark.asyncio
    async def test_redis_key_format(self):
        """Redis key follows {tenant_id}:agent_perf:{agent_type_id} pattern."""
        rows = [("tenant_xyz", "agente_test_v1", 20, 0.9, 0.1)]
        store = self._make_store(rows)
        redis = self._make_redis()

        await run_performance_sync(store, redis)

        call_args = redis.setex.call_args
        key = call_args[0][0]
        assert key == "tenant_xyz:agent_perf:agente_test_v1"

    @pytest.mark.asyncio
    async def test_redis_ttl_applied(self):
        """PERF_KEY_TTL is passed as the TTL argument to setex."""
        rows = [("tenant_demo", "agente_sac_v1", 10, 0.8, 0.1)]
        store = self._make_store(rows)
        redis = self._make_redis()

        await run_performance_sync(store, redis)

        call_args = redis.setex.call_args
        ttl = call_args[0][1]
        assert ttl == PERF_KEY_TTL

    @pytest.mark.asyncio
    async def test_score_value_written(self):
        """Score stored is the result of compute_performance_score."""
        rows = [("tenant_demo", "agente_sac_v1", 10, 1.0, 0.0)]
        store = self._make_store(rows)
        redis = self._make_redis()

        await run_performance_sync(store, redis)

        call_args = redis.setex.call_args
        value = call_args[0][2]
        # resolution=1.0, escalation=0.0 → score=1.0
        assert float(value) == pytest.approx(1.0, abs=1e-4)

    @pytest.mark.asyncio
    async def test_clickhouse_error_returns_error_count(self):
        """When ClickHouse raises, returns updated=0, errors=1."""
        store = MagicMock()
        store._database = "analytics"
        store._client.query = MagicMock(side_effect=RuntimeError("CH down"))
        redis = self._make_redis()

        result = await run_performance_sync(store, redis)

        assert result["updated"] == 0
        assert result["errors"] == 1
        redis.setex.assert_not_called()

    @pytest.mark.asyncio
    async def test_redis_write_error_counted(self):
        """When Redis write fails, error count increments, other rows still processed."""
        rows = [
            ("tenant_demo", "agente_sac_v1", 10, 0.9, 0.05),
            ("tenant_demo", "agente_billing_v1", 10, 0.8, 0.1),
        ]
        store = self._make_store(rows)
        redis = self._make_redis()
        # Fail on first call, succeed on second
        redis.setex = AsyncMock(side_effect=[RuntimeError("Redis error"), True])

        result = await run_performance_sync(store, redis)

        assert result["updated"] == 1
        assert result["errors"] == 1
