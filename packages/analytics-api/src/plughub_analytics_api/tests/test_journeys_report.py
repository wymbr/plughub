"""
test_journeys_report.py — Journey J2a.

Cobre query_journeys_report (agrega sessions por root_session_id, proveniência-only):
  - short-circuit sem chamar ClickHouse quando accessible_pools=[] (sem acesso);
  - shape do resultado + total via _count;
  - filtro de significância vira HAVING (presente com significant_only, ausente sem);
  - SQL agrupa por root_session_id.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from plughub_analytics_api.reports_query import query_journeys_report


class _FakeResult:
    def __init__(self, column_names, result_rows):
        self.column_names = column_names
        self.result_rows = result_rows


def _executed_sql(client) -> str:
    return " ".join(str(c.args[0]) for c in client.query.call_args_list)


# Journey J3 — _fetch_journeys agora chama _journey_resolved_map primeiro (query de
# journey_aliases). Os testes de agregação prefixam um resultado de aliases-vazio.
def _no_aliases() -> "_FakeResult":
    return _FakeResult(["source_root", "canonical_root"], [])


# J4 — _fetch_journeys agrega session_signal grain=journey numa query extra
# (_attach_journey_signals). Os testes com rows prefixam um resultado de sinais vazio.
def _no_signals() -> "_FakeResult":
    return _FakeResult(["journey_id", "signal_count", "nps_avg", "csat_avg", "ces_avg"], [])


@pytest.mark.asyncio
async def test_short_circuits_without_pool_access():
    client = MagicMock()
    out = await query_journeys_report(client, "db", "t", accessible_pools=[])
    assert out["data"] == []
    assert out["meta"]["total"] == 0
    client.query.assert_not_called()


@pytest.mark.asyncio
async def test_aggregates_by_root_with_having_when_significant_only():
    count_res = _FakeResult(["count()"], [(2,)])
    rows_res = _FakeResult(
        ["journey_id", "session_count", "started_at", "last_activity_at",
         "channels", "pool_ids", "open_count", "significant"],
        [("W1", 3,
          datetime(2026, 7, 9, 18, 0, tzinfo=timezone.utc),
          datetime(2026, 7, 9, 18, 5, tzinfo=timezone.utc),
          ["webhook"], ["portabilidade_processo_ia"], 1, 1)],
    )
    client = MagicMock()
    client.query.side_effect = [_no_aliases(), count_res, rows_res, _no_signals()]

    out = await query_journeys_report(client, "db", "t", significant_only=True)

    assert out["meta"]["total"] == 2
    assert out["data"][0]["journey_id"] == "W1"
    assert out["data"][0]["session_count"] == 3
    assert out["data"][0]["channels"] == ["webhook"]
    sql = _executed_sql(client)
    # J3: agrupa pela raiz canônica. Sem aliases, o expr é identidade
    # (s.root_session_id AS journey_id) e o GROUP BY é pelo alias.
    assert "AS journey_id" in sql
    assert "GROUP BY journey_id" in sql
    assert "HAVING" in sql


@pytest.mark.asyncio
async def test_no_having_when_significant_only_false():
    client = MagicMock()
    client.query.side_effect = [
        _no_aliases(),
        _FakeResult(["count()"], [(1,)]),
        _FakeResult(["journey_id", "session_count"], [("W1", 1)]),
        _no_signals(),
    ]
    await query_journeys_report(client, "db", "t", significant_only=False)
    # A query de sinais (J4) usa HAVING journey_id IN (...) — a checagem é só sobre a
    # agregação de journeys (queries que NÃO tocam session_signal).
    non_signal = " ".join(
        str(c.args[0]) for c in client.query.call_args_list
        if "session_signal" not in str(c.args[0])
    )
    assert "HAVING" not in non_signal


@pytest.mark.asyncio
async def test_returns_error_dict_on_client_failure():
    client = MagicMock()
    client.query.side_effect = RuntimeError("clickhouse down")
    out = await query_journeys_report(client, "db", "t")
    assert out["data"] == []
    assert out.get("error") == "data_unavailable"


# ── _attach_journey_signals (J4 — sinal de qualidade N3) ──────────────────────

def test_attach_journey_signals_merges_by_journey():
    from plughub_analytics_api.reports_query import _attach_journey_signals
    client = MagicMock()
    client.query.return_value = _FakeResult(
        ["journey_id", "signal_count", "nps_avg", "csat_avg", "ces_avg"],
        [("J1", 3, 8.5, None, None)],
    )
    rows = [{"journey_id": "J1"}, {"journey_id": "J2"}]
    _attach_journey_signals(client, "db", "t", {}, rows)
    assert rows[0]["signal_count"] == 3 and rows[0]["nps_avg"] == 8.5
    # journey sem sinal → defaults
    assert rows[1]["signal_count"] == 0 and rows[1]["nps_avg"] is None


def test_attach_journey_signals_noop_without_rows():
    from plughub_analytics_api.reports_query import _attach_journey_signals
    client = MagicMock()
    _attach_journey_signals(client, "db", "t", {}, [])
    client.query.assert_not_called()


def test_attach_journey_signals_failsoft():
    from plughub_analytics_api.reports_query import _attach_journey_signals
    client = MagicMock()
    client.query.side_effect = RuntimeError("clickhouse down")
    rows = [{"journey_id": "J1"}]
    _attach_journey_signals(client, "db", "t", {}, rows)
    assert rows[0]["signal_count"] == 0  # não levanta; aplica defaults
