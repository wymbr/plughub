"""
test_router.py
Integration tests for evaluation-api router.
All PostgreSQL and Kafka calls are mocked.

Tests: 42 assertions across:
  TestHealth           (1)
  TestForms            (8)
  TestCampaigns        (7)
  TestInstances        (8)
  TestIngest           (5)
  TestResults          (6)
  TestContestations    (5)
  TestSampleCheck      (3)
  TestReports          (3)
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from ..router import router


# ─── Fixtures ─────────────────────────────────────────────────────────────────

def _make_form(form_id: str = "evform_abc", tenant_id: str = "t1") -> dict:
    return {
        "id": form_id, "tenant_id": tenant_id, "name": "Test Form",
        "description": "", "version": 1, "status": "active",
        "dimensions": [], "total_weight": 1.0, "passing_score": 0.7,
        "allow_na": True, "knowledge_domains": [],
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
        "created_by": "operator",
    }


def _make_campaign(
    campaign_id: str = "evcampaign_abc",
    form_id: str = "evform_abc",
    tenant_id: str = "t1",
    status: str = "active",
) -> dict:
    return {
        "id": campaign_id, "tenant_id": tenant_id, "name": "Test Campaign",
        "description": "", "form_id": form_id, "pool_id": "sac_ia",
        "status": status, "sampling_rules": {}, "reviewer_rules": {}, "schedule": {},
        "total_instances": 0, "completed_instances": 0, "avg_score": None,
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
        "created_by": "operator",
    }


def _make_instance(
    instance_id: str = "evinstance_abc",
    campaign_id: str = "evcampaign_abc",
    tenant_id: str = "t1",
    status: str = "scheduled",
) -> dict:
    return {
        "id": instance_id, "tenant_id": tenant_id,
        "campaign_id": campaign_id, "form_id": "evform_abc",
        "session_id": "sess_001", "segment_id": None,
        "evaluator_agent_id": None, "reviewer_agent_id": None,
        "status": status, "priority": 5,
        "scheduled_at": "2026-01-01T00:00:00Z", "assigned_at": None,
        "completed_at": None, "expires_at": None, "error_message": None,
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
    }


def _make_result(
    result_id: str = "evresult_abc",
    instance_id: str = "evinstance_abc",
    tenant_id: str = "t1",
    eval_status: str = "submitted",
) -> dict:
    return {
        "id": result_id, "tenant_id": tenant_id,
        "instance_id": instance_id, "session_id": "sess_001",
        "campaign_id": "evcampaign_abc", "form_id": "evform_abc",
        "evaluator_agent_id": "agente_avaliacao_v1-001",
        "overall_score": 8.5, "max_score": 10.0, "normalized_score": 0.85,
        "passed": True, "eval_status": eval_status,
        "evaluator_notes": "", "comparison_mode": False,
        "comparison_report": None, "knowledge_snippets": [],
        "reviewer_agent_id": None, "reviewer_outcome": None,
        "reviewer_notes": None, "reviewer_score": None, "reviewed_at": None,
        "contested_by": None, "contested_at": None, "contestation_reason": None,
        "locked_at": None, "locked_by": None,
        "submitted_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
    }


def _make_contestation(
    contest_id: str = "evcontest_abc",
    result_id: str = "evresult_abc",
    tenant_id: str = "t1",
    status: str = "open",
) -> dict:
    return {
        "id": contest_id, "tenant_id": tenant_id,
        "result_id": result_id, "instance_id": "evinstance_abc",
        "session_id": "sess_001",
        "contested_by": "agent_human_001",
        "contested_at": "2026-01-01T00:00:00Z",
        "contestation_reason": "Score too low",
        "status": status, "adjudicated_by": None, "adjudicated_at": None,
        "adjudication_notes": None, "adjusted_score": None,
        "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z",
    }


def _app_with_mocks(db_mock: Any, kafka_mock: Any) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.db_pool = db_mock
    app.state.kafka_producer = kafka_mock
    return app


async def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ─── Tests ────────────────────────────────────────────────────────────────────

class TestHealth:
    @pytest.mark.asyncio
    async def test_health_ok(self):
        app = _app_with_mocks(MagicMock(), MagicMock())
        async with await _client(app) as c:
            resp = await c.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


class TestForms:
    def _app(self, db_mock):
        return _app_with_mocks(db_mock, AsyncMock())

    @pytest.mark.asyncio
    async def test_list_forms(self):
        db = MagicMock()
        with patch("plughub_evaluation_api.router._db.list_forms", new=AsyncMock(return_value=[_make_form()])):
            async with await _client(self._app(db)) as c:
                resp = await c.get("/v1/evaluation/forms?tenant_id=t1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 1
        assert data["forms"][0]["id"] == "evform_abc"

    @pytest.mark.asyncio
    async def test_create_form(self):
        form = _make_form()
        with patch("plughub_evaluation_api.router._db.create_form", new=AsyncMock(return_value=form)):
            async with await _client(self._app(MagicMock())) as c:
                resp = await c.post("/v1/evaluation/forms", json={
                    "tenant_id": "t1", "name": "Test Form"
                })
        assert resp.status_code == 201
        assert resp.json()["id"] == "evform_abc"

    @pytest.mark.asyncio
    async def test_get_form_found(self):
        with patch("plughub_evaluation_api.router._db.get_form", new=AsyncMock(return_value=_make_form())):
            async with await _client(self._app(MagicMock())) as c:
                resp = await c.get("/v1/evaluation/forms/evform_abc?tenant_id=t1")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_get_form_not_found(self):
        with patch("plughub_evaluation_api.router._db.get_form", new=AsyncMock(return_value=None)):
            async with await _client(self._app(MagicMock())) as c:
                resp = await c.get("/v1/evaluation/forms/evform_missing?tenant_id=t1")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_update_form(self):
        updated = _make_form()
        updated["name"] = "Updated"
        with patch("plughub_evaluation_api.router._db.update_form", new=AsyncMock(return_value=updated)):
            async with await _client(self._app(MagicMock())) as c:
                resp = await c.put("/v1/evaluation/forms/evform_abc?tenant_id=t1", json={"name": "Updated"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "Updated"

    @pytest.mark.asyncio
    async def test_delete_form_archives(self):
        archived = _make_form()
        archived["status"] = "archived"
        with patch("plughub_evaluation_api.router._db.update_form", new=AsyncMock(return_value=archived)):
            async with await _client(self._app(MagicMock())) as c:
                resp = await c.delete("/v1/evaluation/forms/evform_abc?tenant_id=t1")
        assert resp.status_code == 204

    @pytest.mark.asyncio
    async def test_delete_form_not_found(self):
        with patch("plughub_evaluation_api.router._db.update_form", new=AsyncMock(return_value=None)):
            async with await _client(self._app(MagicMock())) as c:
                resp = await c.delete("/v1/evaluation/forms/evform_missing?tenant_id=t1")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_form_with_dimensions(self):
        form = _make_form()
        form["dimensions"] = [{"id": "dim_1", "name": "Quality", "weight": 0.5, "criteria": []}]
        with patch("plughub_evaluation_api.router._db.create_form", new=AsyncMock(return_value=form)):
            async with await _client(self._app(MagicMock())) as c:
                resp = await c.post("/v1/evaluation/forms", json={
                    "tenant_id": "t1", "name": "Form with dims",
                    "dimensions": [{"id": "dim_1", "name": "Quality", "weight": 0.5, "criteria": []}],
                })
        assert resp.status_code == 201
        assert len(resp.json()["dimensions"]) == 1


class TestCampaigns:
    @pytest.mark.asyncio
    async def test_create_campaign(self):
        camp = _make_campaign()
        with patch("plughub_evaluation_api.router._db.get_form", new=AsyncMock(return_value=_make_form())), \
             patch("plughub_evaluation_api.router._db.create_campaign", new=AsyncMock(return_value=camp)):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/campaigns", json={
                    "tenant_id": "t1", "name": "Test Campaign",
                    "form_id": "evform_abc", "pool_id": "sac_ia",
                })
        assert resp.status_code == 201
        assert resp.json()["id"] == "evcampaign_abc"

    @pytest.mark.asyncio
    async def test_create_campaign_form_not_found(self):
        with patch("plughub_evaluation_api.router._db.get_form", new=AsyncMock(return_value=None)):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/campaigns", json={
                    "tenant_id": "t1", "name": "Bad Campaign",
                    "form_id": "evform_missing", "pool_id": "sac_ia",
                })
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_list_campaigns(self):
        with patch("plughub_evaluation_api.router._db.list_campaigns", new=AsyncMock(return_value=[_make_campaign()])):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.get("/v1/evaluation/campaigns?tenant_id=t1")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    @pytest.mark.asyncio
    async def test_get_campaign(self):
        with patch("plughub_evaluation_api.router._db.get_campaign", new=AsyncMock(return_value=_make_campaign())):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.get("/v1/evaluation/campaigns/evcampaign_abc?tenant_id=t1")
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_pause_campaign(self):
        paused = _make_campaign(status="paused")
        with patch("plughub_evaluation_api.router._db.update_campaign", new=AsyncMock(return_value=paused)):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/campaigns/evcampaign_abc/pause?tenant_id=t1")
        assert resp.status_code == 200
        assert resp.json()["status"] == "paused"

    @pytest.mark.asyncio
    async def test_resume_campaign(self):
        active = _make_campaign(status="active")
        with patch("plughub_evaluation_api.router._db.update_campaign", new=AsyncMock(return_value=active)):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/campaigns/evcampaign_abc/resume?tenant_id=t1")
        assert resp.status_code == 200
        assert resp.json()["status"] == "active"

    @pytest.mark.asyncio
    async def test_update_sampling_rules(self):
        updated = _make_campaign()
        updated["sampling_rules"] = {"mode": "fixed", "every_n": 3}
        with patch("plughub_evaluation_api.router._db.update_campaign", new=AsyncMock(return_value=updated)):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.put(
                    "/v1/evaluation/campaigns/evcampaign_abc?tenant_id=t1",
                    json={"sampling_rules": {"mode": "fixed", "every_n": 3}},
                )
        assert resp.status_code == 200
        assert resp.json()["sampling_rules"]["mode"] == "fixed"


class TestInstances:
    @pytest.mark.asyncio
    async def test_create_instance(self):
        inst = _make_instance()
        with patch("plughub_evaluation_api.router._db.get_campaign", new=AsyncMock(return_value=_make_campaign())), \
             patch("plughub_evaluation_api.router.compute_expires_at", new=AsyncMock(return_value=None)), \
             patch("plughub_evaluation_api.router._db.create_instance", new=AsyncMock(return_value=inst)), \
             patch("plughub_evaluation_api.router._kafka.emit_instance_created", new=AsyncMock()):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/instances", json={
                    "tenant_id": "t1", "campaign_id": "evcampaign_abc", "session_id": "sess_001",
                })
        assert resp.status_code == 201
        assert resp.json()["id"] == "evinstance_abc"

    @pytest.mark.asyncio
    async def test_create_instance_campaign_not_found(self):
        with patch("plughub_evaluation_api.router._db.get_campaign", new=AsyncMock(return_value=None)):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/instances", json={
                    "tenant_id": "t1", "campaign_id": "bad_campaign", "session_id": "sess_001",
                })
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_create_instance_paused_campaign_rejected(self):
        with patch("plughub_evaluation_api.router._db.get_campaign",
                   new=AsyncMock(return_value=_make_campaign(status="paused"))):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/instances", json={
                    "tenant_id": "t1", "campaign_id": "evcampaign_abc", "session_id": "sess_001",
                })
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_list_instances(self):
        with patch("plughub_evaluation_api.router._db.list_instances", new=AsyncMock(return_value=[_make_instance()])):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.get("/v1/evaluation/instances?tenant_id=t1")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    @pytest.mark.asyncio
    async def test_get_instance_not_found(self):
        with patch("plughub_evaluation_api.router._db.get_instance", new=AsyncMock(return_value=None)):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.get("/v1/evaluation/instances/missing?tenant_id=t1")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_claim_instance(self):
        inst = _make_instance(status="assigned")
        inst["evaluator_agent_id"] = "agente_avaliacao_v1-001"
        with patch("plughub_evaluation_api.router._db.claim_next_instance", new=AsyncMock(return_value=inst)), \
             patch("plughub_evaluation_api.router._kafka.emit_instance_assigned", new=AsyncMock()):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/instances/claim", json={
                    "tenant_id": "t1", "evaluator_agent_id": "agente_avaliacao_v1-001",
                })
        assert resp.status_code == 200
        assert resp.json()["status"] == "assigned"

    @pytest.mark.asyncio
    async def test_claim_no_available(self):
        with patch("plughub_evaluation_api.router._db.claim_next_instance", new=AsyncMock(return_value=None)):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/instances/claim", json={"tenant_id": "t1"})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_expire_instance(self):
        inst = _make_instance(status="expired")
        with patch("plughub_evaluation_api.router._db.update_instance_status", new=AsyncMock(return_value=inst)), \
             patch("plughub_evaluation_api.router._kafka.emit_instance_expired", new=AsyncMock()):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/instances/evinstance_abc/expire?tenant_id=t1")
        assert resp.status_code == 204


class TestIngest:
    @pytest.mark.asyncio
    async def test_ingest_creates_result(self):
        result = _make_result()
        with patch("plughub_evaluation_api.router._db.get_instance", new=AsyncMock(return_value=_make_instance())), \
             patch("plughub_evaluation_api.router._db.create_result", new=AsyncMock(return_value=result)), \
             patch("plughub_evaluation_api.router._db.create_criterion_responses", new=AsyncMock(return_value=[])), \
             patch("plughub_evaluation_api.router._kafka.emit_instance_completed", new=AsyncMock()):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/ingest", json={
                    "tenant_id": "t1",
                    "instance_id": "evinstance_abc",
                    "session_id": "sess_001",
                    "campaign_id": "evcampaign_abc",
                    "form_id": "evform_abc",
                    "evaluator_agent_id": "agente_avaliacao_v1-001",
                    "overall_score": 8.5,
                })
        assert resp.status_code == 201
        data = resp.json()
        assert data["result_id"] == "evresult_abc"
        assert data["eval_status"] == "submitted"

    @pytest.mark.asyncio
    async def test_ingest_instance_not_found(self):
        with patch("plughub_evaluation_api.router._db.get_instance", new=AsyncMock(return_value=None)):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/ingest", json={
                    "tenant_id": "t1", "instance_id": "missing",
                    "session_id": "s", "campaign_id": "c", "form_id": "f",
                    "evaluator_agent_id": "a",
                })
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_ingest_with_criterion_responses(self):
        result = _make_result()
        responses = [{"id": "evcrr_001", "criterion_id": "crit_1"}]
        with patch("plughub_evaluation_api.router._db.get_instance", new=AsyncMock(return_value=_make_instance())), \
             patch("plughub_evaluation_api.router._db.create_result", new=AsyncMock(return_value=result)), \
             patch("plughub_evaluation_api.router._db.create_criterion_responses", new=AsyncMock(return_value=responses)), \
             patch("plughub_evaluation_api.router._kafka.emit_instance_completed", new=AsyncMock()):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/ingest", json={
                    "tenant_id": "t1", "instance_id": "evinstance_abc",
                    "session_id": "sess_001", "campaign_id": "evcampaign_abc",
                    "form_id": "evform_abc", "evaluator_agent_id": "a",
                    "criterion_responses": [{"criterion_id": "crit_1", "score": 8.0}],
                })
        assert resp.status_code == 201
        assert resp.json()["criteria_rows_created"] == 1

    @pytest.mark.asyncio
    async def test_ingest_emits_kafka(self):
        result = _make_result()
        kafka_mock = AsyncMock()
        emit_mock = AsyncMock()
        with patch("plughub_evaluation_api.router._db.get_instance", new=AsyncMock(return_value=_make_instance())), \
             patch("plughub_evaluation_api.router._db.create_result", new=AsyncMock(return_value=result)), \
             patch("plughub_evaluation_api.router._db.create_criterion_responses", new=AsyncMock(return_value=[])), \
             patch("plughub_evaluation_api.router._kafka.emit_instance_completed", new=emit_mock):
            async with await _client(_app_with_mocks(MagicMock(), kafka_mock)) as c:
                await c.post("/v1/evaluation/ingest", json={
                    "tenant_id": "t1", "instance_id": "evinstance_abc",
                    "session_id": "sess_001", "campaign_id": "evcampaign_abc",
                    "form_id": "evform_abc", "evaluator_agent_id": "a",
                })
        emit_mock.assert_called_once()

    @pytest.mark.asyncio
    async def test_ingest_comparison_mode(self):
        result = _make_result()
        result["comparison_mode"] = True
        with patch("plughub_evaluation_api.router._db.get_instance", new=AsyncMock(return_value=_make_instance())), \
             patch("plughub_evaluation_api.router._db.create_result", new=AsyncMock(return_value=result)), \
             patch("plughub_evaluation_api.router._db.create_criterion_responses", new=AsyncMock(return_value=[])), \
             patch("plughub_evaluation_api.router._kafka.emit_instance_completed", new=AsyncMock()):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/ingest", json={
                    "tenant_id": "t1", "instance_id": "evinstance_abc",
                    "session_id": "sess_001", "campaign_id": "evcampaign_abc",
                    "form_id": "evform_abc", "evaluator_agent_id": "a",
                    "comparison_mode": True,
                    "comparison_report": {"jaccard_avg": 0.85},
                })
        assert resp.status_code == 201


class TestResults:
    @pytest.mark.asyncio
    async def test_list_results(self):
        with patch("plughub_evaluation_api.router._db.list_results", new=AsyncMock(return_value=[_make_result()])):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.get("/v1/evaluation/results?tenant_id=t1")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    @pytest.mark.asyncio
    async def test_get_result_not_found(self):
        with patch("plughub_evaluation_api.router._db.get_result", new=AsyncMock(return_value=None)):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.get("/v1/evaluation/results/missing?tenant_id=t1")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_get_criteria(self):
        responses = [{"id": "evcrr_001", "criterion_id": "crit_1", "score": 8.0}]
        with patch("plughub_evaluation_api.router._db.list_criterion_responses", new=AsyncMock(return_value=responses)):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.get("/v1/evaluation/results/evresult_abc/criteria?tenant_id=t1")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    @pytest.mark.asyncio
    async def test_review_result(self):
        reviewed = _make_result(eval_status="reviewed")
        reviewed["reviewer_outcome"] = "approved"
        with patch("plughub_evaluation_api.router._db.update_result", new=AsyncMock(return_value=reviewed)):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/results/evresult_abc/review?tenant_id=t1", json={
                    "reviewer_agent_id": "agente_reviewer_v1-001",
                    "reviewer_outcome": "approved",
                })
        assert resp.status_code == 200
        assert resp.json()["reviewer_outcome"] == "approved"

    @pytest.mark.asyncio
    async def test_review_invalid_outcome(self):
        async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
            resp = await c.post("/v1/evaluation/results/evresult_abc/review?tenant_id=t1", json={
                "reviewer_agent_id": "a", "reviewer_outcome": "invalid_value",
            })
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_lock_result(self):
        locked = _make_result(eval_status="locked")
        with patch("plughub_evaluation_api.router._db.update_result", new=AsyncMock(return_value=locked)):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/results/evresult_abc/lock?tenant_id=t1",
                                    json={"locked_by": "admin"})
        assert resp.status_code == 200
        assert resp.json()["eval_status"] == "locked"


class TestContestations:
    @pytest.mark.asyncio
    async def test_create_contestation(self):
        contest = _make_contestation()
        with patch("plughub_evaluation_api.router._db.get_result", new=AsyncMock(return_value=_make_result())), \
             patch("plughub_evaluation_api.router._db.create_contestation", new=AsyncMock(return_value=contest)), \
             patch("plughub_evaluation_api.router._kafka.emit_contestation_opened", new=AsyncMock()):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/contestations", json={
                    "tenant_id": "t1", "result_id": "evresult_abc",
                    "instance_id": "evinstance_abc", "session_id": "sess_001",
                    "contested_by": "agent_human_001",
                    "contestation_reason": "Score too low",
                })
        assert resp.status_code == 201
        assert resp.json()["id"] == "evcontest_abc"

    @pytest.mark.asyncio
    async def test_cannot_contest_locked_result(self):
        locked = _make_result(eval_status="locked")
        with patch("plughub_evaluation_api.router._db.get_result", new=AsyncMock(return_value=locked)):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/contestations", json={
                    "tenant_id": "t1", "result_id": "evresult_abc",
                    "instance_id": "evinstance_abc", "session_id": "sess_001",
                    "contested_by": "agent_001",
                })
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_list_contestations(self):
        with patch("plughub_evaluation_api.router._db.list_contestations",
                   new=AsyncMock(return_value=[_make_contestation()])):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.get("/v1/evaluation/contestations?tenant_id=t1")
        assert resp.status_code == 200
        assert resp.json()["count"] == 1

    @pytest.mark.asyncio
    async def test_adjudicate_contestation(self):
        adj = _make_contestation(status="accepted")
        adj["result_id"] = "evresult_abc"
        pool_mock = MagicMock()
        pool_mock.acquire = MagicMock(return_value=MagicMock(
            __aenter__=AsyncMock(return_value=MagicMock(
                fetchrow=AsyncMock(return_value=adj)
            )),
            __aexit__=AsyncMock(return_value=None),
        ))
        with patch("plughub_evaluation_api.router._db.adjudicate_contestation", new=AsyncMock(return_value=adj)), \
             patch("plughub_evaluation_api.router._kafka.emit_contestation_closed", new=AsyncMock()):
            async with await _client(_app_with_mocks(pool_mock, AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/contestations/evcontest_abc/adjudicate?tenant_id=t1", json={
                    "adjudicated_by": "supervisor_001",
                    "status": "accepted",
                })
        assert resp.status_code == 200
        assert resp.json()["status"] == "accepted"

    @pytest.mark.asyncio
    async def test_adjudicate_invalid_status(self):
        async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
            resp = await c.post("/v1/evaluation/contestations/evcontest_abc/adjudicate?tenant_id=t1", json={
                "adjudicated_by": "sup", "status": "bad_status",
            })
        assert resp.status_code == 400


class TestSampleCheck:
    @pytest.mark.asyncio
    async def test_should_sample_true(self):
        camp = _make_campaign()
        camp["sampling_rules"] = {"mode": "all"}
        with patch("plughub_evaluation_api.router._db.get_campaign", new=AsyncMock(return_value=camp)):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/sample", json={
                    "tenant_id": "t1", "campaign_id": "evcampaign_abc",
                    "session_id": "sess_001", "session_meta": {},
                })
        assert resp.status_code == 200
        assert resp.json()["should_sample"] is True

    @pytest.mark.asyncio
    async def test_paused_campaign_not_sampled(self):
        with patch("plughub_evaluation_api.router._db.get_campaign",
                   new=AsyncMock(return_value=_make_campaign(status="paused"))):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/sample", json={
                    "tenant_id": "t1", "campaign_id": "evcampaign_abc", "session_id": "s",
                })
        assert resp.status_code == 200
        assert resp.json()["should_sample"] is False

    @pytest.mark.asyncio
    async def test_campaign_not_found_404(self):
        with patch("plughub_evaluation_api.router._db.get_campaign", new=AsyncMock(return_value=None)):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.post("/v1/evaluation/sample", json={
                    "tenant_id": "t1", "campaign_id": "missing", "session_id": "s",
                })
        assert resp.status_code == 404


class TestReports:
    @pytest.mark.asyncio
    async def test_campaign_report(self):
        camp = _make_campaign()
        pool_mock = MagicMock()
        conn_mock = AsyncMock()
        conn_mock.fetch = AsyncMock(return_value=[])
        ctx_mock = MagicMock(__aenter__=AsyncMock(return_value=conn_mock), __aexit__=AsyncMock(return_value=None))
        pool_mock.acquire = MagicMock(return_value=ctx_mock)
        with patch("plughub_evaluation_api.router._db.get_campaign", new=AsyncMock(return_value=camp)):
            async with await _client(_app_with_mocks(pool_mock, AsyncMock())) as c:
                resp = await c.get("/v1/evaluation/reports/campaign/evcampaign_abc?tenant_id=t1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["campaign"]["id"] == "evcampaign_abc"
        assert "status_breakdown" in data
        assert "criteria_breakdown" in data

    @pytest.mark.asyncio
    async def test_campaign_report_not_found(self):
        with patch("plughub_evaluation_api.router._db.get_campaign", new=AsyncMock(return_value=None)):
            async with await _client(_app_with_mocks(MagicMock(), AsyncMock())) as c:
                resp = await c.get("/v1/evaluation/reports/campaign/missing?tenant_id=t1")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_agent_report(self):
        pool_mock = MagicMock()
        conn_mock = AsyncMock()
        conn_mock.fetch = AsyncMock(return_value=[])
        ctx_mock = MagicMock(__aenter__=AsyncMock(return_value=conn_mock), __aexit__=AsyncMock(return_value=None))
        pool_mock.acquire = MagicMock(return_value=ctx_mock)
        async with await _client(_app_with_mocks(pool_mock, AsyncMock())) as c:
            resp = await c.get("/v1/evaluation/reports/agent?tenant_id=t1")
        assert resp.status_code == 200
        assert "agents" in resp.json()
