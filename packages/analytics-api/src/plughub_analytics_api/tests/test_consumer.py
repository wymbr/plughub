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
    parse_participant_event,
    parse_evaluation_event,
)
from ..consumer import _write_row


# ── helpers ───────────────────────────────────────────────────────────────────

def make_store() -> MagicMock:
    s = MagicMock()
    s.upsert_session               = AsyncMock()
    s.insert_queue_event           = AsyncMock()
    s.insert_agent_event           = AsyncMock()
    s.insert_message               = AsyncMock()
    s.insert_usage_event           = AsyncMock()
    s.insert_sentiment_event       = AsyncMock()
    s.upsert_participation_interval = AsyncMock()
    s.upsert_segment               = AsyncMock()
    s.insert_timeline_event        = AsyncMock()
    s.upsert_evaluation_result     = AsyncMock()
    s.insert_evaluation_event      = AsyncMock()
    s.insert_contact_insight       = AsyncMock()
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

    async def test_participation_intervals_dispatched(self):
        store = make_store()
        await _write_row(store, {"table": "participation_intervals"}, "conversations.participants", 0)
        store.upsert_participation_interval.assert_called_once()

    async def test_segments_dispatched(self):
        store = make_store()
        await _write_row(store, {"table": "segments"}, "conversations.participants", 0)
        store.upsert_segment.assert_called_once()

    async def test_session_timeline_dispatched(self):
        store = make_store()
        await _write_row(store, {"table": "session_timeline"}, "conversations.participants", 0)
        store.insert_timeline_event.assert_called_once()

    async def test_unknown_table_does_not_call_any_method(self):
        store = make_store()
        await _write_row(store, {"table": "unknown_table"}, "topic", 0)
        store.upsert_session.assert_not_called()
        store.insert_queue_event.assert_not_called()
        store.insert_agent_event.assert_not_called()


# ── parse_participant_event ───────────────────────────────────────────────────

PARTICIPANT = "part-agent-001"
INSTANCE    = "agente_retencao_v1-001"


class TestParseParticipantEvent:
    def _joined_payload(self, conference_id: str | None = None) -> dict:
        p = {
            "type":           "participant_joined",
            "session_id":     SESSION,
            "tenant_id":      TENANT,
            "participant_id": PARTICIPANT,
            "pool_id":        POOL,
            "agent_type_id":  "agente_retencao_v1",
            "role":           "primary",
            "agent_type":     "ai",
            "joined_at":      "2026-01-01T10:00:00+00:00",
            "timestamp":      "2026-01-01T10:00:00+00:00",
        }
        if conference_id:
            p["conference_id"] = conference_id
        return p

    def _left_payload(self) -> dict:
        return {
            "type":           "participant_left",
            "session_id":     SESSION,
            "tenant_id":      TENANT,
            "participant_id": PARTICIPANT,
            "pool_id":        POOL,
            "agent_type_id":  "agente_retencao_v1",
            "role":           "primary",
            "agent_type":     "ai",
            "joined_at":      "2026-01-01T10:00:00+00:00",
            "duration_ms":    180000,
            "timestamp":      "2026-01-01T10:03:00+00:00",
        }

    # Arc 5: parse_participant_event now returns a list of 2 rows:
    #   [0] participation_intervals row  (legacy)
    #   [1] segments row                 (Arc 5 ContactSegment)

    def test_participant_joined_returns_two_rows(self):
        rows = parse_participant_event(self._joined_payload())
        assert rows is not None
        assert isinstance(rows, list)
        assert len(rows) == 2

    def test_participation_row_correct(self):
        rows = parse_participant_event(self._joined_payload())
        row = rows[0]
        assert row["table"] == "participation_intervals"
        assert row["session_id"] == SESSION
        assert row["tenant_id"] == TENANT
        assert row["participant_id"] == PARTICIPANT
        assert row["pool_id"] == POOL
        assert row["role"] == "primary"
        assert row["type"] == "participant_joined"
        assert row["duration_ms"] is None

    def test_segment_row_correct(self):
        rows = parse_participant_event(self._joined_payload())
        seg = rows[1]
        assert seg["table"] == "segments"
        assert seg["session_id"] == SESSION
        assert seg["tenant_id"] == TENANT
        assert seg["participant_id"] == PARTICIPANT
        assert seg["pool_id"] == POOL
        assert seg["role"] == "primary"
        assert isinstance(seg["segment_id"], str) and len(seg["segment_id"]) > 0
        assert seg["sequence_index"] == 0

    def test_segment_id_passed_through(self):
        payload = self._joined_payload()
        payload["segment_id"] = "fixed-seg-uuid"
        rows = parse_participant_event(payload)
        seg = rows[1]
        assert seg["segment_id"] == "fixed-seg-uuid"

    def test_sequence_index_passed_through(self):
        payload = self._joined_payload()
        payload["segment_id"] = "seg-1"
        payload["sequence_index"] = 2
        rows = parse_participant_event(payload)
        assert rows[1]["sequence_index"] == 2

    def test_parent_segment_id_passed_through(self):
        payload = self._joined_payload()
        payload["parent_segment_id"] = "parent-seg-uuid"
        rows = parse_participant_event(payload)
        assert rows[1]["parent_segment_id"] == "parent-seg-uuid"

    def test_participant_left_has_duration(self):
        rows = parse_participant_event(self._left_payload())
        assert rows is not None
        assert rows[0]["table"] == "participation_intervals"
        assert rows[0]["type"] == "participant_left"
        assert rows[0]["duration_ms"] == 180000

    def test_conference_id_propagated(self):
        rows = parse_participant_event(self._joined_payload(conference_id="conf-abc"))
        assert rows is not None
        assert rows[0]["conference_id"] == "conf-abc"
        assert rows[1]["conference_id"] == "conf-abc"

    def test_conference_id_absent_is_none(self):
        rows = parse_participant_event(self._joined_payload())
        assert rows is not None
        assert rows[0].get("conference_id") is None

    def test_unknown_type_returns_none(self):
        payload = self._joined_payload()
        payload["type"] = "participant_muted"
        assert parse_participant_event(payload) is None

    def test_missing_session_id_returns_none(self):
        payload = self._joined_payload()
        del payload["session_id"]
        assert parse_participant_event(payload) is None

    def test_missing_participant_id_returns_none(self):
        payload = self._joined_payload()
        del payload["participant_id"]
        assert parse_participant_event(payload) is None

    def test_event_id_generated_when_absent(self):
        payload = self._joined_payload()
        rows = parse_participant_event(payload)
        assert rows is not None
        # Both rows share the same event_id
        assert isinstance(rows[0]["event_id"], str)
        assert len(rows[0]["event_id"]) > 0
        assert rows[0]["event_id"] == rows[1]["event_id"]


# ── parse_evaluation_event ───────────────────────────────────────────────────

class TestParseEvaluationEvent:
    """Arc 6 — parse_evaluation_event returns [result_row, event_row]."""

    RESULT_ID  = "res-eval-001"
    INSTANCE_ID = "inst-eval-001"
    SESSION_ID  = "sess-eval-001"
    CAMPAIGN_ID = "camp-q1-2026"
    EVALUATOR   = "agente_avaliacao_v1-001"

    def _submitted_payload(self) -> dict:
        return {
            "event_type":   "evaluation.submitted",
            "tenant_id":    TENANT,
            "result_id":    self.RESULT_ID,
            "instance_id":  self.INSTANCE_ID,
            "session_id":   self.SESSION_ID,
            "campaign_id":  self.CAMPAIGN_ID,
            "evaluator_id": self.EVALUATOR,
            "form_id":      "form-sac-v1",
            "overall_score": 0.85,
            "compliance_flags": [],
            "timestamp":    "2026-04-01T10:00:00+00:00",
        }

    def test_returns_two_rows(self):
        rows = parse_evaluation_event(self._submitted_payload())
        assert rows is not None
        assert isinstance(rows, list)
        assert len(rows) == 2

    def test_result_row_table(self):
        rows = parse_evaluation_event(self._submitted_payload())
        assert rows[0]["table"] == "evaluation_results"

    def test_event_row_table(self):
        rows = parse_evaluation_event(self._submitted_payload())
        assert rows[1]["table"] == "evaluation_events"

    def test_result_row_fields(self):
        rows = parse_evaluation_event(self._submitted_payload())
        row = rows[0]
        assert row["result_id"]    == self.RESULT_ID
        assert row["tenant_id"]    == TENANT
        assert row["session_id"]   == self.SESSION_ID
        assert row["campaign_id"]  == self.CAMPAIGN_ID
        assert row["evaluator_id"] == self.EVALUATOR
        assert row["overall_score"] == pytest.approx(0.85)
        assert row["eval_status"]  == "submitted"
        assert row["locked"]       == 0

    def test_event_row_fields(self):
        rows = parse_evaluation_event(self._submitted_payload())
        row = rows[1]
        assert row["result_id"]   == self.RESULT_ID
        assert row["tenant_id"]   == TENANT
        assert row["event_type"]  == "evaluation.submitted"
        assert row["eval_status"] == "submitted"
        assert row["overall_score"] == pytest.approx(0.85)
        assert isinstance(row["event_id"], str) and len(row["event_id"]) > 0

    def test_event_row_actor_id_from_evaluator(self):
        rows = parse_evaluation_event(self._submitted_payload())
        assert rows[1]["actor_id"] == self.EVALUATOR

    def test_reviewed_event_actor_from_reviewed_by(self):
        payload = self._submitted_payload()
        payload["event_type"] = "evaluation.reviewed"
        payload["eval_status"] = "approved"
        payload["reviewed_by"] = "supervisor-001"
        rows = parse_evaluation_event(payload)
        assert rows is not None
        assert rows[1]["actor_id"] == "supervisor-001"
        assert rows[0]["eval_status"] == "approved"

    def test_contested_event_actor_from_contested_by(self):
        payload = self._submitted_payload()
        payload["event_type"] = "evaluation.contested"
        payload["contested_by"] = "operator-007"
        rows = parse_evaluation_event(payload)
        assert rows is not None
        assert rows[1]["actor_id"] == "operator-007"
        assert rows[0]["eval_status"] == "contested"

    def test_locked_event_sets_locked_flag(self):
        payload = self._submitted_payload()
        payload["event_type"] = "evaluation.locked"
        rows = parse_evaluation_event(payload)
        assert rows is not None
        assert rows[0]["locked"] == 1
        assert rows[0]["eval_status"] == "locked"

    def test_compliance_flags_propagated(self):
        payload = self._submitted_payload()
        payload["compliance_flags"] = ["gdpr_breach", "tone_violation"]
        rows = parse_evaluation_event(payload)
        assert rows is not None
        assert rows[0]["compliance_flags"] == ["gdpr_breach", "tone_violation"]

    def test_missing_result_id_returns_none(self):
        payload = self._submitted_payload()
        del payload["result_id"]
        assert parse_evaluation_event(payload) is None

    def test_missing_tenant_id_returns_none(self):
        payload = self._submitted_payload()
        del payload["tenant_id"]
        assert parse_evaluation_event(payload) is None

    def test_missing_event_type_returns_none(self):
        payload = self._submitted_payload()
        del payload["event_type"]
        assert parse_evaluation_event(payload) is None

    def test_none_overall_score_in_event_row(self):
        payload = self._submitted_payload()
        del payload["overall_score"]
        rows = parse_evaluation_event(payload)
        assert rows is not None
        # result_row defaults to 0.0; event_row stays None
        assert rows[0]["overall_score"] == pytest.approx(0.0)
        assert rows[1]["overall_score"] is None


# ── _write_row dispatch — evaluation tables ───────────────────────────────────

class TestWriteRowDispatchEvaluation:
    @pytest.mark.asyncio
    async def test_evaluation_results_dispatched(self):
        store = make_store()
        row = {"table": "evaluation_results", "result_id": "r1", "tenant_id": TENANT}
        await _write_row(store, row, "evaluation.events", 0)
        store.upsert_evaluation_result.assert_awaited_once_with(row)
        store.insert_evaluation_event.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_evaluation_events_dispatched(self):
        store = make_store()
        row = {"table": "evaluation_events", "event_id": "e1", "tenant_id": TENANT}
        await _write_row(store, row, "evaluation.events", 1)
        store.insert_evaluation_event.assert_awaited_once_with(row)
        store.upsert_evaluation_result.assert_not_awaited()


# ── parse_conversations_event — insight.* events ──────────────────────────────

class TestParseConversationsEventInsight:
    """parse_conversations_event dispatches insight.* event_types to contact_insights table."""

    def _payload(self, event_type="insight.registered", **extra):
        base = {
            "event_type": event_type,
            "session_id": SESSION,
            "tenant_id":  TENANT,
            "insight_id": "ins-001",
            "timestamp":  "2026-01-15T12:00:00+00:00",
            "category":   "cancelamento",
            "value":      "produto_x",
            "tags":       ["churn", "vip"],
            "agent_id":   "agente_sac_v1-001",
        }
        base.update(extra)
        return base

    def test_returns_contact_insights_row(self):
        rows = parse_conversations_event(self._payload())
        assert rows is not None and len(rows) == 1
        assert rows[0]["table"] == "contact_insights"

    def test_insight_id_preserved(self):
        rows = parse_conversations_event(self._payload())
        assert rows[0]["insight_id"] == "ins-001"

    def test_insight_type_equals_event_type(self):
        rows = parse_conversations_event(self._payload("insight.historico.cancelamento"))
        assert rows[0]["insight_type"] == "insight.historico.cancelamento"

    def test_category_and_value_mapped(self):
        rows = parse_conversations_event(self._payload())
        assert rows[0]["category"] == "cancelamento"
        assert rows[0]["value"] == "produto_x"

    def test_tags_propagated(self):
        rows = parse_conversations_event(self._payload())
        assert rows[0]["tags"] == ["churn", "vip"]

    def test_agent_id_from_agent_id_field(self):
        rows = parse_conversations_event(self._payload(agent_id="agente_sac_v1-001"))
        assert rows[0]["agent_id"] == "agente_sac_v1-001"

    def test_agent_id_from_instance_id_fallback(self):
        payload = self._payload()
        del payload["agent_id"]
        payload["instance_id"] = "agente_sac_v1-002"
        rows = parse_conversations_event(payload)
        assert rows[0]["agent_id"] == "agente_sac_v1-002"

    def test_category_from_nested_data(self):
        payload = {
            "event_type": "insight.registered",
            "session_id": SESSION,
            "tenant_id":  TENANT,
            "data": {"category": "retencao", "value": "oferta_aceita", "tags": ["retencao"]},
        }
        rows = parse_conversations_event(payload)
        assert rows[0]["category"] == "retencao"
        assert rows[0]["value"] == "oferta_aceita"

    def test_insight_id_generated_when_absent(self):
        payload = self._payload()
        del payload["insight_id"]
        rows = parse_conversations_event(payload)
        assert rows[0]["insight_id"]  # auto-generated UUID

    def test_unknown_event_type_returns_none(self):
        # Non-insight event types that aren't known are skipped
        rows = parse_conversations_event(self._payload("unknown.event.type"))
        assert rows is None

    def test_missing_session_id_returns_none(self):
        payload = self._payload()
        del payload["session_id"]
        assert parse_conversations_event(payload) is None

    def test_missing_tenant_id_returns_none(self):
        payload = self._payload()
        del payload["tenant_id"]
        assert parse_conversations_event(payload) is None


# ── _write_row dispatch — contact_insights ────────────────────────────────────

class TestWriteRowDispatchContactInsight:
    @pytest.mark.asyncio
    async def test_contact_insight_dispatched(self):
        store = make_store()
        store.insert_contact_insight = AsyncMock()
        row = {
            "table":        "contact_insights",
            "insight_id":   "ins-001",
            "tenant_id":    TENANT,
            "session_id":   SESSION,
            "insight_type": "insight.registered",
            "category":     "cancelamento",
            "value":        "produto_x",
            "tags":         ["churn"],
            "agent_id":     None,
            "timestamp":    "2026-01-15T12:00:00+00:00",
        }
        await _write_row(store, row, "conversations.events", 0)
        store.insert_contact_insight.assert_awaited_once_with(row)

    @pytest.mark.asyncio
    async def test_contact_insight_does_not_touch_other_stores(self):
        store = make_store()
        store.insert_contact_insight = AsyncMock()
        row = {"table": "contact_insights", "insight_id": "ins-002", "tenant_id": TENANT}
        await _write_row(store, row, "conversations.events", 0)
        store.upsert_session.assert_not_awaited()
        store.insert_agent_event.assert_not_awaited()
        store.insert_evaluation_event.assert_not_awaited()


# ── parse_agent_lifecycle — Arc 8 pause/resume ────────────────────────────────

class TestParseAgentLifecyclePause:
    """Arc 8 — agent_pause and agent_ready events map to agent_pause_intervals."""

    INSTANCE = "agente_retencao_v1-001"
    TS       = "2026-05-01T09:00:00+00:00"

    def _pause_payload(self, **extra):
        base = {
            "event":         "agent_pause",
            "tenant_id":     TENANT,
            "instance_id":   self.INSTANCE,
            "agent_type_id": "agente_retencao_v1",
            "pool_id":       POOL,
            "reason_id":     "intervalo",
            "reason_label":  "Intervalo",
            "timestamp":     self.TS,
        }
        base.update(extra)
        return base

    def _ready_payload(self, **extra):
        base = {
            "event":         "agent_ready",
            "tenant_id":     TENANT,
            "instance_id":   self.INSTANCE,
            "agent_type_id": "agente_retencao_v1",
            "pools":         [POOL],
            "status":        "ready",
            "execution_model": "stateless",
            "max_concurrent_sessions": 5,
            "current_sessions": 0,
            "timestamp":     self.TS,
        }
        base.update(extra)
        return base

    # ── agent_pause ──────────────────────────────────────────────────────────

    def test_agent_pause_returns_open_row(self):
        row = parse_agent_lifecycle(self._pause_payload())
        assert row is not None
        assert row["table"] == "agent_pause_intervals"
        assert row["action"] == "open"

    def test_agent_pause_fields_propagated(self):
        row = parse_agent_lifecycle(self._pause_payload(note="Pausa para café"))
        assert row["tenant_id"]    == TENANT
        assert row["instance_id"]  == self.INSTANCE
        assert row["agent_type_id"] == "agente_retencao_v1"
        assert row["pool_id"]      == POOL
        assert row["reason_id"]    == "intervalo"
        assert row["reason_label"] == "Intervalo"
        assert row["note"]         == "Pausa para café"
        assert row["paused_at"]    == self.TS

    def test_agent_pause_generates_interval_id(self):
        row = parse_agent_lifecycle(self._pause_payload())
        assert row is not None
        assert "interval_id" in row
        assert len(row["interval_id"]) == 36  # UUID format

    def test_agent_pause_note_none_when_absent(self):
        row = parse_agent_lifecycle(self._pause_payload())
        assert row["note"] is None

    def test_agent_pause_missing_tenant_returns_none(self):
        payload = self._pause_payload()
        del payload["tenant_id"]
        assert parse_agent_lifecycle(payload) is None

    def test_agent_pause_missing_instance_returns_none(self):
        payload = self._pause_payload()
        del payload["instance_id"]
        assert parse_agent_lifecycle(payload) is None

    # ── agent_ready (close_check) ────────────────────────────────────────────

    def test_agent_ready_returns_close_check(self):
        row = parse_agent_lifecycle(self._ready_payload())
        assert row is not None
        assert row["table"]  == "agent_pause_intervals"
        assert row["action"] == "close_check"

    def test_agent_ready_carries_tenant_instance_resumed_at(self):
        row = parse_agent_lifecycle(self._ready_payload())
        assert row["tenant_id"]   == TENANT
        assert row["instance_id"] == self.INSTANCE
        assert row["resumed_at"]  == self.TS

    def test_agent_ready_missing_tenant_returns_none(self):
        payload = self._ready_payload()
        del payload["tenant_id"]
        assert parse_agent_lifecycle(payload) is None

    def test_agent_ready_missing_instance_returns_none(self):
        payload = self._ready_payload()
        del payload["instance_id"]
        assert parse_agent_lifecycle(payload) is None

    # ── agent_done unaffected ────────────────────────────────────────────────

    def test_agent_done_still_returns_agent_events_row(self):
        row = parse_agent_lifecycle({
            "event":      "agent_done",
            "tenant_id":  TENANT,
            "instance_id": self.INSTANCE,
            "session_id": SESSION,
            "timestamp":  self.TS,
        })
        assert row is not None
        assert row["table"] == "agent_events"
        assert row["event_type"] == "agent_done"

    # ── untracked events ─────────────────────────────────────────────────────

    def test_agent_login_returns_none(self):
        assert parse_agent_lifecycle({
            "event": "agent_login", "tenant_id": TENANT, "instance_id": self.INSTANCE,
        }) is None

    def test_agent_heartbeat_returns_none(self):
        assert parse_agent_lifecycle({
            "event": "agent_heartbeat", "tenant_id": TENANT, "instance_id": self.INSTANCE,
            "status": "ready",
        }) is None

    def test_agent_busy_returns_none(self):
        assert parse_agent_lifecycle({
            "event": "agent_busy", "tenant_id": TENANT, "instance_id": self.INSTANCE,
            "session_id": SESSION,
        }) is None


# ── _write_row dispatch — agent_pause_intervals ───────────────────────────────

class TestWriteRowDispatchPauseIntervals:
    @pytest.mark.asyncio
    async def test_agent_pause_intervals_dispatched(self):
        store = make_store()
        store.upsert_agent_pause_interval = AsyncMock()
        row = {
            "table":        "agent_pause_intervals",
            "action":       "close",
            "interval_id":  "00000000-0000-0000-0000-000000000001",
            "tenant_id":    TENANT,
            "instance_id":  "agente_retencao_v1-001",
            "agent_type_id": "agente_retencao_v1",
            "pool_id":      POOL,
            "reason_id":    "intervalo",
            "reason_label": "Intervalo",
            "note":         None,
            "paused_at":    "2026-05-01T09:00:00+00:00",
            "resumed_at":   "2026-05-01T09:30:00+00:00",
            "duration_ms":  1800000,
        }
        await _write_row(store, row, "agent.lifecycle", 0)
        store.upsert_agent_pause_interval.assert_awaited_once_with(row)

    @pytest.mark.asyncio
    async def test_agent_pause_intervals_does_not_touch_other_stores(self):
        store = make_store()
        store.upsert_agent_pause_interval = AsyncMock()
        row = {"table": "agent_pause_intervals", "interval_id": "x", "tenant_id": TENANT}
        await _write_row(store, row, "agent.lifecycle", 0)
        store.upsert_session.assert_not_awaited()
        store.insert_agent_event.assert_not_awaited()
        store.insert_evaluation_event.assert_not_awaited()
