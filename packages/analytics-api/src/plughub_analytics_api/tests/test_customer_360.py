"""
test_customer_360.py — Cliente 360 (C1b, ADR §D4).

Cobre query_customer_360 (agrega contacts + quality[evaluation_finalized] +
surveys[session_signal] por customer_id):
  - shape do resultado no caminho feliz (3 queries em ordem);
  - guarda do "zero plausível": quality com count=0 vira None (não bloco vazio);
  - fail-soft: se o ClickHouse quebra, cada bloco degrada (None/[]), sem lançar;
  - a subquery de sessões do cliente é origin-scoped (default 'live').
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from plughub_analytics_api.reports_query import query_customer_360


class _FakeResult:
    def __init__(self, column_names, result_rows):
        self.column_names = column_names
        self.result_rows = result_rows


def _executed_sql(client) -> str:
    return " ".join(str(c.args[0]) for c in client.query.call_args_list)


def _contacts(total=3, resolved=2, open_count=1):
    return _FakeResult(
        ["total", "resolved", "open_count", "channels", "last_contact_at"],
        [(total, resolved, open_count, ["webchat", "whatsapp"],
          datetime(2026, 7, 15, 12, 0, tzinfo=timezone.utc))],
    )


def _quality(count=4, avg=0.82):
    return _FakeResult(
        ["count", "avg_score", "min_score", "max_score", "latest_score", "latest_at"],
        [(count, avg, 0.6, 0.95, 0.9,
          datetime(2026, 7, 14, 9, 0, tzinfo=timezone.utc))],
    )


def _surveys():
    return _FakeResult(
        ["metric", "count", "avg_value", "latest_value", "latest_label", "latest_at"],
        [("nps", 2, 9.0, 10.0, "10",
          datetime(2026, 7, 13, 8, 0, tzinfo=timezone.utc))],
    )


@pytest.mark.asyncio
async def test_happy_path_shape():
    client = MagicMock()
    client.query.side_effect = [_contacts(), _quality(), _surveys()]

    out = await query_customer_360(client, "db", "t", "cus_x")

    assert out["customer_id"] == "cus_x"
    assert out["contacts"]["total"] == 3
    assert out["contacts"]["resolved"] == 2
    assert out["quality"]["count"] == 4
    assert out["quality"]["avg_score"] == 0.82
    assert len(out["surveys"]) == 1
    assert out["surveys"][0]["metric"] == "nps"


@pytest.mark.asyncio
async def test_quality_zero_count_is_none():
    """count=0 ⇒ sem avaliações finalizadas: quality None (não expõe zero plausível)."""
    client = MagicMock()
    client.query.side_effect = [_contacts(), _quality(count=0), _surveys()]

    out = await query_customer_360(client, "db", "t", "cus_x")
    assert out["quality"] is None


@pytest.mark.asyncio
async def test_failsoft_each_block():
    client = MagicMock()
    client.query.side_effect = RuntimeError("clickhouse down")

    out = await query_customer_360(client, "db", "t", "cus_x")
    # Nunca lança; cada bloco degrada individualmente.
    assert out["contacts"] is None
    assert out["quality"] is None
    assert out["surveys"] == []


@pytest.mark.asyncio
async def test_sessions_subquery_is_origin_scoped():
    client = MagicMock()
    client.query.side_effect = [_contacts(), _quality(), _surveys()]

    await query_customer_360(client, "db", "t", "cus_x")
    sql = _executed_sql(client)
    # Default origin='live' entra na subquery de sessões (isolamento de substrato).
    assert "origin IN ('live')" in sql
    # Os joins de quality/survey filtram pelo conjunto de sessões do cliente.
    assert "session_id IN (SELECT session_id FROM db.sessions" in sql
