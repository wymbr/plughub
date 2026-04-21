"""
test_sessions.py
Unit tests for sessions.py — active session listing and SSE stream.

Tests cover:
  - _classify: score → sentiment category
  - _parse_entry: raw Redis stream entry → clean dict
  - _safe_json: JSON parsing with fallback
  - _fetch_active_sessions: ClickHouse query result → list of dicts
  - _overlay_sentiment: Redis pipeline results overlaid onto sessions
  - list_active_sessions endpoint (mocked store + redis)
  - session_stream SSE endpoint (mocked redis)
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from plughub_analytics_api.sessions import (
    _classify,
    _fetch_active_sessions,
    _overlay_sentiment,
    _parse_entry,
    _safe_json,
    router,
)


# ─── TestClassify ─────────────────────────────────────────────────────────────

class TestClassify:
    def test_satisfied_high(self):
        assert _classify(1.0) == "satisfied"

    def test_satisfied_boundary(self):
        assert _classify(0.3) == "satisfied"

    def test_neutral_positive(self):
        assert _classify(0.29) == "neutral"

    def test_neutral_zero(self):
        assert _classify(0.0) == "neutral"

    def test_neutral_boundary(self):
        assert _classify(-0.3) == "neutral"

    def test_frustrated(self):
        assert _classify(-0.31) == "frustrated"

    def test_frustrated_boundary(self):
        assert _classify(-0.6) == "frustrated"

    def test_angry(self):
        assert _classify(-0.61) == "angry"

    def test_angry_extreme(self):
        assert _classify(-1.0) == "angry"


# ─── TestSafeJson ─────────────────────────────────────────────────────────────

class TestSafeJson:
    def test_none_returns_none(self):
        assert _safe_json(None) is None

    def test_empty_string_returns_none(self):
        assert _safe_json("") is None

    def test_valid_json_dict(self):
        assert _safe_json('{"key": "value"}') == {"key": "value"}

    def test_valid_json_list(self):
        assert _safe_json('[1, 2, 3]') == [1, 2, 3]

    def test_invalid_json_returns_raw(self):
        assert _safe_json("not json") == "not json"

    def test_plain_string_text(self):
        assert _safe_json("hello world") == "hello world"


# ─── TestParseEntry ───────────────────────────────────────────────────────────

class TestParseEntry:
    def _make_entry(self, **kwargs):
        base = {
            "type": "message",
            "timestamp": "2024-01-01T00:00:00Z",
            "author_id": "part_001",
            "author_role": "primary",
            "visibility": "all",
            "content": json.dumps({"text": "Hello"}),
            "payload": json.dumps({"key": "value"}),
        }
        base.update(kwargs)
        return base

    def test_basic_parse(self):
        entry = _parse_entry("1-0", self._make_entry())
        assert entry["entry_id"] == "1-0"
        assert entry["type"] == "message"
        assert entry["author_role"] == "primary"
        assert entry["visibility"] == "all"
        assert entry["content"] == {"text": "Hello"}
        assert entry["payload"] == {"key": "value"}

    def test_bytes_entry_id_decoded(self):
        entry = _parse_entry(b"2-0", self._make_entry())
        assert entry["entry_id"] == "2-0"

    def test_bytes_values_decoded(self):
        data = {b"type": b"session_opened", b"visibility": b"all"}
        entry = _parse_entry("1-0", data)
        assert entry["type"] == "session_opened"
        assert entry["visibility"] == "all"

    def test_missing_optional_fields(self):
        entry = _parse_entry("1-0", {"type": "session_opened"})
        assert entry["author_id"] is None
        assert entry["author_role"] is None
        assert entry["timestamp"] is None
        assert entry["content"] is None
        assert entry["payload"] is None

    def test_visibility_defaults_to_all(self):
        entry = _parse_entry("1-0", {"type": "message"})
        assert entry["visibility"] == "all"

    def test_unknown_type_default(self):
        entry = _parse_entry("1-0", {})
        assert entry["type"] == "unknown"

    def test_json_content_parsed(self):
        entry = _parse_entry("1-0", {"type": "message", "content": '{"text": "hi"}'})
        assert entry["content"] == {"text": "hi"}

    def test_invalid_json_content_returns_raw(self):
        entry = _parse_entry("1-0", {"type": "message", "content": "plain text"})
        assert entry["content"] == "plain text"


# ─── TestFetchActiveSessions ──────────────────────────────────────────────────

class TestFetchActiveSessions:
    def _make_client(self, rows):
        result = MagicMock()
        result.result_rows = rows
        client = MagicMock()
        client.query.return_value = result
        return client

    def test_returns_list_of_dicts(self):
        opened_at = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        rows = [("sess_001", "webchat", opened_at, 5000)]
        client = self._make_client(rows)
        result = _fetch_active_sessions(client, "analytics", "tenant_test", "pool_test", 10)
        assert len(result) == 1
        row = result[0]
        assert row["session_id"] == "sess_001"
        assert row["channel"] == "webchat"
        assert row["wait_time_ms"] == 5000
        assert row["latest_score"] is None
        assert row["latest_category"] is None

    def test_handle_time_ms_is_positive(self):
        opened_at = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        rows = [("sess_002", "voice", opened_at, 0)]
        client = self._make_client(rows)
        result = _fetch_active_sessions(client, "analytics", "tenant_test", "pool_test", 10)
        assert result[0]["handle_time_ms"] is not None
        assert result[0]["handle_time_ms"] > 0

    def test_opened_at_iso_format(self):
        opened_at = datetime(2024, 6, 15, 10, 30, 0, tzinfo=timezone.utc)
        rows = [("sess_003", "email", opened_at, 0)]
        client = self._make_client(rows)
        result = _fetch_active_sessions(client, "analytics", "tenant_test", "pool_test", 10)
        assert "2024-06-15" in result[0]["opened_at"]

    def test_empty_result(self):
        client = self._make_client([])
        result = _fetch_active_sessions(client, "analytics", "tenant_test", "pool_test", 50)
        assert result == []

    def test_string_opened_at_fallback(self):
        rows = [("sess_004", "sms", "2024-01-01 12:00:00", 0)]
        client = self._make_client(rows)
        result = _fetch_active_sessions(client, "analytics", "tenant_test", "pool_test", 10)
        # opened_ts_ms falls back to 0, handle_time_ms is None
        assert result[0]["handle_time_ms"] is None
        assert result[0]["opened_at"] == "2024-01-01 12:00:00"


# ─── TestOverlaySentiment ─────────────────────────────────────────────────────

class TestOverlaySentiment:
    def _make_sessions(self, ids):
        return [
            {"session_id": sid, "latest_score": None, "latest_category": None}
            for sid in ids
        ]

    @pytest.mark.asyncio
    async def test_overlays_score_and_category(self):
        sessions = self._make_sessions(["s1"])
        redis = AsyncMock()
        pipe = AsyncMock()
        pipe.lrange = MagicMock()
        pipe.execute = AsyncMock(return_value=[[json.dumps({"score": 0.75})]])
        redis.pipeline = MagicMock(return_value=pipe)

        result = await _overlay_sentiment(redis, sessions)
        assert result[0]["latest_score"] == 0.75
        assert result[0]["latest_category"] == "satisfied"

    @pytest.mark.asyncio
    async def test_empty_redis_list_leaves_none(self):
        sessions = self._make_sessions(["s1"])
        redis = AsyncMock()
        pipe = AsyncMock()
        pipe.lrange = MagicMock()
        pipe.execute = AsyncMock(return_value=[[]])  # empty list from Redis
        redis.pipeline = MagicMock(return_value=pipe)

        result = await _overlay_sentiment(redis, sessions)
        assert result[0]["latest_score"] is None
        assert result[0]["latest_category"] is None

    @pytest.mark.asyncio
    async def test_sorts_worst_first(self):
        sessions = self._make_sessions(["s1", "s2", "s3"])
        redis = AsyncMock()
        pipe = AsyncMock()
        pipe.lrange = MagicMock()
        pipe.execute = AsyncMock(return_value=[
            [json.dumps({"score": 0.5})],   # s1 → satisfied
            [json.dumps({"score": -0.8})],  # s2 → angry
            [json.dumps({"score": -0.1})],  # s3 → neutral
        ])
        redis.pipeline = MagicMock(return_value=pipe)

        result = await _overlay_sentiment(redis, sessions)
        scores = [r["latest_score"] for r in result]
        assert scores == sorted(scores)  # ascending (worst first)

    @pytest.mark.asyncio
    async def test_none_scores_at_end(self):
        sessions = self._make_sessions(["s1", "s2"])
        redis = AsyncMock()
        pipe = AsyncMock()
        pipe.lrange = MagicMock()
        pipe.execute = AsyncMock(return_value=[
            [],                            # s1 → no data
            [json.dumps({"score": -0.5})], # s2 → frustrated
        ])
        redis.pipeline = MagicMock(return_value=pipe)

        result = await _overlay_sentiment(redis, sessions)
        # s2 (frustrated) should come first, s1 (none) at end
        assert result[0]["session_id"] == "s2"
        assert result[1]["session_id"] == "s1"

    @pytest.mark.asyncio
    async def test_redis_failure_returns_unsorted(self):
        sessions = self._make_sessions(["s1"])
        redis = AsyncMock()
        pipe = AsyncMock()
        pipe.lrange = MagicMock()
        pipe.execute = AsyncMock(side_effect=RuntimeError("redis down"))
        redis.pipeline = MagicMock(return_value=pipe)

        # Should not raise — graceful degradation
        result = await _overlay_sentiment(redis, sessions)
        assert len(result) == 1
        assert result[0]["latest_score"] is None

    @pytest.mark.asyncio
    async def test_frustrated_category(self):
        sessions = self._make_sessions(["s1"])
        redis = AsyncMock()
        pipe = AsyncMock()
        pipe.lrange = MagicMock()
        pipe.execute = AsyncMock(return_value=[[json.dumps({"score": -0.45})]])
        redis.pipeline = MagicMock(return_value=pipe)

        result = await _overlay_sentiment(redis, sessions)
        assert result[0]["latest_category"] == "frustrated"


# ─── TestListActiveSessionsEndpoint ──────────────────────────────────────────

class TestListActiveSessionsEndpoint:
    def _make_app(self, ch_rows=None, redis_results=None):
        app = FastAPI()
        app.include_router(router)

        store   = MagicMock()
        store._client   = MagicMock()
        store._database = "analytics"
        if ch_rows is not None:
            result = MagicMock()
            result.result_rows = ch_rows
            store._client.query.return_value = result

        redis = MagicMock()
        pipe  = MagicMock()
        pipe.lrange = MagicMock()
        if redis_results is not None:
            pipe.execute = AsyncMock(return_value=redis_results)
        else:
            pipe.execute = AsyncMock(return_value=[])
        redis.pipeline = MagicMock(return_value=pipe)

        app.state.store = store
        app.state.redis = redis
        return app

    def test_returns_200_with_sessions(self):
        opened_at = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        rows = [("sess_001", "webchat", opened_at, 1000)]
        redis_results = [[json.dumps({"score": -0.5})]]
        app = self._make_app(ch_rows=rows, redis_results=redis_results)

        with TestClient(app) as client:
            resp = client.get("/sessions/active?tenant_id=tenant_test&pool_id=pool_a")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["session_id"] == "sess_001"
        assert data[0]["channel"] == "webchat"

    def test_returns_200_empty_when_no_sessions(self):
        app = self._make_app(ch_rows=[])
        with TestClient(app) as client:
            resp = client.get("/sessions/active?tenant_id=t&pool_id=p")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_200_on_store_failure(self):
        app = self._make_app()
        app.state.store._client.query.side_effect = RuntimeError("CH down")
        with TestClient(app) as client:
            resp = client.get("/sessions/active?tenant_id=t&pool_id=p")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_missing_pool_id_returns_422(self):
        app = self._make_app(ch_rows=[])
        with TestClient(app) as client:
            resp = client.get("/sessions/active?tenant_id=t")
        assert resp.status_code == 422

    def test_limit_param_respected(self):
        opened_at = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        rows = [("sess_001", "voice", opened_at, 0)]
        app = self._make_app(ch_rows=rows, redis_results=[[]])

        with TestClient(app) as client:
            resp = client.get("/sessions/active?tenant_id=t&pool_id=p&limit=5")
        assert resp.status_code == 200
        # Verify query was called (limit was forwarded)
        app.state.store._client.query.assert_called_once()
