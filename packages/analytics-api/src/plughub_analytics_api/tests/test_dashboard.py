"""
test_dashboard.py
Unit tests for the Analytics API dashboard query helpers (query.py).

Strategy:
  - get_metrics_24h: mocks the ClickHouse client; verifies aggregation logic.
  - get_pool_snapshots: mocks Redis scan + mget; verifies snapshot parsing.
  - get_sentiment_live: mocks Redis scan + hgetall; verifies structure.
  - Error paths: client raises → function returns empty/zero result.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ..query import (
    get_metrics_24h,
    get_pool_snapshots,
    get_sentiment_live,
)

TENANT = "tenant_telco"
POOL   = "retencao_humano"
DB     = "plughub"


# ── helpers ───────────────────────────────────────────────────────────────────

def _ch_result(col_names: list[str], rows: list[list]) -> MagicMock:
    """Simulates a clickhouse_connect query result."""
    r = MagicMock()
    r.column_names = col_names
    r.result_rows  = rows
    return r


def make_ch_client(query_results: list) -> MagicMock:
    """
    Returns a mock ClickHouse client whose .query() returns successive results
    from query_results list (one per call).
    """
    client = MagicMock()
    client.query = MagicMock(side_effect=query_results)
    return client


# ── get_metrics_24h ───────────────────────────────────────────────────────────

class TestGetMetrics24h:
    def _build_client(
        self,
        sess_rows=None, ae_rows=None, usage_rows=None, sent_rows=None
    ):
        """Builds a client that returns 4 successive query results."""
        results = [
            _ch_result(
                ["total", "avg_handle_ms", "channel", "outcome", "close_reason"],
                sess_rows or [],
            ),
            _ch_result(["event_type", "outcome", "cnt"], ae_rows or []),
            _ch_result(["dimension", "total_qty"],         usage_rows or []),
            _ch_result(["category", "cnt", "avg_sc"],      sent_rows or []),
        ]
        return make_ch_client(results)

    async def test_returns_required_keys(self):
        client = self._build_client()
        data   = await get_metrics_24h(client, DB, TENANT)
        assert "sessions" in data
        assert "agent_events" in data
        assert "usage" in data
        assert "sentiment" in data
        assert data["tenant_id"] == TENANT
        assert data["period"] == "last_24h"

    async def test_session_totals_aggregated_correctly(self):
        client = self._build_client(
            sess_rows=[
                [10, 5000.0, "webchat", "resolved",   "flow_complete"],
                [5,  None,   "whatsapp", "transferred", "agent_transfer"],
            ]
        )
        data = await get_metrics_24h(client, DB, TENANT)
        sess = data["sessions"]
        assert sess["total"] == 15
        assert sess["by_channel"]["webchat"]  == 10
        assert sess["by_channel"]["whatsapp"] == 5
        assert sess["by_outcome"]["resolved"]    == 10
        assert sess["by_outcome"]["transferred"] == 5

    async def test_avg_handle_ms_ignores_none(self):
        client = self._build_client(
            sess_rows=[
                [10, 6000.0, "webchat", "resolved",   "flow_complete"],
                [5,  None,   "whatsapp", "transferred", "agent_transfer"],
            ]
        )
        data = await get_metrics_24h(client, DB, TENANT)
        # avg of [6000.0] only (None excluded)
        assert data["sessions"]["avg_handle_ms"] == 6000

    async def test_agent_events_counted(self):
        client = self._build_client(
            ae_rows=[
                ["routed",     None,       20],
                ["agent_done", "resolved", 15],
                ["agent_done", "transferred", 5],
            ]
        )
        data = await get_metrics_24h(client, DB, TENANT)
        ae = data["agent_events"]
        assert ae["total_routed"] == 20
        assert ae["total_done"]   == 20
        assert ae["by_outcome"]["resolved"]    == 15
        assert ae["by_outcome"]["transferred"] == 5

    async def test_usage_dimension_aggregated(self):
        client = self._build_client(
            usage_rows=[
                ["llm_tokens_input",  50000],
                ["llm_tokens_output", 12000],
                ["sessions",          37],
            ]
        )
        data = await get_metrics_24h(client, DB, TENANT)
        dims = data["usage"]["by_dimension"]
        assert dims["llm_tokens_input"]  == 50000
        assert dims["llm_tokens_output"] == 12000
        assert dims["sessions"]          == 37

    async def test_sentiment_weighted_avg(self):
        # satisfied: 20 samples, avg 0.6 → total_score = 12
        # neutral:   10 samples, avg 0.0 → total_score = 0
        # total 30, total_score 12 → avg = 0.4
        client = self._build_client(
            sent_rows=[
                ["satisfied", 20, 0.6],
                ["neutral",   10, 0.0],
            ]
        )
        data = await get_metrics_24h(client, DB, TENANT)
        sent = data["sentiment"]
        assert sent["sample_count"] == 30
        assert sent["avg_score"] == pytest.approx(0.4, abs=1e-4)
        assert sent["by_category"]["satisfied"] == 20
        assert sent["by_category"]["neutral"]   == 10

    async def test_error_returns_empty_metrics(self):
        client = MagicMock()
        client.query = MagicMock(side_effect=Exception("clickhouse down"))
        data = await get_metrics_24h(client, DB, TENANT)
        assert data.get("error") == "data_unavailable"
        assert data["sessions"]["total"] == 0

    async def test_no_data_returns_zeros(self):
        client = self._build_client()  # all empty results
        data = await get_metrics_24h(client, DB, TENANT)
        assert data["sessions"]["total"] == 0
        assert data["sessions"]["avg_handle_ms"] is None
        assert data["sentiment"]["avg_score"] is None


# ── get_pool_snapshots ────────────────────────────────────────────────────────

class TestGetPoolSnapshots:
    def make_redis(self, keys: list[str], values: list[str | None]) -> MagicMock:
        redis = MagicMock()
        # scan returns (0, keys) in one call then (0, []) to end loop
        redis.scan = AsyncMock(side_effect=[
            (0, keys),      # first call
        ])
        redis.mget = AsyncMock(return_value=values)
        return redis

    async def test_returns_parsed_snapshots(self):
        snap = {"pool_id": POOL, "tenant_id": TENANT, "available": 3, "queue_length": 0}
        redis = self.make_redis(
            [f"{TENANT}:pool:{POOL}:snapshot"],
            [json.dumps(snap)],
        )
        result = await get_pool_snapshots(redis, TENANT)
        assert len(result) == 1
        assert result[0]["pool_id"] == POOL
        assert result[0]["available"] == 3

    async def test_returns_empty_when_no_keys(self):
        redis = MagicMock()
        redis.scan = AsyncMock(return_value=(0, []))
        result = await get_pool_snapshots(redis, TENANT)
        assert result == []

    async def test_skips_malformed_json(self):
        redis = self.make_redis(
            [f"{TENANT}:pool:{POOL}:snapshot"],
            ["not-valid-json"],
        )
        result = await get_pool_snapshots(redis, TENANT)
        assert result == []

    async def test_skips_none_values(self):
        redis = self.make_redis(
            [f"{TENANT}:pool:{POOL}:snapshot"],
            [None],
        )
        result = await get_pool_snapshots(redis, TENANT)
        assert result == []

    async def test_error_returns_empty_list(self):
        redis = MagicMock()
        redis.scan = AsyncMock(side_effect=Exception("redis timeout"))
        result = await get_pool_snapshots(redis, TENANT)
        assert result == []


# ── get_sentiment_live ────────────────────────────────────────────────────────

class TestGetSentimentLive:
    def make_redis(self, keys: list[str], hgetall_data: list[dict]) -> MagicMock:
        redis = MagicMock()
        redis.scan    = AsyncMock(return_value=(0, keys))
        redis.hgetall = AsyncMock(side_effect=hgetall_data)
        return redis

    async def test_returns_structured_sentiment(self):
        key   = f"{TENANT}:pool:{POOL}:sentiment_live"
        hdata = {
            "avg_score": "0.45",
            "count": "37",
            "satisfied": "20",
            "neutral": "10",
            "frustrated": "5",
            "angry": "2",
            "last_session_id": "sess-001",
            "updated_at": "2026-01-01T10:00:00+00:00",
        }
        redis = self.make_redis([key], [hdata])
        result = await get_sentiment_live(redis, TENANT)
        assert len(result) == 1
        row = result[0]
        assert row["pool_id"]   == POOL
        assert row["avg_score"] == pytest.approx(0.45)
        assert row["count"]     == 37
        assert row["distribution"]["satisfied"] == 20
        assert row["distribution"]["angry"]     == 2
        assert row["last_session_id"] == "sess-001"

    async def test_returns_empty_when_no_keys(self):
        redis = MagicMock()
        redis.scan = AsyncMock(return_value=(0, []))
        result = await get_sentiment_live(redis, TENANT)
        assert result == []

    async def test_skips_empty_hashes(self):
        key   = f"{TENANT}:pool:{POOL}:sentiment_live"
        redis = self.make_redis([key], [{}])  # empty hash
        result = await get_sentiment_live(redis, TENANT)
        assert result == []

    async def test_error_returns_empty_list(self):
        redis = MagicMock()
        redis.scan = AsyncMock(side_effect=Exception("redis down"))
        result = await get_sentiment_live(redis, TENANT)
        assert result == []

    async def test_multiple_pools(self):
        keys = [
            f"{TENANT}:pool:pool_a:sentiment_live",
            f"{TENANT}:pool:pool_b:sentiment_live",
        ]
        hdata = [
            {"avg_score": "0.3", "count": "10", "satisfied": "5", "neutral": "5", "frustrated": "0", "angry": "0"},
            {"avg_score": "-0.2", "count": "8", "satisfied": "0", "neutral": "6", "frustrated": "2", "angry": "0"},
        ]
        redis = self.make_redis(keys, hdata)
        result = await get_sentiment_live(redis, TENANT)
        assert len(result) == 2
        pool_ids = {r["pool_id"] for r in result}
        assert pool_ids == {"pool_a", "pool_b"}
