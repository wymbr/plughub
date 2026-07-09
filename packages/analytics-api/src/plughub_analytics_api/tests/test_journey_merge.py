"""
test_journey_merge.py — Journey J3.

Cobre a resolução canônica (union-find sobre journey_aliases) e o parser do topic
journey.merges:
  - _journey_resolved_map: cadeia (A→B→C), aresta única, cycle guard, tenant param;
  - _journey_group_expr: identidade sem aliases, transform com aliases;
  - _journey_member_roots: expansão canônico→membros (para o drill);
  - parse_journey_merged: aresta válida, self-merge no-op, campos faltando.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from plughub_analytics_api.reports_query import (
    _journey_resolved_map,
    _journey_group_expr,
    _journey_member_roots,
)
from plughub_analytics_api.models import parse_journey_merged


class _FakeResult:
    def __init__(self, rows):
        self.column_names = ["source_root", "canonical_root"]
        self.result_rows = rows


def _client(edges):
    c = MagicMock()
    c.query.return_value = _FakeResult(edges)
    return c


# ── _journey_resolved_map (union-find) ────────────────────────────────────────

def test_resolves_transitive_chain():
    # A→B, B→C  ⇒  A e B resolvem para C
    m = _journey_resolved_map(_client([("A", "B"), ("B", "C")]), "db", "t")
    assert m["A"] == "C"
    assert m["B"] == "C"


def test_single_edge():
    m = _journey_resolved_map(_client([("A", "B")]), "db", "t")
    assert m == {"A": "B"}


def test_empty_no_aliases():
    assert _journey_resolved_map(_client([]), "db", "t") == {}


def test_cycle_guard_terminates():
    # A→B, B→A — não deve travar; ambos presentes no mapa.
    m = _journey_resolved_map(_client([("A", "B"), ("B", "A")]), "db", "t")
    assert set(m.keys()) == {"A", "B"}


def test_ignores_self_edge():
    m = _journey_resolved_map(_client([("A", "A"), ("B", "C")]), "db", "t")
    assert "A" not in m
    assert m["B"] == "C"


def test_passes_tenant_param():
    c = _client([("A", "B")])
    _journey_resolved_map(c, "db", "tenant_x")
    _, kwargs = c.query.call_args
    assert kwargs["parameters"] == {"tenant_id": "tenant_x"}


def test_degrades_to_empty_on_error():
    c = MagicMock()
    c.query.side_effect = RuntimeError("clickhouse down")
    assert _journey_resolved_map(c, "db", "t") == {}


# ── _journey_group_expr ───────────────────────────────────────────────────────

def test_group_expr_identity_without_aliases():
    assert _journey_group_expr({}) == "s.root_session_id"


def test_group_expr_transform_with_aliases():
    expr = _journey_group_expr({"A": "C", "B": "C"})
    assert expr.startswith("transform(s.root_session_id")
    assert "'A'" in expr and "'B'" in expr and "'C'" in expr


# ── _journey_member_roots (drill expansion) ───────────────────────────────────

def test_member_roots_expands_canonical():
    resolved = {"A": "C", "B": "C"}
    assert _journey_member_roots(resolved, "C") == {"A", "B", "C"}


def test_member_roots_from_a_member_gives_same_set():
    resolved = {"A": "C", "B": "C"}
    assert _journey_member_roots(resolved, "A") == {"A", "B", "C"}


def test_member_roots_unknown_root_is_singleton():
    assert _journey_member_roots({"A": "C"}, "Z") == {"Z"}


# ── parse_journey_merged ──────────────────────────────────────────────────────

def test_parse_valid_edge():
    row = parse_journey_merged({
        "tenant_id": "t", "source_root": "NEW", "canonical_root": "OLD",
        "merged_at": "2026-07-09T00:00:00Z", "actor": "skill_x",
    })
    assert row["table"] == "journey_aliases"
    assert row["source_root"] == "NEW"
    assert row["canonical_root"] == "OLD"
    assert row["active"] == 1


def test_parse_self_merge_is_noop():
    assert parse_journey_merged({
        "tenant_id": "t", "source_root": "X", "canonical_root": "X",
    }) is None


def test_parse_missing_fields():
    assert parse_journey_merged({"tenant_id": "t", "source_root": "A"}) is None
    assert parse_journey_merged({"source_root": "A", "canonical_root": "B"}) is None
