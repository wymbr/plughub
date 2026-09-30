"""RUL-05 — `rules.turn_contexts` → `rule_turn_contexts`: o contexto que a regra VIU.

O dry-run relê estas linhas com o mesmo avaliador da execução real, então o parser não
completa nada: numérico ausente recusa a linha, sentimento ausente fica NULL (não medido)."""
from __future__ import annotations

import pytest

from ..clickhouse import _ALL_DDL, _DDL_RULE_TURN_CONTEXTS
from ..consumer import _PARSERS, _TOPICS
from ..models import parse_rule_turn_context_event

BASE = {"event_id": "e1", "tenant_id": "t", "session_id": "s1",
        "observed_at": "2026-09-30T19:00:00+00:00", "trigger": "sentiment_measured",
        "turn_count": 3, "elapsed_ms": 4200, "sentiment_score": -0.8,
        "intent_confidence": 0.7, "flags": ["human_requested"]}


def test_row_copies_what_the_rule_saw():
    row = parse_rule_turn_context_event(dict(BASE))
    assert row["table"] == "rule_turn_contexts"
    assert (row["turn_count"], row["elapsed_ms"], row["sentiment_score"]) == (3, 4200, -0.8)
    assert row["trigger"] == "sentiment_measured" and row["flags"] == ["human_requested"]


def test_unmeasured_stays_null_and_measured_zero_stays_zero():
    assert parse_rule_turn_context_event({**BASE, "sentiment_score": None})["sentiment_score"] is None
    assert parse_rule_turn_context_event({**BASE, "sentiment_score": 0.0})["sentiment_score"] == 0.0


@pytest.mark.parametrize("campo", ["turn_count", "elapsed_ms", "intent_confidence",
                                   "event_id", "tenant_id", "session_id", "observed_at"])
def test_missing_field_refuses_the_row_instead_of_inventing(campo):
    ev = dict(BASE)
    ev.pop(campo)
    assert parse_rule_turn_context_event(ev) is None


def test_only_numbers_and_flag_names_cross():
    row = parse_rule_turn_context_event({**BASE, "flags": ["ok", 7, {"x": 1}], "texto": "cpf 123",
                                         "trigger": "inventado"})
    assert row["flags"] == ["ok"] and "texto" not in row and row["trigger"] == "turn"


def test_topic_is_consumed_and_table_created_with_90_day_ttl():
    assert "rules.turn_contexts" in _TOPICS
    assert _PARSERS["rules.turn_contexts"] is parse_rule_turn_context_event
    assert _DDL_RULE_TURN_CONTEXTS in _ALL_DDL
    assert "INTERVAL 90 DAY" in _DDL_RULE_TURN_CONTEXTS
    assert "Nullable(Float64)" in _DDL_RULE_TURN_CONTEXTS
