"""
test_session_trace.py — Journey T6 (rastro forense bidirecional).

Cobre query_session_trace / _fetch_session_trace:
  - short-circuit sem chamar ClickHouse quando accessible_pools=[] (sem acesso);
  - foco inexistente → focus None + nodes vazios;
  - walk bidirecional: ancestral (sobe por origin_session_id) + descendentes (BFS),
    com depth relativo ao foco e marcação de journey_boundary quando a raiz canônica
    do nó difere da do foco (o corte do `journey: new`);
  - degradação graciosa em erro do client.

Cenário base (foco = B):

    A  (raiz de topo, origin=NULL, root=A)
    └── B  (origin=A, spawn=trigger, root=A)      ← FOCO
        └── C  (origin=B, spawn=collect, root=C)  ← journey: new → boundary
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from plughub_analytics_api.reports_query import query_session_trace


class _FakeResult:
    def __init__(self, column_names, result_rows):
        self.column_names = column_names
        self.result_rows = result_rows


_TRACE_COLS = [
    "session_id", "origin_session_id", "spawn_reason", "root_session_id",
    "channel", "pool_id", "status", "outcome", "opened_at", "closed_at",
]


def _row(sid, origin, spawn, root, channel, pool, opened_min):
    return (
        sid, origin, spawn, root, channel, pool, "closed", "resolved",
        datetime(2026, 7, 14, 18, opened_min, tzinfo=timezone.utc),
        datetime(2026, 7, 14, 18, opened_min + 1, tzinfo=timezone.utc),
    )


def _no_aliases() -> "_FakeResult":
    return _FakeResult(["source_root", "canonical_root"], [])


@pytest.mark.asyncio
async def test_short_circuits_without_pool_access():
    client = MagicMock()
    out = await query_session_trace(client, "db", "t", "B", accessible_pools=[])
    assert out["focus"] is None
    assert out["nodes"] == []
    client.query.assert_not_called()


@pytest.mark.asyncio
async def test_focus_not_found_returns_empty():
    client = MagicMock()
    client.query.side_effect = [
        _no_aliases(),
        _FakeResult(_TRACE_COLS, []),  # focus fetch → nada
    ]
    out = await query_session_trace(client, "db", "t", "ghost")
    assert out["focus"] is None
    assert out["nodes"] == []
    assert out["focus_session_id"] == "ghost"


@pytest.mark.asyncio
async def test_bidirectional_walk_with_boundary():
    b = _row("B", "A", "trigger", "A", "webhook", "proc", 0)
    a = _row("A", None, None, "A", "webhook", "proc", 0)
    c = _row("C", "B", "collect", "C", "webchat", "survey", 2)  # journey: new

    client = MagicMock()
    client.query.side_effect = [
        _no_aliases(),                       # 1. journey_aliases (union-find)
        _FakeResult(_TRACE_COLS, [b]),       # 2. focus = B
        _FakeResult(_TRACE_COLS, [a]),       # 3. sobe: A (pai de B); A.origin=NULL → para
        _FakeResult(_TRACE_COLS, [c]),       # 4. desce: filhos de B → [C]
        _FakeResult(_TRACE_COLS, []),        # 5. desce: filhos de C → []
    ]

    out = await query_session_trace(client, "db", "t", "B")

    assert out["focus_session_id"] == "B"
    assert out["focus_journey_id"] == "A"        # canon(B.root=A) = A
    assert out["meta"]["node_count"] == 3

    by_id = {n["session_id"]: n for n in out["nodes"]}
    assert set(by_id) == {"A", "B", "C"}

    # depth relativo ao foco: ancestral < 0, foco = 0, descendente > 0
    assert by_id["A"]["depth"] == -1
    assert by_id["B"]["depth"] == 0
    assert by_id["C"]["depth"] == 1

    assert by_id["B"]["is_focus"] is True
    assert by_id["A"]["is_focus"] is False

    # journey_boundary: C reseta a raiz (journey: new) → cruza a fronteira do foco
    assert by_id["A"]["journey_boundary"] is False
    assert by_id["B"]["journey_boundary"] is False
    assert by_id["C"]["journey_boundary"] is True
    assert by_id["C"]["journey_id"] == "C"

    # rótulo da aresta preservado (T4)
    assert by_id["C"]["spawn_reason"] == "collect"
    assert by_id["B"]["spawn_reason"] == "trigger"


@pytest.mark.asyncio
async def test_failsoft_on_client_error():
    client = MagicMock()
    client.query.side_effect = RuntimeError("clickhouse down")
    out = await query_session_trace(client, "db", "t", "B")
    assert out["focus"] is None
    assert out["nodes"] == []
    assert out.get("error") == "data_unavailable"


@pytest.mark.asyncio
async def test_canonical_boundary_uses_alias_map():
    """Se C foi absorvido por um merge (alias C→A), deixa de cruzar a fronteira:
    a raiz canônica de C passa a ser A, igual à do foco."""
    b = _row("B", "A", "trigger", "A", "webhook", "proc", 0)
    a = _row("A", None, None, "A", "webhook", "proc", 0)
    c = _row("C", "B", "collect", "C", "webchat", "survey", 2)

    client = MagicMock()
    client.query.side_effect = [
        _FakeResult(["source_root", "canonical_root"], [("C", "A")]),  # merge C→A
        _FakeResult(_TRACE_COLS, [b]),
        _FakeResult(_TRACE_COLS, [a]),
        _FakeResult(_TRACE_COLS, [c]),
        _FakeResult(_TRACE_COLS, []),
    ]
    out = await query_session_trace(client, "db", "t", "B")
    by_id = {n["session_id"]: n for n in out["nodes"]}
    assert by_id["C"]["journey_id"] == "A"
    assert by_id["C"]["journey_boundary"] is False
