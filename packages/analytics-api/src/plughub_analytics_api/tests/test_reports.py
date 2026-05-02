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
    _apply_pool_scope,
    _clamp_page_size,
    _to_csv,
    query_agent_availability,
    query_agent_performance_daily,
    query_agent_performance_report,
    query_agents_report,
    query_contact_insights_report,
    query_evaluations_report,
    query_evaluations_summary,
    query_participation_report,
    query_quality_report,
    query_segments_report,
    query_session_complexity,
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


# ─── query_evaluations_report ────────────────────────────────────────────────

@pytest.mark.asyncio
class TestQueryEvaluationsReport:
    _COLS = [
        "result_id", "instance_id", "session_id", "tenant_id",
        "evaluator_id", "form_id", "campaign_id",
        "overall_score", "eval_status", "locked",
        "compliance_flags", "timestamp",
    ]

    def _count_result(self, n: int) -> MagicMock:
        return _ch_result(["count()"], [[n]])

    async def test_returns_data_and_meta(self):
        client = _make_client(
            self._count_result(0),
            _ch_result(self._COLS, []),
        )
        result = await query_evaluations_report(client, DB, TENANT)
        assert "data" in result
        assert "meta" in result
        assert result["meta"]["total"] == 0

    async def test_data_row_mapped_correctly(self):
        client = _make_client(
            self._count_result(1),
            _ch_result(self._COLS, [[
                "res-001", "inst-001", "sess-001", TENANT,
                "agente_avaliacao_v1-001", "form-sac-v1", "camp-q1-2026",
                0.87, "approved", 0,
                [], "2026-04-01T10:00:00",
            ]]),
        )
        result = await query_evaluations_report(client, DB, TENANT)
        assert result["meta"]["total"] == 1
        row = result["data"][0]
        assert row["result_id"]     == "res-001"
        assert row["tenant_id"]     == TENANT
        assert row["campaign_id"]   == "camp-q1-2026"
        assert row["eval_status"]   == "approved"
        assert row["overall_score"] == pytest.approx(0.87)
        assert row["locked"]        == 0

    async def test_filters_do_not_crash(self):
        client = _make_client(
            self._count_result(0),
            _ch_result(self._COLS, []),
        )
        result = await query_evaluations_report(
            client, DB, TENANT,
            campaign_id  = "camp-q1-2026",
            form_id      = "form-sac-v1",
            evaluator_id = "agente_avaliacao_v1-001",
            eval_status  = "approved",
        )
        assert result["data"] == []
        assert result["meta"]["total"] == 0

    async def test_error_returns_empty_with_error_key(self):
        client = MagicMock()
        client.query = MagicMock(side_effect=Exception("ch timeout"))
        result = await query_evaluations_report(client, DB, TENANT)
        assert result["data"] == []
        assert result.get("error") == "data_unavailable"


# ─── query_evaluations_summary ───────────────────────────────────────────────

@pytest.mark.asyncio
class TestQueryEvaluationsSummary:
    _COLS = [
        "group_key",
        "total_evaluated",
        "count_submitted", "count_approved", "count_rejected",
        "count_contested", "count_locked", "count_locked_flag",
        "avg_score", "min_score", "max_score",
        "score_excellent", "score_good", "score_fair", "score_poor",
        "with_compliance_flags",
    ]

    async def test_returns_data_and_meta(self):
        client = _make_client(_ch_result(self._COLS, []))
        result = await query_evaluations_summary(client, DB, TENANT)
        assert "data" in result
        assert "meta" in result
        assert "group_by" in result
        assert result["group_by"] == "campaign_id"  # default

    async def test_summary_row_mapped_correctly(self):
        client = _make_client(_ch_result(self._COLS, [[
            "camp-q1-2026",
            20,      # total_evaluated
            5,       # count_submitted
            12,      # count_approved
            2,       # count_rejected
            1,       # count_contested
            0,       # count_locked
            0,       # count_locked_flag
            0.82,    # avg_score
            0.55,    # min_score
            0.98,    # max_score
            8,       # score_excellent
            6,       # score_good
            4,       # score_fair
            2,       # score_poor
            3,       # with_compliance_flags
        ]]))
        result = await query_evaluations_summary(client, DB, TENANT)
        assert result["meta"]["total"] == 1
        row = result["data"][0]
        assert row["group_key"]             == "camp-q1-2026"
        assert row["total_evaluated"]       == 20
        assert row["count_approved"]        == 12
        assert row["avg_score"]             == pytest.approx(0.82)
        assert row["score_excellent"]       == 8
        assert row["with_compliance_flags"] == 3

    async def test_invalid_group_by_defaults_to_campaign_id(self):
        client = _make_client(_ch_result(self._COLS, []))
        result = await query_evaluations_summary(client, DB, TENANT, group_by="injection; DROP")
        assert result["group_by"] == "campaign_id"

    async def test_error_returns_empty_with_error_key(self):
        client = MagicMock()
        client.query = MagicMock(side_effect=Exception("ch timeout"))
        result = await query_evaluations_summary(client, DB, TENANT)
        assert result["data"] == []
        assert result.get("error") == "data_unavailable"


# ── Arc 7c — pool-scoped visibility ───────────────────────────────────────────

class TestApplyPoolScope:
    """Unit tests for the _apply_pool_scope helper (pure, no async)."""

    def test_none_pools_noop_returns_true(self):
        conditions: list = ["tenant_id = 'x'"]
        result = _apply_pool_scope(conditions, None)
        assert result is True
        assert len(conditions) == 1   # no extra condition added

    def test_empty_list_returns_false(self):
        conditions: list = ["tenant_id = 'x'"]
        result = _apply_pool_scope(conditions, [])
        assert result is False
        assert len(conditions) == 1   # no condition added (caller short-circuits)

    def test_single_pool_appends_in_clause(self):
        conditions: list = []
        _apply_pool_scope(conditions, ["pool_sac"])
        assert len(conditions) == 1
        assert "pool_id IN ('pool_sac')" in conditions[0]

    def test_multiple_pools_joined_correctly(self):
        conditions: list = []
        _apply_pool_scope(conditions, ["sac", "retencao", "billing"])
        clause = conditions[0]
        assert "pool_id IN" in clause
        assert "'sac'" in clause
        assert "'retencao'" in clause
        assert "'billing'" in clause


class TestPoolScopedSessionsReport:
    """Arc 7c — accessible_pools filtering in query_sessions_report."""

    _COLS = ["session_id", "tenant_id", "channel", "pool_id",
             "opened_at", "closed_at", "close_reason", "outcome",
             "wait_time_ms", "handle_time_ms"]

    def _count_result(self, n: int) -> MagicMock:
        return _ch_result(["count()"], [[n]])

    async def test_none_accessible_pools_passes_through(self):
        """accessible_pools=None (unrestricted) — ClickHouse is still called."""
        client = _make_client(
            self._count_result(5),
            _ch_result(self._COLS, []),
        )
        result = await query_sessions_report(
            client, DB, TENANT, accessible_pools=None
        )
        assert result["meta"]["total"] == 5
        assert client.query.call_count == 2   # count + data

    async def test_empty_accessible_pools_short_circuits(self):
        """accessible_pools=[] (no access) — ClickHouse never called."""
        client = MagicMock()
        result = await query_sessions_report(
            client, DB, TENANT, accessible_pools=[]
        )
        assert result["data"] == []
        assert result["meta"]["total"] == 0
        client.query.assert_not_called()

    async def test_pool_list_injects_in_clause(self):
        """accessible_pools=['sac'] — WHERE clause contains pool_id IN (...)."""
        client = _make_client(
            self._count_result(2),
            _ch_result(self._COLS, []),
        )
        await query_sessions_report(
            client, DB, TENANT, accessible_pools=["sac"]
        )
        # Both calls (count + data) should contain the IN clause
        for call in client.query.call_args_list:
            sql = call[0][0]
            assert "pool_id IN ('sac')" in sql


class TestPoolScopedAgentsReport:
    """Arc 7c — accessible_pools short-circuit in query_agents_report."""

    async def test_empty_pools_returns_empty_without_ch_call(self):
        client = MagicMock()
        result = await query_agents_report(client, DB, TENANT, accessible_pools=[])
        assert result["data"] == []
        client.query.assert_not_called()

    async def test_pools_list_filters_agent_events(self):
        cols = ["event_id", "tenant_id", "session_id", "agent_type_id", "pool_id",
                "instance_id", "event_type", "outcome", "handoff_reason",
                "handle_time_ms", "routing_mode", "timestamp"]
        client = _make_client(
            _ch_result(["count()"], [[0]]),
            _ch_result(cols, []),
        )
        await query_agents_report(client, DB, TENANT, accessible_pools=["retencao"])
        for call in client.query.call_args_list:
            assert "pool_id IN ('retencao')" in call[0][0]


class TestPoolPrincipalAuth:
    """Unit tests for pool_auth.PoolPrincipal and optional_pool_principal."""

    def test_is_unrestricted_when_none(self):
        from ..pool_auth import PoolPrincipal
        p = PoolPrincipal(accessible_pools=None, tenant_id="t", sub="u")
        assert p.is_unrestricted is True

    def test_is_not_unrestricted_when_list(self):
        from ..pool_auth import PoolPrincipal
        p = PoolPrincipal(accessible_pools=["pool_a"], tenant_id="t", sub="u")
        assert p.is_unrestricted is False

    async def test_open_access_returns_unrestricted(self):
        from unittest.mock import patch
        from ..pool_auth import optional_pool_principal
        with patch("plughub_analytics_api.pool_auth.get_settings") as m:
            m.return_value.analytics_open_access = True
            m.return_value.auth_jwt_secret = "secret"
            principal = await optional_pool_principal(credentials=None)
        assert principal.accessible_pools is None

    async def test_no_secret_returns_unrestricted(self):
        from unittest.mock import patch
        from ..pool_auth import optional_pool_principal
        with patch("plughub_analytics_api.pool_auth.get_settings") as m:
            m.return_value.analytics_open_access = False
            m.return_value.auth_jwt_secret = ""
            principal = await optional_pool_principal(credentials=None)
        assert principal.accessible_pools is None

    async def test_no_token_returns_unrestricted(self):
        from unittest.mock import patch
        from ..pool_auth import optional_pool_principal
        with patch("plughub_analytics_api.pool_auth.get_settings") as m:
            m.return_value.analytics_open_access = False
            m.return_value.auth_jwt_secret = "mysecret"
            principal = await optional_pool_principal(credentials=None)
        assert principal.accessible_pools is None

    async def test_valid_jwt_empty_pools_returns_unrestricted(self):
        """JWT with accessible_pools=[] means all pools (admin convention)."""
        import jwt as pyjwt
        from unittest.mock import MagicMock, patch
        from ..pool_auth import optional_pool_principal
        secret = "testsecret"
        token = pyjwt.encode(
            {"sub": "u1", "tenant_id": "t1", "accessible_pools": []},
            secret, algorithm="HS256",
        )
        creds = MagicMock()
        creds.credentials = token
        with patch("plughub_analytics_api.pool_auth.get_settings") as m:
            m.return_value.analytics_open_access = False
            m.return_value.auth_jwt_secret = secret
            principal = await optional_pool_principal(credentials=creds)
        assert principal.accessible_pools is None   # [] → all pools

    async def test_valid_jwt_with_pools_restricts(self):
        import jwt as pyjwt
        from unittest.mock import MagicMock, patch
        from ..pool_auth import optional_pool_principal
        secret = "testsecret"
        token = pyjwt.encode(
            {"sub": "u2", "tenant_id": "t1", "accessible_pools": ["sac", "retencao"]},
            secret, algorithm="HS256",
        )
        creds = MagicMock()
        creds.credentials = token
        with patch("plughub_analytics_api.pool_auth.get_settings") as m:
            m.return_value.analytics_open_access = False
            m.return_value.auth_jwt_secret = secret
            principal = await optional_pool_principal(credentials=creds)
        assert principal.accessible_pools == ["sac", "retencao"]

    async def test_invalid_jwt_raises_401(self):
        from unittest.mock import MagicMock, patch
        from fastapi import HTTPException
        from ..pool_auth import optional_pool_principal
        creds = MagicMock()
        creds.credentials = "not.a.valid.jwt"
        with patch("plughub_analytics_api.pool_auth.get_settings") as m:
            m.return_value.analytics_open_access = False
            m.return_value.auth_jwt_secret = "secret"
            with pytest.raises(HTTPException) as exc_info:
                await optional_pool_principal(credentials=creds)
        assert exc_info.value.status_code == 401


# ── query_contact_insights_report ─────────────────────────────────────────────

class TestQueryContactInsightsReport:
    """Tests for the _fetch_contact_insights path via query_contact_insights_report."""

    COLS = ["insight_id", "tenant_id", "session_id",
            "insight_type", "category", "value", "tags", "agent_id", "timestamp"]

    def _insight_row(self, **overrides):
        base = [
            "ins-001", TENANT, "sess-001",
            "insight.registered", "cancelamento", "produto_x",
            ["churn", "vip"], "agente_sac_v1-001", "2026-01-15T12:00:00",
        ]
        return base

    @pytest.mark.asyncio
    async def test_returns_data_rows(self):
        count_r = _ch_result(["count()"], [[3]])
        data_r  = _ch_result(self.COLS, [self._insight_row()])
        client  = _make_client(count_r, data_r)
        result  = await query_contact_insights_report(client, DB, TENANT)
        assert result["meta"]["total"] == 3
        assert len(result["data"]) == 1
        assert result["data"][0]["insight_id"] == "ins-001"

    @pytest.mark.asyncio
    async def test_category_filter_appends_condition(self):
        count_r = _ch_result(["count()"], [[1]])
        data_r  = _ch_result(self.COLS, [self._insight_row()])
        client  = _make_client(count_r, data_r)
        await query_contact_insights_report(client, DB, TENANT, category="cancelamento")
        # Verify both queries (count + data) were called
        assert client.query.call_count == 2
        # The count query SQL should contain the category parameter
        count_sql = client.query.call_args_list[0][0][0]
        assert "category" in count_sql

    @pytest.mark.asyncio
    async def test_tags_filter_appends_has_condition(self):
        count_r = _ch_result(["count()"], [[0]])
        data_r  = _ch_result(self.COLS, [])
        client  = _make_client(count_r, data_r)
        await query_contact_insights_report(client, DB, TENANT, tags=["churn", "vip"])
        count_sql = client.query.call_args_list[0][0][0]
        assert "has" in count_sql

    @pytest.mark.asyncio
    async def test_error_returns_empty_with_error_key(self):
        client = MagicMock()
        client.query = MagicMock(side_effect=RuntimeError("CH timeout"))
        result = await query_contact_insights_report(client, DB, TENANT)
        assert result["data"] == []
        assert "error" in result


# ─── query_agent_performance_daily (Arc 5 MV — v_agent_performance) ──────────

@pytest.mark.asyncio
class TestQueryAgentPerformanceDaily:
    """Tests for the daily MV-backed performance endpoint (v_agent_performance view)."""

    _COLS = [
        "agent_type_id", "pool_id", "period_date",
        "total_sessions", "avg_duration_ms",
        "resolution_rate", "escalation_rate", "transfer_rate", "human_rate",
    ]

    async def test_returns_data_and_meta(self):
        """Empty result set still returns data + meta with date keys."""
        client = _make_client(_ch_result(self._COLS, []))
        result = await query_agent_performance_daily(client, DB, TENANT)
        assert "data" in result
        assert "meta" in result
        assert "from_date" in result["meta"]
        assert "to_date" in result["meta"]
        assert result["meta"]["total"] == 0

    async def test_data_row_mapped_correctly(self):
        """Each row contains all expected columns with correct values."""
        from datetime import date
        client = _make_client(_ch_result(self._COLS, [[
            "agente_sac_v1", "sac_ia", date(2026, 4, 28),
            42,         # total_sessions
            28500.0,    # avg_duration_ms
            0.857143,   # resolution_rate
            0.095238,   # escalation_rate
            0.047619,   # transfer_rate
            0.0,        # human_rate
        ]]))
        result = await query_agent_performance_daily(client, DB, TENANT)
        assert result["meta"]["total"] == 1
        row = result["data"][0]
        assert row["agent_type_id"]   == "agente_sac_v1"
        assert row["pool_id"]         == "sac_ia"
        assert row["total_sessions"]  == 42
        assert row["avg_duration_ms"] == pytest.approx(28500.0)
        assert row["resolution_rate"] == pytest.approx(0.857143)
        assert row["escalation_rate"] == pytest.approx(0.095238)

    async def test_filters_do_not_crash(self):
        """Passing pool_id and agent_type_id filters runs without error."""
        client = _make_client(_ch_result(self._COLS, []))
        result = await query_agent_performance_daily(
            client, DB, TENANT,
            pool_id       = "sac_ia",
            agent_type_id = "agente_sac_v1",
        )
        assert result["data"] == []
        assert result["meta"]["total"] == 0

    async def test_empty_accessible_pools_short_circuits(self):
        """accessible_pools=[] returns empty immediately without hitting ClickHouse."""
        client = MagicMock()
        result = await query_agent_performance_daily(
            client, DB, TENANT, accessible_pools=[]
        )
        assert result["data"] == []
        assert result["meta"]["total"] == 0
        client.query.assert_not_called()

    async def test_error_returns_empty_with_error_key(self):
        """ClickHouse error returns graceful fallback with error key."""
        client = MagicMock()
        client.query = MagicMock(side_effect=Exception("CH timeout"))
        result = await query_agent_performance_daily(client, DB, TENANT)
        assert result["data"] == []
        assert result.get("error") == "data_unavailable"


# ─── query_session_complexity (Arc 5 MV — v_segment_summary) ─────────────────

@pytest.mark.asyncio
class TestQuerySessionComplexity:
    """Tests for the session-complexity MV-backed endpoint (v_segment_summary view)."""

    _COLS = [
        "session_id", "pool_id",
        "segment_count", "primary_segments", "specialist_segments", "human_segments",
        "total_duration_ms", "handoff_count", "escalation_count", "resolved_count",
    ]

    def _count_result(self, n: int) -> MagicMock:
        r = MagicMock()
        r.result_rows = [[n]]
        return r

    async def test_returns_data_and_meta(self):
        """Empty result set still returns data + meta."""
        client = _make_client(
            self._count_result(0),
            _ch_result(self._COLS, []),
        )
        result = await query_session_complexity(client, DB, TENANT)
        assert "data" in result
        assert "meta" in result
        assert result["meta"]["total"] == 0

    async def test_data_row_mapped_correctly(self):
        """Each row maps all expected columns correctly."""
        client = _make_client(
            self._count_result(1),
            _ch_result(self._COLS, [[
                "sess-complex-001", "retencao_humano",
                3,       # segment_count
                2,       # primary_segments
                1,       # specialist_segments
                1,       # human_segments
                125000,  # total_duration_ms
                2,       # handoff_count
                1,       # escalation_count
                0,       # resolved_count
            ]]),
        )
        result = await query_session_complexity(client, DB, TENANT)
        assert result["meta"]["total"] == 1
        row = result["data"][0]
        assert row["session_id"]         == "sess-complex-001"
        assert row["pool_id"]            == "retencao_humano"
        assert row["segment_count"]      == 3
        assert row["handoff_count"]      == 2
        assert row["escalation_count"]   == 1
        assert row["total_duration_ms"]  == 125000

    async def test_min_handoffs_filter(self):
        """min_handoffs parameter is accepted without crashing."""
        client = _make_client(
            self._count_result(0),
            _ch_result(self._COLS, []),
        )
        result = await query_session_complexity(
            client, DB, TENANT, min_handoffs=2
        )
        assert result["data"] == []
        # SQL sent to ClickHouse should reference min_handoffs
        for call in client.query.call_args_list:
            sql = call[0][0]
            assert "handoff_count" in sql

    async def test_empty_accessible_pools_short_circuits(self):
        """accessible_pools=[] returns empty immediately without hitting ClickHouse."""
        client = MagicMock()
        result = await query_session_complexity(
            client, DB, TENANT, accessible_pools=[]
        )
        assert result["data"] == []
        assert result["meta"]["total"] == 0
        client.query.assert_not_called()

    async def test_error_returns_empty_with_error_key(self):
        """ClickHouse error returns graceful fallback with error key."""
        client = MagicMock()
        client.query = MagicMock(side_effect=Exception("CH timeout"))
        result = await query_session_complexity(client, DB, TENANT)
        assert result["data"] == []
        assert result.get("error") == "data_unavailable"


# ── query_agent_availability (Arc 8) ─────────────────────────────────────────

@pytest.mark.asyncio
class TestQueryAgentAvailabilityReport:
    """Unit tests for Arc 8 agent availability / pause interval report."""

    # Column names returned by the aggregate query
    _AGG_COLS    = ["agent_type_id", "pool_id", "period_date", "total_pauses", "total_pause_ms"]
    _REASON_COLS = ["agent_type_id", "pool_id", "period_date", "reason_id", "reason_label", "cnt", "total_ms"]

    def _make_client_availability(self, agg_rows=None, reason_rows=None, total=0):
        """
        _fetch_agent_availability makes 3 ClickHouse queries:
          1. countDistinct  → total
          2. aggregate      → agg_rows
          3. reason breakdown → reason_rows

        query_agent_availability receives a *store* object (store.client + store.database),
        not a raw client — so we wrap the prepared client in a MagicMock store.
        """
        count_result  = _ch_result(["countDistinct((agent_type_id, pool_id, toDate(paused_at)))"], [[total]])
        agg_result    = _ch_result(self._AGG_COLS,    agg_rows    or [])
        reason_result = _ch_result(self._REASON_COLS, reason_rows or [])
        client = _make_client(count_result, agg_result, reason_result)
        store = MagicMock()
        store.client   = client
        store.database = DB
        return store

    async def test_returns_data_and_meta(self):
        """Successful call returns data list and meta dict."""
        store  = self._make_client_availability()
        result = await query_agent_availability(store, TENANT)
        assert "data" in result
        assert "meta" in result
        assert result["data"] == []
        assert result["meta"]["total"] == 0

    async def test_agg_row_mapped_with_reason_breakdown(self):
        """An aggregated row is returned with reason_breakdown attached."""
        from datetime import date
        period = date(2026, 5, 1)
        store  = self._make_client_availability(
            agg_rows=[["agente_retencao_v1", "retencao_humano", period, 3, 5400000]],
            reason_rows=[
                ["agente_retencao_v1", "retencao_humano", period, "intervalo", "Intervalo", 2, 3600000],
                ["agente_retencao_v1", "retencao_humano", period, "almoco",    "Almoço",    1, 1800000],
            ],
            total=1,
        )
        result = await query_agent_availability(store, TENANT)
        assert result["meta"]["total"] == 1
        row = result["data"][0]
        assert row["agent_type_id"]  == "agente_retencao_v1"
        assert row["pool_id"]        == "retencao_humano"
        assert row["total_pauses"]   == 3
        assert row["total_pause_ms"] == 5400000
        breakdown = row["reason_breakdown"]
        assert len(breakdown) == 2
        reasons = {r["reason_id"] for r in breakdown}
        assert "intervalo" in reasons
        assert "almoco"    in reasons

    async def test_empty_accessible_pools_short_circuits(self):
        """accessible_pools=[] returns empty immediately without hitting ClickHouse."""
        store = MagicMock()
        result = await query_agent_availability(
            store, TENANT, accessible_pools=[]
        )
        assert result["data"] == []
        assert result["meta"]["total"] == 0
        # short-circuit: store.client.query must never be called
        store.client.query.assert_not_called()

    async def test_none_accessible_pools_calls_ch(self):
        """accessible_pools=None (unrestricted) — ClickHouse is queried normally."""
        store  = self._make_client_availability(total=0)
        result = await query_agent_availability(
            store, TENANT, accessible_pools=None
        )
        assert store.client.query.call_count == 3  # count + agg + reason breakdown
        assert result["data"] == []

    async def test_pool_filter_injects_in_clause(self):
        """accessible_pools=['retencao_humano'] → IN clause in all queries."""
        store = self._make_client_availability(total=0)
        await query_agent_availability(
            store, TENANT, accessible_pools=["retencao_humano"]
        )
        for call in store.client.query.call_args_list:
            sql = call[0][0]
            assert "pool_id IN ('retencao_humano')" in sql

    async def test_error_returns_empty_with_error_key(self):
        """ClickHouse error returns graceful fallback with error key."""
        store = MagicMock()
        store.client.query = MagicMock(side_effect=Exception("CH timeout"))
        result = await query_agent_availability(store, TENANT)
        assert result["data"] == []
        assert result.get("error") == "data_unavailable"
