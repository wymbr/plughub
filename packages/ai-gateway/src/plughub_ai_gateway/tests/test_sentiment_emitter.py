"""
test_sentiment_emitter.py
Unit tests for AI Gateway sentiment emitter.

Strategy:
  - All functions are fire-and-forget async; tested by inspecting mock calls.
  - Redis state is fully mocked (hgetall → dict, hset/expire capture args).
  - Kafka producer is a simple AsyncMock.
  - Error path: producer/redis raises → function returns without propagating.
  - Classify: boundary values, midpoints, edge cases.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from ..sentiment_emitter import (
    _classify,
    emit_sentiment_updated,
    update_sentiment_live,
    write_context_store_sentiment,
)

TENANT  = "tenant_telco"
SESSION = "sess-sentiment-001"
POOL    = "retencao_humano"


# ── helpers ───────────────────────────────────────────────────────────────────

def make_producer() -> MagicMock:
    p = MagicMock()
    p.send = AsyncMock()
    return p


def make_redis(existing: dict | None = None) -> MagicMock:
    """Redis mock. hgetall returns existing dict (default empty = new key)."""
    r = MagicMock()
    r.hgetall = AsyncMock(return_value=existing or {})
    r.hset    = AsyncMock()
    r.expire  = AsyncMock()
    r.hget    = AsyncMock(return_value=None)
    return r


def captured_kafka_event(producer: MagicMock) -> dict:
    call_kwargs = producer.send.call_args
    value_bytes: bytes = call_kwargs.kwargs.get("value") or call_kwargs.args[1]
    return json.loads(value_bytes)


# ── _classify ─────────────────────────────────────────────────────────────────

class TestClassify:
    def test_satisfied_above_threshold(self):
        assert _classify(0.3)  == "satisfied"
        assert _classify(0.5)  == "satisfied"
        assert _classify(1.0)  == "satisfied"

    def test_neutral_range(self):
        assert _classify(0.0)  == "neutral"
        assert _classify(0.29) == "neutral"
        assert _classify(-0.29) == "neutral"

    def test_frustrated_range(self):
        # frustrated: [-0.6, -0.3) — -0.3 itself is neutral (neutral wins at overlap)
        assert _classify(-0.31) == "frustrated"
        assert _classify(-0.5)  == "frustrated"
        assert _classify(-0.59) == "frustrated"

    def test_angry_range(self):
        # angry: [-1.0, -0.6) — -0.6 itself is frustrated (frustrated wins at overlap)
        assert _classify(-0.61) == "angry"
        assert _classify(-0.8)  == "angry"
        assert _classify(-1.0)  == "angry"

    def test_boundary_neutral_satisfied(self):
        # 0.3 is the satisfied boundary (inclusive on satisfied side)
        assert _classify(0.3)   == "satisfied"
        assert _classify(0.299) == "neutral"

    def test_boundary_frustrated_neutral(self):
        # -0.3 is neutral (included in neutral range [-0.3, 0.3])
        assert _classify(-0.3)   == "neutral"
        assert _classify(-0.301) == "frustrated"

    def test_boundary_angry_frustrated(self):
        # -0.6 is frustrated (included in frustrated range [-0.6, -0.3))
        assert _classify(-0.6)   == "frustrated"
        assert _classify(-0.601) == "angry"


# ── emit_sentiment_updated ────────────────────────────────────────────────────

class TestEmitSentimentUpdated:
    async def test_sends_to_sentiment_updated_topic(self):
        p = make_producer()
        await emit_sentiment_updated(p, TENANT, SESSION, POOL, 0.5)
        topic = p.send.call_args.args[0]
        assert topic == "sentiment.updated"

    async def test_event_fields_satisfied(self):
        p = make_producer()
        await emit_sentiment_updated(p, TENANT, SESSION, POOL, 0.75)
        ev = captured_kafka_event(p)
        assert ev["tenant_id"]  == TENANT
        assert ev["session_id"] == SESSION
        assert ev["pool_id"]    == POOL
        assert ev["score"]      == 0.75
        assert ev["category"]   == "satisfied"
        assert "event_id"       in ev
        assert "timestamp"      in ev

    async def test_category_neutral(self):
        p = make_producer()
        await emit_sentiment_updated(p, TENANT, SESSION, POOL, 0.0)
        ev = captured_kafka_event(p)
        assert ev["category"] == "neutral"

    async def test_category_frustrated(self):
        p = make_producer()
        await emit_sentiment_updated(p, TENANT, SESSION, POOL, -0.4)
        ev = captured_kafka_event(p)
        assert ev["category"] == "frustrated"

    async def test_category_angry(self):
        p = make_producer()
        await emit_sentiment_updated(p, TENANT, SESSION, POOL, -0.8)
        ev = captured_kafka_event(p)
        assert ev["category"] == "angry"

    async def test_event_id_unique(self):
        p = make_producer()
        await emit_sentiment_updated(p, TENANT, SESSION, POOL, 0.5)
        await emit_sentiment_updated(p, TENANT, SESSION, POOL, 0.5)
        calls = p.send.call_args_list
        id1 = json.loads(calls[0].kwargs["value"])["event_id"]
        id2 = json.loads(calls[1].kwargs["value"])["event_id"]
        assert id1 != id2

    async def test_score_rounded_to_4_decimals(self):
        p = make_producer()
        await emit_sentiment_updated(p, TENANT, SESSION, POOL, 0.123456789)
        ev = captured_kafka_event(p)
        assert ev["score"] == 0.1235  # round to 4

    async def test_silently_ignores_producer_error(self):
        p = make_producer()
        p.send = AsyncMock(side_effect=Exception("kafka down"))
        # Must not raise
        await emit_sentiment_updated(p, TENANT, SESSION, POOL, 0.5)

    async def test_none_producer_is_noop(self):
        # Should return without error when producer is None
        await emit_sentiment_updated(None, TENANT, SESSION, POOL, 0.5)


# ── update_sentiment_live ─────────────────────────────────────────────────────

class TestUpdateSentimentLive:
    async def test_creates_key_from_scratch(self):
        r = make_redis(existing={})  # empty hash → new key
        await update_sentiment_live(r, TENANT, POOL, 0.5, SESSION)
        r.hset.assert_called_once()
        mapping = r.hset.call_args.kwargs["mapping"]
        assert mapping["count"]       == "1"
        assert mapping["avg_score"]   == "0.5"
        assert mapping["score_total"] == "0.5"
        assert mapping["satisfied"]   == "1"
        assert mapping["last_session_id"] == SESSION

    async def test_increments_count_on_second_call(self):
        r = make_redis(existing={
            "count": "1", "score_total": "0.5",
            "satisfied": "1",
        })
        await update_sentiment_live(r, TENANT, POOL, -0.2, SESSION)  # neutral
        mapping = r.hset.call_args.kwargs["mapping"]
        assert mapping["count"]     == "2"
        assert mapping["neutral"]   == "1"
        assert float(mapping["avg_score"]) == pytest.approx(0.15, abs=1e-3)

    async def test_avg_score_running_mean(self):
        # Simulate 3 accumulated turns: 0.8, 0.6, 0.4 → avg 0.6
        r = make_redis(existing={
            "count": "2", "score_total": "1.4",
            "satisfied": "2",
        })
        await update_sentiment_live(r, TENANT, POOL, 0.4, SESSION)
        mapping = r.hset.call_args.kwargs["mapping"]
        assert float(mapping["avg_score"]) == pytest.approx(1.8 / 3, abs=1e-4)
        assert mapping["count"] == "3"

    async def test_expire_called_with_correct_ttl(self):
        r = make_redis()
        await update_sentiment_live(r, TENANT, POOL, 0.1, SESSION)
        r.expire.assert_called_once()
        args = r.expire.call_args.args
        assert args[1] == 300  # _SENTIMENT_LIVE_TTL

    async def test_key_format(self):
        r = make_redis()
        await update_sentiment_live(r, TENANT, POOL, 0.1, SESSION)
        key_arg = r.hgetall.call_args.args[0]
        assert key_arg == f"{TENANT}:pool:{POOL}:sentiment_live"

    async def test_updated_at_present(self):
        r = make_redis()
        await update_sentiment_live(r, TENANT, POOL, 0.0, SESSION)
        mapping = r.hset.call_args.kwargs["mapping"]
        assert "updated_at" in mapping
        assert len(mapping["updated_at"]) > 10

    async def test_angry_category_incremented(self):
        r = make_redis(existing={"count": "1", "score_total": "-0.8", "angry": "1"})
        await update_sentiment_live(r, TENANT, POOL, -0.9, SESSION)
        mapping = r.hset.call_args.kwargs["mapping"]
        assert mapping["angry"] == "2"

    async def test_silently_ignores_redis_error(self):
        r = make_redis()
        r.hgetall = AsyncMock(side_effect=Exception("redis timeout"))
        # Must not raise
        await update_sentiment_live(r, TENANT, POOL, 0.5, SESSION)

    async def test_none_redis_is_noop(self):
        # Should return without error when redis is None
        await update_sentiment_live(None, TENANT, POOL, 0.5, SESSION)


# ── SessionManager integration ────────────────────────────────────────────────

class TestSessionManagerSentimentIntegration:
    """
    Verifies that update_partial_params wires sentiment emitters correctly.
    Isolates SessionManager from real Redis by mocking at the method level.
    """

    async def test_pool_id_looked_up_from_meta(self):
        from unittest.mock import patch, AsyncMock as AM
        from ..session import SessionManager

        redis = make_redis()
        redis.get    = AM(return_value=None)   # no existing AI state
        redis.set    = AM()
        redis.publish = AM()
        redis.hget   = AM(return_value="retencao_humano")

        producer = make_producer()
        mgr = SessionManager(redis, kafka_producer=producer)

        with patch("plughub_ai_gateway.session.emit_sentiment_updated", new_callable=AM) as mock_emit, \
             patch("plughub_ai_gateway.session.update_sentiment_live",  new_callable=AM) as mock_live:
            await mgr.update_partial_params(
                session_id      = SESSION,
                tenant_id       = TENANT,
                elapsed_ms      = 120,
                intent          = "cancelamento",
                confidence      = 0.9,
                sentiment_score = -0.4,
                flags           = [],
            )
            mock_emit.assert_called_once()
            call_kwargs = mock_emit.call_args.kwargs
            assert call_kwargs["tenant_id"]  == TENANT
            assert call_kwargs["session_id"] == SESSION
            assert call_kwargs["pool_id"]    == "retencao_humano"
            assert call_kwargs["score"]      == -0.4

            mock_live.assert_called_once()
            live_kwargs = mock_live.call_args.kwargs
            assert live_kwargs["tenant_id"] == TENANT
            assert live_kwargs["pool_id"]   == "retencao_humano"
            assert live_kwargs["score"]     == -0.4

    async def test_unknown_pool_id_when_meta_missing(self):
        from unittest.mock import patch, AsyncMock as AM
        from ..session import SessionManager

        redis = make_redis()
        redis.get    = AM(return_value=None)
        redis.set    = AM()
        redis.publish = AM()
        redis.hget   = AM(return_value=None)  # pool_id not found

        mgr = SessionManager(redis, kafka_producer=make_producer())

        with patch("plughub_ai_gateway.session.emit_sentiment_updated", new_callable=AM) as mock_emit, \
             patch("plughub_ai_gateway.session.update_sentiment_live",  new_callable=AM):
            await mgr.update_partial_params(
                session_id      = SESSION,
                tenant_id       = TENANT,
                elapsed_ms      = 50,
                intent          = None,
                confidence      = 0.0,
                sentiment_score = 0.0,
                flags           = [],
            )
            # pool_id falls back to "unknown"
            call_kwargs = mock_emit.call_args.kwargs
            assert call_kwargs["pool_id"] == "unknown"


# ── write_context_store_sentiment ─────────────────────────────────────────────

class TestWriteContextStoreSentiment:
    async def test_writes_to_correct_key(self):
        r = make_redis()
        await write_context_store_sentiment(r, TENANT, SESSION, 0.5)
        key_arg = r.hset.call_args.args[0]
        assert key_arg == f"{TENANT}:ctx:{SESSION}"

    async def test_writes_two_fields(self):
        r = make_redis()
        await write_context_store_sentiment(r, TENANT, SESSION, 0.5)
        mapping = r.hset.call_args.kwargs["mapping"]
        assert "session.sentimento.current"   in mapping
        assert "session.sentimento.categoria" in mapping

    async def test_current_entry_value_matches_score(self):
        r = make_redis()
        await write_context_store_sentiment(r, TENANT, SESSION, -0.4)
        mapping = r.hset.call_args.kwargs["mapping"]
        entry = json.loads(mapping["session.sentimento.current"])
        assert entry["value"]      == -0.4
        assert entry["confidence"] == 0.80
        assert entry["source"]     == "ai_inferred:sentiment_emitter"
        assert entry["visibility"] == "agents_only"
        assert "updated_at" in entry

    async def test_categoria_entry_maps_to_label(self):
        r = make_redis()
        await write_context_store_sentiment(r, TENANT, SESSION, -0.7)  # angry
        mapping = r.hset.call_args.kwargs["mapping"]
        entry = json.loads(mapping["session.sentimento.categoria"])
        assert entry["value"] == "angry"
        assert entry["confidence"] == 0.80
        assert entry["visibility"] == "agents_only"

    async def test_satisfied_score(self):
        r = make_redis()
        await write_context_store_sentiment(r, TENANT, SESSION, 0.8)
        mapping = r.hset.call_args.kwargs["mapping"]
        cat_entry = json.loads(mapping["session.sentimento.categoria"])
        assert cat_entry["value"] == "satisfied"

    async def test_expire_called_with_session_ttl(self):
        r = make_redis()
        await write_context_store_sentiment(r, TENANT, SESSION, 0.0)
        r.expire.assert_called_once()
        key, ttl = r.expire.call_args.args
        assert key == f"{TENANT}:ctx:{SESSION}"
        assert ttl == 14_400  # _CTX_SESSION_TTL

    async def test_score_rounded_to_4_decimals(self):
        r = make_redis()
        await write_context_store_sentiment(r, TENANT, SESSION, 0.123456789)
        mapping = r.hset.call_args.kwargs["mapping"]
        entry = json.loads(mapping["session.sentimento.current"])
        assert entry["value"] == 0.1235  # round to 4

    async def test_none_redis_is_noop(self):
        await write_context_store_sentiment(None, TENANT, SESSION, 0.5)

    async def test_silently_ignores_redis_error(self):
        r = make_redis()
        r.hset = AsyncMock(side_effect=Exception("redis error"))
        # Must not raise
        await write_context_store_sentiment(r, TENANT, SESSION, 0.5)

    async def test_integration_wired_in_session_manager(self):
        """Verifies write_context_store_sentiment is called by update_partial_params."""
        from unittest.mock import patch, AsyncMock as AM
        from ..session import SessionManager

        redis = make_redis()
        redis.get     = AM(return_value=None)
        redis.set     = AM()
        redis.publish = AM()
        redis.hget    = AM(return_value="retencao_humano")

        mgr = SessionManager(redis, kafka_producer=make_producer())

        with patch("plughub_ai_gateway.session.emit_sentiment_updated",       new_callable=AM), \
             patch("plughub_ai_gateway.session.update_sentiment_live",         new_callable=AM), \
             patch("plughub_ai_gateway.session.write_context_store_sentiment", new_callable=AM) as mock_ctx:
            await mgr.update_partial_params(
                session_id      = SESSION,
                tenant_id       = TENANT,
                elapsed_ms      = 80,
                intent          = "cancelamento",
                confidence      = 0.9,
                sentiment_score = -0.55,
                flags           = [],
            )
            mock_ctx.assert_called_once()
            ctx_kwargs = mock_ctx.call_args.kwargs
            assert ctx_kwargs["tenant_id"]  == TENANT
            assert ctx_kwargs["session_id"] == SESSION
            assert ctx_kwargs["score"]      == -0.55
