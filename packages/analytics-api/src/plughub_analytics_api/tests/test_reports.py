"""
test_reports.py
Unit tests for the Analytics API report query helpers (reports_query.py).

Strategy:
  - Each _fetch_* function is called indirectly via the async query_* wrapper.
  - ClickHouse client is mocked: .query() returns MagicMock with column_names +
    result_rows; two calls per endpoint (count + data).
  - Error paths: client raises → function returns {"data": [], "error": ...}.
  - CSV helper (_to_csv) tested separately.
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from ..reports_query import (
    _clamp_page_size,
    _to_csv,
    query_agent_performance_report,
    query_agents_report,
    query_participation_report,
    query_quality_report,
    query_segments_report,
    query_sessions_report,
    query_usage_report,
)

TENANT = "tenant_telco"
DB     = "plughub"


# ── helpers ───────────────────────────────────────────────────────────────────

def _ch_result(col_names: list[str], rows: list[list]) -> MagicMock:
    r = MagicMock()
    r.column_names = col_names
    r.result_rows  = rows
    return r


def _make_client(*query_results) -> MagicMock:
    """Returns a mock ClickHouse client with sequential query results."""
    client = MagicMock()
    client.query = MagicMock(side_effect=list(query_results))
    return client


# ── _to_csv ───────────────────────────────────────────────────────────────────

class TestToCsv:
    def test_empty_returns_empty_string(self):
        assert _to_csv([]) == ""

    def test_single_row_has_header(self):
        csv_str = _to_csv([{"a": 1, "b": "x"}])
        lines = csv_str.strip().split("\n")
        assert lines[0] == "a,b"
        assert lines[1] == "1,x"

    def test_multiple_rows(self):
        data = [{"col": "v1"}, {"col": "v2"}]
        csv_str = _to_csv(data)
        lines = csv_str.strip().split("\n")
        assert len(lines) == 3  # header + 2 rows

    def test_special_chars_quoted(self):
        csv_str = _to_csv([{"msg": "hello, world"}])
        assert '"hello, world"' in csv_str


# ── _clamp_page_size ──────────────────────────────────────────────────────────

class TestClampPageSize:
    def test_json_max_1000(self):
        assert _clamp_page_size(5000, False) == 1_000

    def test_csv_max_10000(self):
        assert _clamp_page_size(5000, True) == 5_000   # within csv limit
        assert _clamp_page_size(20000, True) == 10_000

    def test_minimum_is_1(self):
        assert _clamp_page_size(0, False) == 1


# ── query_sessions_report ────────────────────────────────────────────────────

class TestQuerySessionsReport:
    _COLS = ["session_id", "tenant_id", "channel", "pool_id",
             "opened_at", "closed_at", "close_reason", "outcome",
             "wait_time_ms", "handle_time_ms"]

    def _count_result(self, n: int) -> MagicMock:
        return _ch_result(["count()"], [[n]])

    async def test_returns_required_keys(self):
        client = _make_client(
            self._count_result(0),
            _ch_result(self._COLS, []),
        )
        result = await query_sessions_report(client, DB, TENANT)
        assert "data" in result
        assert "meta" in result
        assert result["meta"]["page"] == 1
        assert result["meta"]["total"] == 0

    async def test_data_rows_mapped_correctly(self):
        now = datetime(2026, 4, 21, 12, 0, 0)
        client = _make_client(
            self._count_result(1),
            _ch_result(self._COLS, [
                ["sess-001", TENANT, "webchat", "retencao",
                 now, None, "flow_complete", "resolved", 0, 30000],
            ]),
        )
        result = await query_sessions_report(client, DB, TENANT)
        row = result["data"][0]
        assert row["session_id"]   == "sess-001"
        assert row["channel"]      == "webchat"
        assert row["outcome"]      == "resolved"
        assert row["handle_time_ms"] == 30000
        # datetime should be ISO string
        assert isinstance(row["opened_at"], str)

    async def test_meta_total_matches_count_query(self):
        client = _make_client(
            self._count_result(42),
            _ch_result(self._COLS, []),
        )
        result = await query_sessions_report(client, DB, TENANT, page=1, page_size=10)
        assert result["meta"]["total"] == 42
        assert result["meta"]["page_size"] == 10

    async def test_optional_filters_do_not_crash(self):
        client = _make_client(
            self._count_result(3),
            _ch_result(self._COLS, []),
        )
        result = await query_sessions_report(
            client, DB, TENANT,
            channel="webchat", outcome="resolved",
            close_reason="flow_complete", pool_id="retencao",
        )
        assert result["meta"]["total"] == 3

    async def test_error_returns_empty_with_error_key(self):
        client = MagicMock()
        client.query = MagicMock(side_effect=Exception("ch down"))
        result = await query_sessions_report(client, DB, TENANT)
        assert result["data"] == []
        assert result.get("error") == "data_unavailable"

    async def test_page_size_reflected_in_meta(self):
        """page_size is passed through as-is; clamping is the router's responsibility."""
        client = _make_client(
            self._count_result(0),
            _ch_result(self._COLS, []),
        )
        result = await query_sessions_report(client, DB, TENANT, page_size=50)
        assert result["meta"]["page_size"] == 50


# ── query_agents_report ──────────────────────────────────────────────────────

class TestQueryAgentsReport:
    _COLS = ["event_id", "tenant_id", "session_id", "agent_type_id", "pool_id",
             "instance_id", "event_type", "outcome", "handoff_reason",
             "handle_time_ms", "routing_mode", "timestamp"]

    def _count_result(self, n: int) -> MagicMock:
        return _ch_result(["count()"], [[n]])

    async def test_returns_required_keys(self):
        client = _make_client(
            self._count_result(0),
            _ch_result(self._COLS, []),
        )
        result = await query_agents_report(client, DB, TENANT)
        assert "data" in result
        assert "meta" in result

    async def test_agent_done_row_mapped(self):
        ts = datetime(2026, 4, 21, 10, 0, 0)
        client = _make_client(
            self._count_result(1),
            _ch_result(self._COLS, [
                ["ev-001", TENANT, "sess-001", "agente_retencao_v1",
                 "retencao_humano", "inst-001", "agent_done",
                 "resolved", None, 45000, "balanced", ts],
            ]),
        )
        result = await query_agents_report(client, DB, TENANT)
        row = result["data"][0]
        assert row["event_type"]    == "agent_done"
        assert row["outcome"]       == "resolved"
        assert row["handle_time_ms"] == 45000
        assert isinstance(row["timestamp"], str)

    async def test_filters_accepted(self):
        client = _make_client(
            self._count_result(0),
            _ch_result(self._COLS, []),
        )
        result = await query_agents_report(
            client, DB, TENANT,
            agent_type_id="agente_retencao_v1",
            pool_id="retencao_humano",
            event_type="agent_done",
            outcome="resolved",
        )
        assert result["meta"]["total"] == 0

    async def test_error_returns_empty(self):
        client = MagicMock()
        client.query = MagicMock(side_effect=Exception("ch timeout"))
        result = await query_agents_report(client, DB, TENANT)
        assert result["data"] == []
        assert result.get("error") == "data_unavailable"


# ── query_quality_report ─────────────────────────────────────────────────────

class TestQueryQualityReport:
    _COLS = ["event_id", "tenant_id", "session_id", "pool_id",
             "score", "category", "timestamp"]

    def _count_result(self, n: int) -> MagicMock:
        return _ch_result(["count()"], [[n]])

    async def test_returns_required_keys(self):
        client = _make_client(
            self._count_result(0),
            _ch_result(self._COLS, []),
        )
        result = await query_quality_report(client, DB, TENANT)
        assert "data" in result
        assert "meta" in result

    async def test_sentiment_row_mapped(self):
        ts = datetime(2026, 4, 21, 9, 30, 0)
        client = _make_client(
            self._count_result(1),
            _ch_result(self._COLS, [
                ["ev-sent-001", TENANT, "sess-001",
                 "retencao_humano", 0.72, "satisfied", ts],
            ]),
        )
        result = await query_quality_report(client, DB, TENANT)
        row = result["data"][0]
        assert row["score"]    == 0.72
        assert row["category"] == "satisfied"
        assert row["pool_id"]  == "retencao_humano"
        assert isinstance(row["timestamp"], str)

    async def test_filters_accepted(self):
        client = _make_client(
            self._count_result(5),
            _ch_result(self._COLS, []),
        )
        result = await query_quality_report(
            client, DB, TENANT,
            pool_id="retencao_humano",
            category="frustrated",
        )
        assert result["meta"]["total"] == 5

    async def test_error_returns_empty(self):
        client = MagicMock()
        client.query = MagicMock(side_effect=Exception("ch error"))
        result = await query_quality_report(client, DB, TENANT)
        assert result["data"] == []
        assert result.get("error") == "data_unavailable"


# ── query_usage_report ───────────────────────────────────────────────────────

class TestQueryUsageReport:
    _COLS = ["event_id", "tenant_id", "session_id",
             "dimension", "quantity", "source_component", "timestamp"]

    def _count_result(self, n: int) -> MagicMock:
        return _ch_result(["count()"], [[n]])

    async def test_returns_required_keys(self):
        client = _make_client(
            self._count_result(0),
            _ch_result(self._COLS, []),
        )
        result = await query_usage_report(client, DB, TENANT)
        assert "data" in result
        assert "meta" in result

    async def test_usage_row_mapped(self):
        ts = datetime(2026, 4, 21, 8, 0, 0)
        client = _make_client(
            self._count_result(1),
            _ch_result(self._COLS, [
                ["ev-use-001", TENANT, "sess-001",
                 "llm_tokens_input", 1234, "ai-gateway", ts],
            ]),
        )
        result = await query_usage_report(client, DB, TENANT)
        row = result["data"][0]
        assert row["dimension"]        == "llm_tokens_input"
        assert row["quantity"]         == 1234
        assert row["source_component"] == "ai-gateway"
        assert isinstance(row["timestamp"], str)

    async def test_filters_accepted(self):
        client = _make_client(
            self._count_result(10),
            _ch_result(self._COLS, []),
        )
        result = await query_usage_report(
            client, DB, TENANT,
            dimension="llm_tokens_output",
            source_component="ai-gateway",
        )
        assert result["meta"]["total"] == 10

    async def test_error_returns_empty(self):
        client = MagicMock()
        client.query = MagicMock(side_effect=Exception("connection refused"))
        result = await query_usage_report(client, DB, TENANT)
        assert result["data"] == []
        assert result.get("error") == "data_unavailable"

    async def test_page_size_clamped_csv(self):
        client = _make_client(
            self._count_result(0),
            _ch_result(self._COLS, []),
        )
        from ..reports_query import _clamp_page_size
        assert _clamp_page_size(50000, True) == 10_000


# ── query_participation_report ────────────────────────────────────────────────

class TestQueryParticipationReport:
    _COLS = [
        "event_id", "session_id", "tenant_id",
        "participant_id", "pool_id", "agent_type_id",
        "role", "agent_type", "conference_id",
        "joined_at", "left_at", "duration_ms",
        "timestamp",
    ]

    def _count_result(self, n: int) -> MagicMock:
        return _ch_result(["count()"], [[n]])

    async def test_returns_required_keys(self):
        client = _make_client(
            self._count_result(0),
            _ch_result(self._COLS, []),
        )
        result = await query_participation_report(client, DB, TENANT)
        assert "data" in result
        assert "meta" in result
        assert result["meta"]["total"] == 0

    async def test_row_mapped_correctly(self):
        joined = datetime(2026, 4, 21, 10, 0, 0)
        left   = datetime(2026, 4, 21, 10, 3, 0)
        client = _make_client(
            self._count_result(1),
            _ch_result(self._COLS, [[
                "evt-part-001", "sess-001", TENANT,
                "part-agent-001", "retencao_humano", "agente_retencao_v1",
                "primary", "ai", None,
                joined, left, 180000,
                left,
            ]]),
        )
        result = await query_participation_report(client, DB, TENANT)
        row = result["data"][0]
        assert row["event_id"]       == "evt-part-001"
        assert row["participant_id"] == "part-agent-001"
        assert row["role"]           == "primary"
        assert row["duration_ms"]    == 180000
        assert isinstance(row["joined_at"], str)
        assert isinstance(row["left_at"], str)

    async def test_filters_do_not_crash(self):
        client = _make_client(
            self._count_result(5),
            _ch_result(self._COLS, []),
        )
        result = await query_participation_report(
            client, DB, TENANT,
            session_id="sess-001",
            pool_id="retencao_humano",
            agent_type_id="agente_retencao_v1",
            role="primary",
        )
        assert result["meta"]["total"] == 5

    async def test_error_returns_empty_with_error_key(self):
        client = MagicMock()
        client.query = MagicMock(side_effect=Exception("ch timeout"))
        result = await query_participation_report(client, DB, TENANT)
        assert result["data"] == []
        assert result.get("error") == "data_unavailable"


# ── query_segments_report (Arc 5 — ContactSegment) ────────────────────────────

class TestQuerySegmentsReport:
    _COLS = [
        "segment_id", "session_id", "tenant_id",
        "participant_id", "pool_id", "agent_type_id",
        "instance_id", "role", "agent_type",
        "parent_segment_id", "sequence_index",
        "started_at", "ended_at", "duration_ms",
        "outcome", "close_reason", "handoff_reason",
        "issue_status", "conference_id",
    ]

    def _count_result(self, n: int) -> MagicMock:
        r = MagicMock()
        r.result_rows = [[n]]
        return r

    async def test_returns_segment_rows(self):
        from datetime import datetime
        client = _make_client(
            self._count_result(1),
            _ch_result(self._COLS, [[
                "seg-uuid-001", "sess-001", "tenant_telco",
                "agente-001", "retencao_humano", "agente_retencao_v1",
                "agente_retencao_v1-001", "primary", "ai",
                None, 0,
                datetime(2026, 1, 1, 10, 0, 0), None, None,
                "resolved", None, None, None, None,
            ]]),
        )
        result = await query_segments_report(client, DB, TENANT)
        assert result["meta"]["total"] == 1
        row = result["data"][0]
        assert row["segment_id"] == "seg-uuid-001"
        assert row["session_id"] == "sess-001"
        assert row["role"] == "primary"
        assert row["outcome"] == "resolved"
        assert row["sequence_index"] == 0

    async def test_filters_do_not_crash(self):
        client = _make_client(
            self._count_result(3),
            _ch_result(self._COLS, []),
        )
        result = await query_segments_report(
            client, DB, TENANT,
            pool_id="retencao_humano",
            agent_type_id="agente_retencao_v1",
            role="primary",
            outcome="resolved",
        )
        assert result["meta"]["total"] == 3

    async def test_error_returns_empty_with_error_key(self):
        client = MagicMock()
        client.query = MagicMock(side_effect=Exception("ch timeout"))
        result = await query_segments_report(client, DB, TENANT)
        assert result["data"] == []
        assert result.get("error") == "data_unavailable"


# ── query_agent_performance_report (Arc 5 — aggregate) ───────────────────────

class TestQueryAgentPerformanceReport:
    # Aggregate query — one call only (no separate count query)
    _COLS = [
        "agent_type_id", "pool_id", "role",
        "total_sessions", "avg_duration_ms",
        "resolved_count", "escalated_count", "transferred_count",
        "abandoned_count", "timeout_count", "handoff_count",
        "escalation_rate", "handoff_rate",
    ]

    async def test_returns_required_keys(self):
        client = _make_client(_ch_result(self._COLS, []))
        result = await query_agent_performance_report(client, DB, TENANT)
        assert "data" in result
        assert "meta" in result
        assert result["meta"]["total"] == 0

    async def test_data_row_mapped_correctly(self):
        client = _make_client(_ch_result(self._COLS, [[
            "agente_retencao_v1", "retencao_ia", "primary",
            10,       # total_sessions
            35000.0,  # avg_duration_ms
            7,        # resolved_count
            1,        # escalated_count
            1,        # transferred_count
            1,        # abandoned_count
            0,        # timeout_count
            2,        # handoff_count
            0.1,      # escalation_rate  (1/10)
            0.2,      # handoff_rate     (2/10)
        ]]))
        result = await query_agent_performance_report(client, DB, TENANT)
        assert result["meta"]["total"] == 1
        row = result["data"][0]
        assert row["agent_type_id"]   == "agente_retencao_v1"
        assert row["pool_id"]         == "retencao_ia"
        assert row["role"]            == "primary"
        assert row["total_sessions"]  == 10
        assert row["avg_duration_ms"] == 35000.0
        assert row["resolved_count"]  == 7
        assert row["escalated_count"] == 1
        assert row["handoff_count"]   == 2
        assert abs(row["escalation_rate"] - 0.1) < 1e-6
        assert abs(row["handoff_rate"]    - 0.2) < 1e-6

    async def test_multiple_groups_returned(self):
        client = _make_client(_ch_result(self._COLS, [
            ["agente_sac_v1",      "sac_ia",       "primary",   5, None, 4, 0, 0, 1, 0, 0, 0.0, 0.0],
            ["agente_retencao_v1", "retencao_ia",  "primary",  20, 60000.0, 18, 1, 1, 0, 0, 3, 0.05, 0.15],
        ]))
        result = await query_agent_performance_report(client, DB, TENANT)
        assert result["meta"]["total"] == 2
        assert result["data"][0]["agent_type_id"] == "agente_sac_v1"
        assert result["data"][0]["avg_duration_ms"] is None   # null propagated
        assert result["data"][1]["total_sessions"]  == 20

    async def test_filters_do_not_crash(self):
        client = _make_client(_ch_result(self._COLS, []))
        result = await query_agent_performance_report(
            client, DB, TENANT,
            pool_id       = "retencao_ia",
            agent_type_id = "agente_retencao_v1",
            role          = "primary",
        )
        assert result["data"] == []
        assert result["meta"]["total"] == 0

    async def test_error_returns_empty_with_error_key(self):
        client = MagicMock()
        client.query = MagicMock(side_effect=Exception("ch timeout"))
        result = await query_agent_performance_report(client, DB, TENANT)
        assert result["data"] == []
        assert result.get("error") == "data_unavailable"
