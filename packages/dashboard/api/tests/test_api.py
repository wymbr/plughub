"""
API endpoint tests for the Dashboard API.
Verifies HTTP responses, response shapes, and correct ClickHouse query dispatch.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from .conftest import (
    AGENT_SECTION_ROW,
    AGENT_SUMMARY_ROW,
    AGENT_TREND_ROW,
    EVAL_HEADER_ROW,
    EVAL_ITEM_ROW,
    POOL_ROW,
    FakeQueryResult,
)


# ── /health ────────────────────────────────────────────────────────────────────

def test_health(api_client: TestClient) -> None:
    res = api_client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


# ── /pools ─────────────────────────────────────────────────────────────────────

class TestListPools:
    def test_returns_200(self, api_client, mock_ch):
        mock_ch.query.return_value = FakeQueryResult([POOL_ROW])
        res = api_client.get("/pools")
        assert res.status_code == 200

    def test_pool_fields_present(self, api_client, mock_ch):
        mock_ch.query.return_value = FakeQueryResult([POOL_ROW])
        data = api_client.get("/pools").json()
        assert "pools" in data
        assert "total" in data

    def test_single_pool_shape(self, api_client, mock_ch):
        mock_ch.query.return_value = FakeQueryResult([POOL_ROW])
        pool = api_client.get("/pools").json()["pools"][0]
        assert pool["pool_id"] == "retencao_humano"
        assert pool["agent_count"] == 3
        assert pool["evaluation_count"] == 25
        assert pool["avg_score"] == 7.5
        assert pool["p25_score"] == 6.8
        assert pool["p75_score"] == 8.2

    def test_total_matches_pools_length(self, api_client, mock_ch):
        mock_ch.query.return_value = FakeQueryResult([POOL_ROW, POOL_ROW])
        data = api_client.get("/pools").json()
        assert data["total"] == 2
        assert len(data["pools"]) == 2

    def test_empty_pools(self, api_client, mock_ch):
        mock_ch.query.return_value = FakeQueryResult([])
        data = api_client.get("/pools").json()
        assert data["pools"] == []
        assert data["total"] == 0

    def test_ch_error_returns_502(self, api_client, mock_ch):
        mock_ch.query.side_effect = RuntimeError("CH down")
        res = api_client.get("/pools")
        assert res.status_code == 502


# ── /pools/{pool_id}/agents ────────────────────────────────────────────────────

class TestListAgents:
    def _setup(self, mock_ch, agent_rows=None, trend_rows=None, section_rows=None, total=1):
        results = [
            FakeQueryResult(agent_rows or [AGENT_SUMMARY_ROW]),
            FakeQueryResult(trend_rows or [AGENT_TREND_ROW]),
            FakeQueryResult(section_rows or [AGENT_SECTION_ROW]),
            FakeQueryResult([(total,)]),
        ]
        mock_ch.query.side_effect = results

    def test_returns_200(self, api_client, mock_ch):
        self._setup(mock_ch)
        res = api_client.get("/pools/retencao_humano/agents")
        assert res.status_code == 200

    def test_agent_fields(self, api_client, mock_ch):
        self._setup(mock_ch)
        agent = api_client.get("/pools/retencao_humano/agents").json()["agents"][0]
        assert agent["agent_id"] == "agent-001"
        assert agent["agent_type"] == "human"
        assert agent["pool_id"] == "retencao_humano"
        assert agent["evaluation_count"] == 10
        assert agent["avg_score"] == 7.8

    def test_agent_has_trend(self, api_client, mock_ch):
        self._setup(mock_ch)
        agent = api_client.get("/pools/retencao_humano/agents").json()["agents"][0]
        assert len(agent["trend"]) == 1
        assert agent["trend"][0]["avg_score"] == 7.5

    def test_agent_has_section_scores(self, api_client, mock_ch):
        self._setup(mock_ch)
        agent = api_client.get("/pools/retencao_humano/agents").json()["agents"][0]
        assert len(agent["section_scores"]) == 1
        sec = agent["section_scores"][0]
        assert sec["section_id"] == "postura_atendimento"
        assert sec["score_type"] == "base_score"
        assert sec["avg_score"] == 8.0

    def test_empty_pool_returns_empty_agents(self, api_client, mock_ch):
        mock_ch.query.side_effect = [
            FakeQueryResult([]),  # summary — no agents
            FakeQueryResult([(0,)]),  # total
        ]
        data = api_client.get("/pools/empty_pool/agents").json()
        assert data["agents"] == []
        assert data["total"] == 0

    def test_pagination_params_forwarded(self, api_client, mock_ch):
        self._setup(mock_ch)
        res = api_client.get("/pools/retencao_humano/agents?limit=10&offset=5")
        assert res.status_code == 200
        # verify limit/offset appear in the query call params
        call_params = mock_ch.query.call_args_list[0][1].get("parameters", {})
        assert call_params.get("limit") == 10
        assert call_params.get("offset") == 5

    def test_ch_error_returns_502(self, api_client, mock_ch):
        mock_ch.query.side_effect = RuntimeError("CH down")
        res = api_client.get("/pools/retencao_humano/agents")
        assert res.status_code == 502


# ── /agents/{agent_id}/contacts ───────────────────────────────────────────────

class TestListContacts:
    def _setup(self, mock_ch, header_rows=None, item_rows=None, total=1):
        results = [
            FakeQueryResult(header_rows or [EVAL_HEADER_ROW]),
            FakeQueryResult(item_rows or [EVAL_ITEM_ROW]),
            FakeQueryResult([(total,)]),
        ]
        mock_ch.query.side_effect = results

    def test_returns_200(self, api_client, mock_ch):
        self._setup(mock_ch)
        res = api_client.get("/agents/agent-001/contacts")
        assert res.status_code == 200

    def test_contact_fields(self, api_client, mock_ch):
        self._setup(mock_ch)
        contact = api_client.get("/agents/agent-001/contacts").json()["contacts"][0]
        assert contact["evaluation_id"] == "eval-uuid-001"
        assert contact["contact_id"] == "contact-uuid-001"
        assert contact["agent_id"] == "agent-001"
        assert contact["pool_id"] == "retencao_humano"
        assert contact["skill_id"] == "eval_retencao_humano_v1"
        assert contact["overall_score"] == 7.5

    def test_contact_has_items(self, api_client, mock_ch):
        self._setup(mock_ch)
        contact = api_client.get("/agents/agent-001/contacts").json()["contacts"][0]
        assert len(contact["items"]) == 1
        item = contact["items"][0]
        assert item["section_id"] == "postura_atendimento"
        assert item["subsection_id"] == "abertura"
        assert item["item_id"] == "saudacao_adequada"
        assert item["value"] == 9
        assert item["weight"] == 3
        assert item["justification"] == "Saudação correta"

    def test_null_justification(self, api_client, mock_ch):
        item_no_just = list(EVAL_ITEM_ROW)
        item_no_just[6] = None
        self._setup(mock_ch, item_rows=[tuple(item_no_just)])
        contact = api_client.get("/agents/agent-001/contacts").json()["contacts"][0]
        assert contact["items"][0]["justification"] is None

    def test_empty_agent_returns_empty(self, api_client, mock_ch):
        mock_ch.query.side_effect = [
            FakeQueryResult([]),  # header — no evals
            FakeQueryResult([(0,)]),  # total
        ]
        data = api_client.get("/agents/nobody/contacts").json()
        assert data["contacts"] == []
        assert data["total"] == 0

    def test_total_in_response(self, api_client, mock_ch):
        self._setup(mock_ch, total=42)
        data = api_client.get("/agents/agent-001/contacts").json()
        assert data["total"] == 42

    def test_agent_id_in_response(self, api_client, mock_ch):
        self._setup(mock_ch)
        data = api_client.get("/agents/agent-001/contacts").json()
        assert data["agent_id"] == "agent-001"

    def test_ch_error_returns_502(self, api_client, mock_ch):
        mock_ch.query.side_effect = RuntimeError("CH down")
        res = api_client.get("/agents/agent-001/contacts")
        assert res.status_code == 502
