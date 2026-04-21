"""
test_consumer.py
Unit tests for the Analytics API event parsers (models.py) and
consumer routing logic (_write_row dispatch).

Strategy:
  - All parsers are pure functions — no I/O mocked.
  - ClickHouse writes are mocked via AsyncMock on AnalyticsStore methods.
  - Verifies: correct table targeting, required fields, None returns on bad input.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from ..models import (
    parse_inbound,
    parse_routed,
    parse_queued,
    parse_conversations_event,
    parse_agent_lifecycle,
    parse_usage_event,
    parse_sentiment_event,
    parse_queue_position,
)
from ..consumer import _write_row


# ── helpers ───────────────────────────────────────────────────────────────────

def make_store() -> MagicMock:
    s = MagicMock()
    s.upsert_session     = AsyncMock()
    s.insert_queue_event = AsyncMock()
    s.insert_agent_event = AsyncMock()
    s.insert_message     = AsyncMock()
    s.insert_usage_event = AsyncMock()
    s.insert_sentiment_event = AsyncMock()
    return s


SESSION = "sess-analytics-001"
TENANT  = "tenant_telco"
POOL    = "retencao_humano"


# ── parse_inbound ─────────────────────────────────────────────────────────────

class TestParseInbound:
    def test_returns_session_row(self):
        row = parse_inbound({
            "session_id": SESSION, "tenant_id": TENANT,
            "channel": "webchat", "pool_id": POOL,
            "started_at": "2026-01-01T10:00:00+00:00",
        })
        assert row is not None
        assert row["table"] == "sessions"
        assert row["session_id"] == SESSION
        assert row["tenant_id"] == TENANT
        assert row["channel"] == "webchat"
        assert row["pool_id"] == POOL
        assert row["closed_at"] is None

    def test_returns_none_without_session_id(self):
        assert parse_inbound({"tenant_id": TENANT, "channel": "webchat"}) is None

    def test_returns_none_without_tenant_id(self):
        assert parse_inbound({"session_id": SESSION, "channel": "webchat"}) is None

    def test_empty_pool_id_when_missing(self):
        row = parse_inbound({"session_id": SESSION, "tenant_id": TENANT})
        assert row["pool_id"] == ""


# ── parse_routed ──────────────────────────────────────────────────────────────

class TestParseRouted:
    def _payload(self, allocated=True):
        return {
            "session_id": SESSION,
            "tenant_id":  TENANT,
            "routed_at":  "2026-01-01T10:00:01+00:00",
            "result": {
                "allocated":    allocated,
                "pool_id":      POOL,
                "instance_id":  "inst-001",
                "routing_mode": "autonomous",
            },
        }

    def test_returns_two_rows(self):
        rows = parse_routed(self._payload())
        assert rows is not None
        assert len(rows) == 2
        tables = {r["table"] for r in rows}
        assert tables == {"sessions", "agent_events"}

    def test_sessions_row_has_pool_id(self):
        rows = parse_routed(self._payload())
        sess = next(r for r in rows if r["table"] == "sessions")
        assert sess["pool_id"] == POOL

    def test_agent_event_has_correct_type(self):
        rows = parse_routed(self._payload())
        ae = next(r for r in rows if r["table"] == "agent_events")
        assert ae["event_type"] == "routed"
        assert ae["routing_mode"] == "autonomous"
        assert ae["instance_id"] == "inst-001"

    def test_returns_none_without_session_id(self):
        assert parse_routed({"tenant_id": TENANT, "result": {}}) is None


# ── parse_queued ──────────────────────────────────────────────────────────────

class TestParseQueued:
    def _payload(self):
        return {
            "session_id": SESSION,
            "tenant_id":  TENANT,
            "routed_at":  "2026-01-01T10:00:02+00:00",
            "result":     {"pool_id": POOL},
        }

    def test_returns_sessions_and_queue_event(self):
        rows = parse_queued(self._payload())
        assert rows is not None
        tables = {r["table"] for r in rows}
        assert tables == {"sessions", "queue_events"}

    def test_queue_event_type_is_queued(self):
        rows = parse_queued(self._payload())
        qe = next(r for r in rows if r["table"] == "queue_events")
        assert qe["event_type"] == "queued"
        assert qe["pool_id"] == POOL


# ── parse_conversations_event ─────────────────────────────────────────────────

class TestParseConversationsEvent:
    def test_contact_open(self):
        rows = parse_conversations_event({
            "event_type": "contact_open",
            "session_id": SESSION, "tenant_id": TENANT,
            "channel": "webchat", "started_at": "2026-01-01T10:00:00+00:00",
        })
        assert rows is not None
        assert rows[0]["table"] == "sessions"
        assert rows[0]["channel"] == "webchat"

    def test_contact_closed_sets_closed_at(self):
        rows = parse_conversations_event({
            "event_type":  "contact_closed",
            "session_id":  SESSION, "tenant_id": TENANT,
            "channel":     "webchat",
            "started_at":  "2026-01-01T10:00:00+00:00",
            "ended_at":    "2026-01-01T10:05:00+00:00",
            "reason":      "flow_complete",
            "outcome":     "resolved",
        })
        assert rows is not None
        row = rows[0]
        assert row["table"] == "sessions"
        assert row["closed_at"] == "2026-01-01T10:05:00+00:00"
        assert row["close_reason"] == "flow_complete"
        assert row["outcome"] == "resolved"

    def test_message_sent(self):
        rows = parse_conversations_event({
            "event_type":   "message_sent",
            "session_id":   SESSION, "tenant_id": TENANT,
            "message_id":   "msg-001",
            "author_role":  "primary",
            "channel":      "webchat",
            "content_type": "text",
            "visibility":   "all",
            "timestamp":    "2026-01-01T10:01:00+00:00",
        })
        assert rows is not None
        row = rows[0]
        assert row["table"] == "messages"
        assert row["message_id"] == "msg-001"
        assert row["visibility"] == "all"

    def test_unknown_event_type_returns_none(self):
        assert parse_conversations_event({
            "event_type": "conference_agent_completed",
            "session_id": SESSION, "tenant_id": TENANT,
        }) is None

    def test_missing_ids_returns_none(self):
        assert parse_conversations_event({"event_type": "contact_open"}) is None


# ── parse_agent_lifecycle ─────────────────────────────────────────────────────

class TestParseAgentLifecycle:
    def test_agent_done_returns_row(self):
        row = parse_agent_lifecycle({
            "event":         "agent_done",
            "session_id":    SESSION,
            "tenant_id":     TENANT,
            "agent_type_id": "agente_retencao_v1",
            "pool_id":       POOL,
            "instance_id":   "inst-001",
            "outcome":       "resolved",
            "handoff_reason": None,
            "handle_time_ms": 12000,
        })
        assert row is not None
        assert row["table"] == "agent_events"
        assert row["event_type"] == "agent_done"
        assert row["outcome"] == "resolved"
        assert row["handle_time_ms"] == 12000

    def test_non_agent_done_returns_none(self):
        assert parse_agent_lifecycle({"event": "agent_ready", "session_id": SESSION, "tenant_id": TENANT}) is None
        assert parse_agent_lifecycle({"event": "agent_busy", "session_id": SESSION, "tenant_id": TENANT}) is None

    def test_missing_session_id_returns_none(self):
        assert parse_agent_lifecycle({"event": "agent_done", "tenant_id": TENANT}) is None


# ── parse_usage_event ─────────────────────────────────────────────────────────

class TestParseUsageEvent:
    def test_passthrough_fields(self):
        row = parse_usage_event({
            "event_id":         "evt-001",
            "tenant_id":        TENANT,
            "session_id":       SESSION,
            "dimension":        "llm_tokens_input",
            "quantity":         512,
            "source_component": "ai-gateway",
            "timestamp":        "2026-01-01T10:02:00+00:00",
        })
        assert row is not None
        assert row["table"] == "usage_events"
        assert row["event_id"] == "evt-001"
        assert row["dimension"] == "llm_tokens_input"
        assert row["quantity"] == 512

    def test_missing_required_fields_returns_none(self):
        assert parse_usage_event({"tenant_id": TENANT}) is None
        assert parse_usage_event({"event_id": "x", "tenant_id": TENANT}) is None


# ── parse_sentiment_event ─────────────────────────────────────────────────────

class TestParseSentimentEvent:
    def test_passthrough_fields(self):
        row = parse_sentiment_event({
            "event_id":   "evt-sent-001",
            "tenant_id":  TENANT,
            "session_id": SESSION,
            "pool_id":    POOL,
            "score":      0.72,
            "category":   "satisfied",
            "timestamp":  "2026-01-01T10:03:00+00:00",
        })
        assert row is not None
        assert row["table"] == "sentiment_events"
        assert row["score"] == 0.72
        assert row["category"] == "satisfied"
        assert row["pool_id"] == POOL

    def test_missing_required_returns_none(self):
        assert parse_sentiment_event({"tenant_id": TENANT}) is None


# ── parse_queue_position ──────────────────────────────────────────────────────

class TestParseQueuePosition:
    def test_returns_position_updated_row(self):
        row = parse_queue_position({
            "event":             "queue.position_updated",
            "session_id":        SESSION,
            "tenant_id":         TENANT,
            "pool_id":           POOL,
            "queue_length":      3,
            "estimated_wait_ms": 90000,
            "available_agents":  0,
            "published_at":      "2026-01-01T10:00:05+00:00",
        })
        assert row is not None
        assert row["table"] == "queue_events"
        assert row["event_type"] == "position_updated"
        assert row["queue_position"] == 3
        assert row["estimated_wait_ms"] == 90000
        assert row["available_agents"] == 0


# ── _write_row dispatch ───────────────────────────────────────────────────────

class TestWriteRowDispatch:
    async def test_sessions_dispatched_to_upsert_session(self):
        store = make_store()
        await _write_row(store, {"table": "sessions", "session_id": SESSION, "tenant_id": TENANT}, "topic", 0)
        store.upsert_session.assert_called_once()

    async def test_queue_events_dispatched(self):
        store = make_store()
        await _write_row(store, {"table": "queue_events"}, "topic", 0)
        store.insert_queue_event.assert_called_once()

    async def test_agent_events_dispatched(self):
        store = make_store()
        await _write_row(store, {"table": "agent_events"}, "topic", 0)
        store.insert_agent_event.assert_called_once()

    async def test_messages_dispatched(self):
        store = make_store()
        await _write_row(store, {"table": "messages"}, "topic", 0)
        store.insert_message.assert_called_once()

    async def test_usage_events_dispatched(self):
        store = make_store()
        await _write_row(store, {"table": "usage_events"}, "topic", 0)
        store.insert_usage_event.assert_called_once()

    async def test_sentiment_events_dispatched(self):
        store = make_store()
        await _write_row(store, {"table": "sentiment_events"}, "topic", 0)
        store.insert_sentiment_event.assert_called_once()

    async def test_unknown_table_does_not_call_any_method(self):
        store = make_store()
        await _write_row(store, {"table": "unknown_table"}, "topic", 0)
        store.upsert_session.assert_not_called()
        store.insert_queue_event.assert_not_called()
        store.insert_agent_event.assert_not_called()
