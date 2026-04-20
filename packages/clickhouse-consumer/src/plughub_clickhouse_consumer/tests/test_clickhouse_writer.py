"""
test_clickhouse_writer.py
Unit tests for ClickHouseWriter.write_evaluation.
Verifies correct row mapping from evaluation.completed to scores/items tables.
All tests use a mocked ClickHouse client — no live server needed.
"""

from __future__ import annotations
import uuid
from datetime import datetime
from unittest.mock import MagicMock, call, patch
import pytest

from plughub_clickhouse_consumer.clickhouse_writer import ClickHouseWriter, _SCORES_COLUMNS, _ITEMS_COLUMNS


@pytest.fixture
def mock_ch_client():
    client = MagicMock()
    client.command = MagicMock()
    client.insert  = MagicMock()
    return client


@pytest.fixture
def writer(mock_ch_client):
    return ClickHouseWriter(client=mock_ch_client)


# ── Test data factory ─────────────────────────────────────────────────────────

def make_evaluation_completed(
    n_sections: int = 1,
    n_subsections: int = 1,
    n_items: int = 2,
    agent_type: str = "human",
    score_type: str = "base_score",
    triggered_by: dict | None = None,
) -> dict:
    evaluation_id = str(uuid.uuid4())
    contact_id    = str(uuid.uuid4())
    agent_id      = str(uuid.uuid4())

    scores = []
    for s in range(n_sections):
        subsections = []
        for sub in range(n_subsections):
            items = []
            for i in range(n_items):
                items.append({
                    "item_id": f"item_{s}_{sub}_{i}",
                    "value": 7 + i,
                    "weight": i + 1,
                    "justification": f"Justification {i}",
                })
            subsections.append({
                "subsection_id": f"subsec_{s}_{sub}",
                "score": 7.5,
                "items": items,
            })
        scores.append({
            "section_id": f"section_{s}",
            "score_type": score_type,
            "triggered_by": triggered_by,
            "score": 7.5,
            "subsections": subsections,
        })

    return {
        "event_type": "evaluation.completed",
        "evaluation_id": evaluation_id,
        "contact_id": contact_id,
        "agent_id": agent_id,
        "agent_type": agent_type,
        "pool_id": "retencao_humano",
        "skill_id": "eval_retencao_humano_v1",
        "evaluated_at": "2024-01-01T10:30:00Z",
        "triggered_by": "contact_closed",
        "scores": scores,
        "overall_observation": "Test observation",
        "items_excluded": [],
    }


# ── Basic write tests ─────────────────────────────────────────────────────────

class TestWriteEvaluation:
    def test_inserts_score_rows(self, writer, mock_ch_client):
        event = make_evaluation_completed(n_sections=1)
        writer.write_evaluation(event)
        # Should call insert for scores
        score_calls = [c for c in mock_ch_client.insert.call_args_list
                       if c.args[0] == "evaluation_scores"]
        assert len(score_calls) == 1

    def test_inserts_item_rows(self, writer, mock_ch_client):
        event = make_evaluation_completed(n_sections=1, n_subsections=1, n_items=3)
        writer.write_evaluation(event)
        item_calls = [c for c in mock_ch_client.insert.call_args_list
                      if c.args[0] == "evaluation_items"]
        assert len(item_calls) == 1

    def test_returns_correct_counts(self, writer, mock_ch_client):
        event = make_evaluation_completed(n_sections=2, n_subsections=2, n_items=3)
        n_scores, n_items = writer.write_evaluation(event)
        assert n_scores == 2             # 2 sections → 2 score rows
        assert n_items == 2 * 2 * 3     # 2 sections × 2 subsections × 3 items

    def test_single_section_single_item(self, writer, mock_ch_client):
        event = make_evaluation_completed(n_sections=1, n_subsections=1, n_items=1)
        n_scores, n_items = writer.write_evaluation(event)
        assert n_scores == 1
        assert n_items == 1


class TestScoreRowMapping:
    def test_score_row_fields_correct(self, writer, mock_ch_client):
        event = make_evaluation_completed(n_sections=1, n_subsections=1, n_items=1)
        writer.write_evaluation(event)

        score_call = next(c for c in mock_ch_client.insert.call_args_list
                          if c.args[0] == "evaluation_scores")
        rows = score_call.args[1]
        row = rows[0]

        col = dict(zip(_SCORES_COLUMNS, row))
        assert col["evaluation_id"] == event["evaluation_id"]
        assert col["contact_id"]    == event["contact_id"]
        assert col["agent_id"]      == event["agent_id"]
        assert col["agent_type"]    == "human"
        assert col["pool_id"]       == "retencao_humano"
        assert col["skill_id"]      == "eval_retencao_humano_v1"
        assert col["section_id"]    == "section_0"
        assert col["score_type"]    == "base_score"
        assert col["score"]         == pytest.approx(7.5)
        assert col["triggered_by_src"] == "contact_closed"

    def test_triggered_by_extracted(self, writer, mock_ch_client):
        event = make_evaluation_completed(
            n_sections=1, n_subsections=1, n_items=1,
            score_type="context_score",
            triggered_by={"flags_include": "churn_signal"},
        )
        writer.write_evaluation(event)

        score_call = next(c for c in mock_ch_client.insert.call_args_list
                          if c.args[0] == "evaluation_scores")
        row = score_call.args[1][0]
        col = dict(zip(_SCORES_COLUMNS, row))
        assert col["triggered_by_key"] == "flags_include"
        assert col["triggered_by_val"] == "churn_signal"

    def test_no_triggered_by_null(self, writer, mock_ch_client):
        event = make_evaluation_completed(n_sections=1, n_subsections=1, n_items=1,
                                          triggered_by=None)
        writer.write_evaluation(event)

        score_call = next(c for c in mock_ch_client.insert.call_args_list
                          if c.args[0] == "evaluation_scores")
        row = score_call.args[1][0]
        col = dict(zip(_SCORES_COLUMNS, row))
        assert col["triggered_by_key"] is None
        assert col["triggered_by_val"] is None

    def test_evaluated_at_is_datetime(self, writer, mock_ch_client):
        event = make_evaluation_completed(n_sections=1, n_subsections=1, n_items=1)
        writer.write_evaluation(event)
        score_call = next(c for c in mock_ch_client.insert.call_args_list
                          if c.args[0] == "evaluation_scores")
        row = score_call.args[1][0]
        col = dict(zip(_SCORES_COLUMNS, row))
        assert isinstance(col["evaluated_at"], datetime)

    def test_multiple_sections_produce_multiple_score_rows(self, writer, mock_ch_client):
        event = make_evaluation_completed(n_sections=3, n_subsections=1, n_items=1)
        writer.write_evaluation(event)
        score_call = next(c for c in mock_ch_client.insert.call_args_list
                          if c.args[0] == "evaluation_scores")
        rows = score_call.args[1]
        assert len(rows) == 3


class TestItemRowMapping:
    def test_item_row_fields_correct(self, writer, mock_ch_client):
        event = make_evaluation_completed(n_sections=1, n_subsections=1, n_items=1)
        writer.write_evaluation(event)

        item_call = next(c for c in mock_ch_client.insert.call_args_list
                         if c.args[0] == "evaluation_items")
        row = item_call.args[1][0]
        col = dict(zip(_ITEMS_COLUMNS, row))

        assert col["evaluation_id"] == event["evaluation_id"]
        assert col["contact_id"]    == event["contact_id"]
        assert col["agent_id"]      == event["agent_id"]
        assert col["pool_id"]       == "retencao_humano"
        assert col["section_id"]    == "section_0"
        assert col["subsection_id"] == "subsec_0_0"
        assert col["item_id"]       == "item_0_0_0"
        assert col["value"]         == 7   # first item value
        assert col["weight"]        == 1   # first item weight
        assert "Justification" in col["justification"]

    def test_value_is_int(self, writer, mock_ch_client):
        """ClickHouse UInt8 requires integer."""
        event = make_evaluation_completed(n_sections=1, n_subsections=1, n_items=1)
        writer.write_evaluation(event)
        item_call = next(c for c in mock_ch_client.insert.call_args_list
                         if c.args[0] == "evaluation_items")
        row = item_call.args[1][0]
        col = dict(zip(_ITEMS_COLUMNS, row))
        assert isinstance(col["value"], int)
        assert isinstance(col["weight"], int)

    def test_all_items_across_subsections_inserted(self, writer, mock_ch_client):
        # 2 sections × 3 subsections × 4 items = 24 item rows
        event = make_evaluation_completed(n_sections=2, n_subsections=3, n_items=4)
        n_scores, n_items = writer.write_evaluation(event)
        assert n_items == 24


class TestEdgeCases:
    def test_empty_scores_no_insert(self, writer, mock_ch_client):
        event = make_evaluation_completed()
        event["scores"] = []
        writer.write_evaluation(event)
        mock_ch_client.insert.assert_not_called()

    def test_missing_agent_id_uses_placeholder(self, writer, mock_ch_client):
        event = make_evaluation_completed(n_sections=1, n_subsections=1, n_items=1)
        event["agent_id"] = None
        writer.write_evaluation(event)
        score_call = next(c for c in mock_ch_client.insert.call_args_list
                          if c.args[0] == "evaluation_scores")
        row = score_call.args[1][0]
        col = dict(zip(_SCORES_COLUMNS, row))
        # Should have a placeholder UUID
        assert col["agent_id"] == "00000000-0000-0000-0000-000000000000"

    def test_migrate_calls_ddl(self, writer, mock_ch_client):
        writer.migrate()
        assert mock_ch_client.command.call_count == 2  # scores DDL + items DDL
