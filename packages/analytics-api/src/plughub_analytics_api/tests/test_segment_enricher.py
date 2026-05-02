"""
test_segment_enricher.py
Unit tests for SegmentEnricher (Arc 5 — post-hoc segment_id enrichment).

Coverage:
  - lookup_by_instance: cache hit, Redis hit, ClickHouse fallback, all-fail
  - lookup_primary:     cache hit, Redis hit, ClickHouse fallback, all-fail
  - Cache deduplication (no second Redis/CH call after first hit)
  - Cache eviction (FIFO at MAX_CACHE_SIZE)
  - Enriched parse_sentiment_event passes segment_id
  - Enriched parse_mcp_audit_event passes segment_id
  - parse_mcp_audit_event skips events without session_id
  - _parse_with_enrichment dispatch via consumer helper
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from plughub_analytics_api.segment_enricher import SegmentEnricher, _MAX_CACHE_SIZE
from plughub_analytics_api.models import parse_sentiment_event, parse_mcp_audit_event
from plughub_analytics_api.consumer import _parse_with_enrichment


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_enricher(
    redis_val: str | None = None,
    redis_raises: bool = False,
    ch_val: str | None = None,
    ch_raises: bool = False,
) -> SegmentEnricher:
    """Returns a SegmentEnricher with mocked Redis and ClickHouse store."""
    redis = AsyncMock()
    if redis_raises:
        redis.get.side_effect = Exception("redis down")
    else:
        redis.get.return_value = redis_val

    store = AsyncMock()
    if ch_raises:
        store.lookup_segment_id.side_effect = Exception("ch down")
        store.lookup_primary_segment_id.side_effect = Exception("ch down")
    else:
        store.lookup_segment_id.return_value = ch_val
        store.lookup_primary_segment_id.return_value = ch_val

    return SegmentEnricher(redis, store), redis, store


# ── lookup_by_instance ────────────────────────────────────────────────────────

class TestLookupByInstance:
    def setup_method(self):
        self.session_id  = "sess_abc"
        self.instance_id = "agente_sac_v1-001"
        self.tenant_id   = "tenant_demo"
        self.seg_id      = "seg_111"

    @pytest.mark.asyncio
    async def test_returns_none_when_session_id_missing(self):
        enricher, _, _ = _make_enricher(redis_val=self.seg_id)
        result = await enricher.lookup_by_instance("", self.instance_id, self.tenant_id)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_when_instance_id_missing(self):
        enricher, _, _ = _make_enricher(redis_val=self.seg_id)
        result = await enricher.lookup_by_instance(self.session_id, "", self.tenant_id)
        assert result is None

    @pytest.mark.asyncio
    async def test_redis_hit_returns_segment_id(self):
        enricher, redis, store = _make_enricher(redis_val=self.seg_id)
        result = await enricher.lookup_by_instance(self.session_id, self.instance_id, self.tenant_id)
        assert result == self.seg_id
        redis.get.assert_awaited_once_with(f"session:{self.session_id}:segment:{self.instance_id}")
        store.lookup_segment_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cache_hit_skips_redis(self):
        enricher, redis, store = _make_enricher(redis_val=self.seg_id)
        # First call populates cache
        await enricher.lookup_by_instance(self.session_id, self.instance_id, self.tenant_id)
        redis.get.reset_mock()
        # Second call should use cache
        result = await enricher.lookup_by_instance(self.session_id, self.instance_id, self.tenant_id)
        assert result == self.seg_id
        redis.get.assert_not_awaited()
        store.lookup_segment_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_redis_miss_falls_back_to_clickhouse(self):
        enricher, redis, store = _make_enricher(redis_val=None, ch_val=self.seg_id)
        result = await enricher.lookup_by_instance(self.session_id, self.instance_id, self.tenant_id)
        assert result == self.seg_id
        redis.get.assert_awaited_once()
        store.lookup_segment_id.assert_awaited_once_with(
            self.tenant_id, self.session_id, self.instance_id
        )

    @pytest.mark.asyncio
    async def test_redis_error_falls_back_to_clickhouse(self):
        enricher, _, store = _make_enricher(redis_raises=True, ch_val=self.seg_id)
        result = await enricher.lookup_by_instance(self.session_id, self.instance_id, self.tenant_id)
        assert result == self.seg_id
        store.lookup_segment_id.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_all_fail_returns_none(self):
        enricher, _, _ = _make_enricher(redis_val=None, ch_val=None)
        result = await enricher.lookup_by_instance(self.session_id, self.instance_id, self.tenant_id)
        assert result is None

    @pytest.mark.asyncio
    async def test_all_errors_returns_none(self):
        enricher, _, _ = _make_enricher(redis_raises=True, ch_raises=True)
        result = await enricher.lookup_by_instance(self.session_id, self.instance_id, self.tenant_id)
        assert result is None


# ── lookup_primary ────────────────────────────────────────────────────────────

class TestLookupPrimary:
    def setup_method(self):
        self.session_id = "sess_xyz"
        self.tenant_id  = "tenant_demo"
        self.seg_id     = "seg_primary_999"

    @pytest.mark.asyncio
    async def test_returns_none_when_session_id_missing(self):
        enricher, _, _ = _make_enricher(redis_val=self.seg_id)
        result = await enricher.lookup_primary("", self.tenant_id)
        assert result is None

    @pytest.mark.asyncio
    async def test_redis_hit_returns_segment_id(self):
        enricher, redis, store = _make_enricher(redis_val=self.seg_id)
        result = await enricher.lookup_primary(self.session_id, self.tenant_id)
        assert result == self.seg_id
        redis.get.assert_awaited_once_with(f"session:{self.session_id}:primary_segment")
        store.lookup_primary_segment_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cache_hit_skips_redis(self):
        enricher, redis, store = _make_enricher(redis_val=self.seg_id)
        await enricher.lookup_primary(self.session_id, self.tenant_id)
        redis.get.reset_mock()
        result = await enricher.lookup_primary(self.session_id, self.tenant_id)
        assert result == self.seg_id
        redis.get.assert_not_awaited()
        store.lookup_primary_segment_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_redis_miss_falls_back_to_clickhouse(self):
        enricher, _, store = _make_enricher(redis_val=None, ch_val=self.seg_id)
        result = await enricher.lookup_primary(self.session_id, self.tenant_id)
        assert result == self.seg_id
        store.lookup_primary_segment_id.assert_awaited_once_with(self.tenant_id, self.session_id)

    @pytest.mark.asyncio
    async def test_redis_error_falls_back_to_clickhouse(self):
        enricher, _, store = _make_enricher(redis_raises=True, ch_val=self.seg_id)
        result = await enricher.lookup_primary(self.session_id, self.tenant_id)
        assert result == self.seg_id
        store.lookup_primary_segment_id.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_all_fail_returns_none(self):
        enricher, _, _ = _make_enricher(redis_val=None, ch_val=None)
        result = await enricher.lookup_primary(self.session_id, self.tenant_id)
        assert result is None


# ── Cache eviction ────────────────────────────────────────────────────────────

class TestCacheEviction:
    @pytest.mark.asyncio
    async def test_cache_evicts_oldest_half_when_full(self):
        """When cache hits MAX_CACHE_SIZE, the oldest half is evicted."""
        redis = AsyncMock()
        redis.get.return_value = "seg_value"
        store = AsyncMock()
        store.lookup_segment_id.return_value = None

        enricher = SegmentEnricher(redis, store)

        # Fill cache to MAX_CACHE_SIZE - 1 using synthetic keys
        for i in range(_MAX_CACHE_SIZE - 1):
            enricher._cache[(f"sess_{i}", f"inst_{i}")] = f"seg_{i}"

        assert len(enricher._cache) == _MAX_CACHE_SIZE - 1

        # One more real lookup will fill to MAX and not evict yet
        await enricher.lookup_by_instance("sess_X", "inst_X", "tenant_demo")
        assert len(enricher._cache) == _MAX_CACHE_SIZE

        # Next lookup triggers eviction (oldest half removed, then new entry added)
        await enricher.lookup_by_instance("sess_Y", "inst_Y", "tenant_demo")
        # After eviction: _MAX_CACHE_SIZE // 2 removed, then 1 added
        expected = _MAX_CACHE_SIZE - _MAX_CACHE_SIZE // 2 + 1
        assert len(enricher._cache) == expected

        # The earliest entries should be gone
        assert ("sess_0", "inst_0") not in enricher._cache
        # But sess_Y should be present
        assert ("sess_Y", "inst_Y") in enricher._cache


# ── Enriched parse_sentiment_event ────────────────────────────────────────────

class TestEnrichedSentimentEvent:
    def test_sentiment_event_with_segment_id(self):
        payload = {
            "event_id":   "evt_001",
            "tenant_id":  "tenant_demo",
            "session_id": "sess_abc",
            "pool_id":    "pool_sac",
            "score":      0.75,
            "category":   "satisfied",
            "timestamp":  "2026-05-01T10:00:00Z",
        }
        result = parse_sentiment_event(payload, segment_id="seg_111")
        assert result is not None
        assert result["segment_id"] == "seg_111"
        assert result["table"] == "sentiment_events"
        assert result["score"] == 0.75
        assert result["category"] == "satisfied"

    def test_sentiment_event_without_segment_id_defaults_to_none(self):
        payload = {
            "event_id":   "evt_002",
            "tenant_id":  "tenant_demo",
            "session_id": "sess_abc",
            "score":      -0.3,
            "category":   "frustrated",
            "timestamp":  "2026-05-01T10:00:01Z",
        }
        result = parse_sentiment_event(payload)
        assert result is not None
        assert result["segment_id"] is None

    def test_sentiment_event_missing_required_fields_returns_none(self):
        assert parse_sentiment_event({}) is None
        assert parse_sentiment_event({"event_id": "x", "tenant_id": "t"}) is None


# ── Enriched parse_mcp_audit_event ────────────────────────────────────────────

class TestEnrichedMcpAuditEvent:
    def _base_payload(self):
        return {
            "event_type":  "mcp.tool_call",
            "tenant_id":   "tenant_demo",
            "session_id":  "sess_abc",
            "instance_id": "agente_sac_v1-001",
            "server_name": "mcp-server-crm",
            "tool_name":   "customer_get",
            "allowed":     True,
            "injection_detected": False,
            "duration_ms": 45,
            "source":      "in_process",
            "timestamp":   "2026-05-01T10:00:00Z",
        }

    def test_mcp_audit_with_segment_id(self):
        result = parse_mcp_audit_event(self._base_payload(), segment_id="seg_222")
        assert result is not None
        assert result["table"] == "session_timeline"
        assert result["segment_id"] == "seg_222"
        assert result["event_type"] == "mcp.tool_call"
        assert result["actor_id"] == "agente_sac_v1-001"
        assert result["actor_role"] == "agent"

    def test_mcp_audit_without_segment_id_uses_empty_string(self):
        result = parse_mcp_audit_event(self._base_payload())
        assert result is not None
        assert result["segment_id"] == ""

    def test_mcp_audit_without_session_id_returns_none(self):
        payload = self._base_payload()
        del payload["session_id"]
        assert parse_mcp_audit_event(payload) is None

    def test_mcp_audit_without_tenant_id_returns_none(self):
        payload = self._base_payload()
        del payload["tenant_id"]
        assert parse_mcp_audit_event(payload) is None

    def test_mcp_audit_payload_field_is_valid_json(self):
        result = parse_mcp_audit_event(self._base_payload(), segment_id="seg_x")
        assert result is not None
        parsed = json.loads(result["payload"])
        assert parsed["tool_name"] == "customer_get"
        assert parsed["allowed"] is True
        assert parsed["duration_ms"] == 45

    def test_mcp_audit_blocked_call(self):
        payload = self._base_payload()
        payload["allowed"] = False
        payload["injection_detected"] = True
        result = parse_mcp_audit_event(payload)
        assert result is not None
        parsed = json.loads(result["payload"])
        assert parsed["allowed"] is False
        assert parsed["injection_detected"] is True


# ── _parse_with_enrichment (consumer helper) ──────────────────────────────────

class TestParseWithEnrichment:
    @pytest.mark.asyncio
    async def test_sentiment_enrichment_dispatches_lookup_primary(self):
        enricher = AsyncMock(spec=SegmentEnricher)
        enricher.lookup_primary.return_value = "seg_primary_abc"

        raw = {
            "event_id":   "evt_s1",
            "tenant_id":  "tenant_demo",
            "session_id": "sess_001",
            "pool_id":    "pool_sac",
            "score":      0.5,
            "category":   "neutral",
            "timestamp":  "2026-05-01T10:00:00Z",
        }

        result = await _parse_with_enrichment(
            raw, "sentiment.updated", parse_sentiment_event, enricher
        )

        enricher.lookup_primary.assert_awaited_once_with("sess_001", "tenant_demo")
        assert result is not None
        assert result["segment_id"] == "seg_primary_abc"

    @pytest.mark.asyncio
    async def test_mcp_audit_enrichment_dispatches_lookup_by_instance(self):
        enricher = AsyncMock(spec=SegmentEnricher)
        enricher.lookup_by_instance.return_value = "seg_inst_xyz"

        raw = {
            "tenant_id":   "tenant_demo",
            "session_id":  "sess_001",
            "instance_id": "agente_sac_v1-001",
            "server_name": "mcp-server-crm",
            "tool_name":   "customer_get",
            "allowed":     True,
            "injection_detected": False,
            "duration_ms": 10,
            "source":      "in_process",
            "timestamp":   "2026-05-01T10:00:00Z",
        }

        result = await _parse_with_enrichment(
            raw, "mcp.audit", parse_mcp_audit_event, enricher
        )

        enricher.lookup_by_instance.assert_awaited_once_with(
            "sess_001", "agente_sac_v1-001", "tenant_demo"
        )
        assert result is not None
        assert result["segment_id"] == "seg_inst_xyz"

    @pytest.mark.asyncio
    async def test_enrichment_failure_still_calls_parser_with_none(self):
        """If enrichment raises, result should still be parsed with segment_id=None."""
        enricher = AsyncMock(spec=SegmentEnricher)
        enricher.lookup_primary.side_effect = Exception("enrichment error")

        raw = {
            "event_id":   "evt_fail",
            "tenant_id":  "tenant_demo",
            "session_id": "sess_001",
            "pool_id":    "pool_sac",
            "score":      0.0,
            "category":   "neutral",
            "timestamp":  "2026-05-01T10:00:00Z",
        }

        result = await _parse_with_enrichment(
            raw, "sentiment.updated", parse_sentiment_event, enricher
        )

        # Parser should still produce a row, but with segment_id=None
        assert result is not None
        assert result["segment_id"] is None
