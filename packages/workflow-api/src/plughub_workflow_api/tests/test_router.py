"""
test_router.py
Unit tests for the Workflow API router.

Strategy:
  - asyncpg pool is replaced with a MagicMock whose fetchrow/fetch/execute
    methods return controlled fake data
  - kafka producer is None (disabled)
  - httpx AsyncClient used as ASGI test client
  - calendar_client.calculate_deadline is patched to avoid HTTP calls

Coverage:
  TestTrigger         — POST /v1/workflow/trigger
  TestPersistSuspend  — POST /v1/workflow/instances/{id}/persist-suspend
  TestResume          — POST /v1/workflow/resume
  TestComplete        — POST /v1/workflow/instances/{id}/complete
  TestFail            — POST /v1/workflow/instances/{id}/fail
  TestCancel          — POST /v1/workflow/instances/{id}/cancel
  TestList            — GET  /v1/workflow/instances
  TestDetail          — GET  /v1/workflow/instances/{id}
  TestHealth          — GET  /v1/health
  TestTimeoutScanner  — timeout_job._scan_once
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from plughub_workflow_api.main import app
from plughub_workflow_api.config import Settings

# ─────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────

INSTANCE_ID = str(uuid4())
RESUME_TOKEN = "tok-" + str(uuid4())[:8]
NOW_ISO = datetime.now(timezone.utc).isoformat()
EXPIRES_ISO = (datetime.now(timezone.utc) + timedelta(hours=48)).isoformat()

# Fake row as asyncpg.Record dict (our _row_to_instance reads these fields)
def fake_row(overrides: dict = {}) -> MagicMock:
    row = MagicMock()
    defaults = {
        "id":                uuid4(),
        "installation_id":   "inst-001",
        "organization_id":   "org-001",
        "tenant_id":         "tenant-test",
        "flow_id":           "wf_approval_v1",
        "session_id":        None,
        "pool_id":           None,
        "status":            "active",
        "current_step":      "aguardar_aprovacao",
        "pipeline_state":    json.dumps({}),
        "suspend_reason":    None,
        "resume_token":      None,
        "resume_expires_at": None,
        "suspended_at":      None,
        "resumed_at":        None,
        "completed_at":      None,
        "created_at":        datetime.now(timezone.utc),
        "metadata":          json.dumps({}),
    }
    defaults.update(overrides)
    row.__getitem__ = lambda self, key: defaults[key]
    row.keys = lambda: defaults.keys()
    return row


def make_pool(fetchrow_result=None, fetch_result=None) -> MagicMock:
    pool = MagicMock()
    pool.fetchrow  = AsyncMock(return_value=fetchrow_result)
    pool.fetch     = AsyncMock(return_value=fetch_result or [])
    pool.execute   = AsyncMock(return_value="UPDATE 1")
    pool.fetchval  = AsyncMock(return_value=1)
    return pool


def make_settings() -> Settings:
    return Settings(
        installation_id="inst-001",
        organization_id="org-001",
        database_url="postgresql://x:x@localhost/x",
        kafka_enabled=False,
        calendar_api_url="http://calendar:3700",
    )


@pytest.fixture
def client():
    """Sync test client with mocked state."""
    app.state.pool     = make_pool()
    app.state.settings = make_settings()
    app.state.producer = None
    return TestClient(app)


# ─────────────────────────────────────────────
# TestTrigger
# ─────────────────────────────────────────────

class TestTrigger:
    def test_creates_instance_and_returns_201(self, client):
        app.state.pool = make_pool(fetchrow_result=fake_row())
        resp = client.post("/v1/workflow/trigger", json={
            "tenant_id": "tenant-test",
            "flow_id":   "wf_approval_v1",
            "trigger_type": "manual",
            "metadata": {"invoice_id": "INV-001"},
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["flow_id"] == "wf_approval_v1"
        assert data["status"]  == "active"

    def test_session_id_optional(self, client):
        app.state.pool = make_pool(fetchrow_result=fake_row())
        resp = client.post("/v1/workflow/trigger", json={
            "tenant_id":  "t1",
            "flow_id":    "wf_onboarding_v1",
            "session_id": "session-abc",
        })
        assert resp.status_code == 201

    def test_missing_required_fields_returns_422(self, client):
        resp = client.post("/v1/workflow/trigger", json={"tenant_id": "t1"})
        assert resp.status_code == 422


# ─────────────────────────────────────────────
# TestPersistSuspend
# ─────────────────────────────────────────────

class TestPersistSuspend:
    def test_suspends_active_instance(self, client):
        active_row     = fake_row({"status": "active"})
        suspended_row  = fake_row({
            "status":            "suspended",
            "resume_token":      RESUME_TOKEN,
            "suspend_reason":    "approval",
            "resume_expires_at": datetime.now(timezone.utc) + timedelta(hours=48),
            "suspended_at":      datetime.now(timezone.utc),
        })
        # fetchrow called twice: get_instance + update
        app.state.pool = MagicMock()
        app.state.pool.fetchrow  = AsyncMock(side_effect=[active_row, suspended_row])
        app.state.pool.fetchval  = AsyncMock(return_value=1)

        with patch(
            "plughub_workflow_api.router.calculate_deadline",
            new=AsyncMock(return_value=datetime.now(timezone.utc) + timedelta(hours=48))
        ):
            resp = client.post(
                f"/v1/workflow/instances/{INSTANCE_ID}/persist-suspend",
                json={
                    "step_id":        "aguardar_aprovacao",
                    "resume_token":   RESUME_TOKEN,
                    "reason":         "approval",
                    "timeout_hours":  48,
                    "business_hours": True,
                    "entity_type":    "workflow",
                    "entity_id":      INSTANCE_ID,
                },
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "resume_expires_at" in data
        assert data["instance"]["status"] == "suspended"

    def test_not_found_returns_404(self, client):
        app.state.pool = make_pool(fetchrow_result=None)
        resp = client.post(
            f"/v1/workflow/instances/{INSTANCE_ID}/persist-suspend",
            json={
                "step_id": "s", "resume_token": "tok", "reason": "approval",
                "timeout_hours": 24, "business_hours": False,
            },
        )
        assert resp.status_code == 404

    def test_terminal_status_returns_409(self, client):
        app.state.pool = make_pool(fetchrow_result=fake_row({"status": "completed"}))
        resp = client.post(
            f"/v1/workflow/instances/{INSTANCE_ID}/persist-suspend",
            json={
                "step_id": "s", "resume_token": "tok", "reason": "approval",
                "timeout_hours": 24, "business_hours": False,
            },
        )
        assert resp.status_code == 409

    def test_wall_clock_fallback_when_no_entity_id(self, client):
        active_row    = fake_row({"status": "active"})
        suspended_row = fake_row({
            "status": "suspended",
            "resume_token": RESUME_TOKEN,
            "suspend_reason": "timer",
            "resume_expires_at": datetime.now(timezone.utc) + timedelta(hours=24),
            "suspended_at": datetime.now(timezone.utc),
        })
        app.state.pool = MagicMock()
        app.state.pool.fetchrow = AsyncMock(side_effect=[active_row, suspended_row])
        app.state.pool.fetchval = AsyncMock(return_value=1)

        resp = client.post(
            f"/v1/workflow/instances/{INSTANCE_ID}/persist-suspend",
            json={
                "step_id": "wait_step", "resume_token": RESUME_TOKEN,
                "reason": "timer", "timeout_hours": 24,
                "business_hours": True,
                # entity_id omitted → falls back to wall-clock
            },
        )
        assert resp.status_code == 200


# ─────────────────────────────────────────────
# TestResume
# ─────────────────────────────────────────────

class TestResume:
    def _suspended_row(self, future_expiry=True):
        expires = (
            datetime.now(timezone.utc) + timedelta(hours=1)
            if future_expiry
            else datetime.now(timezone.utc) - timedelta(hours=1)
        )
        return fake_row({
            "status":            "suspended",
            "resume_token":      RESUME_TOKEN,
            "suspend_reason":    "approval",
            "resume_expires_at": expires,
            "suspended_at":      datetime.now(timezone.utc) - timedelta(minutes=30),
        })

    def test_approves_and_returns_200(self, client):
        suspended  = self._suspended_row()
        active_row = fake_row({"status": "active", "resume_token": None})
        app.state.pool = MagicMock()
        app.state.pool.fetchrow = AsyncMock(side_effect=[suspended, active_row])
        app.state.pool.fetchval = AsyncMock(return_value=1)

        resp = client.post("/v1/workflow/resume", json={
            "token":    RESUME_TOKEN,
            "decision": "approved",
            "payload":  {"approved_by": "maria"},
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["decision"] == "approved"
        assert "wait_duration_ms" in data

    def test_token_not_found_returns_404(self, client):
        app.state.pool = make_pool(fetchrow_result=None)
        resp = client.post("/v1/workflow/resume", json={
            "token": "nonexistent", "decision": "approved",
        })
        assert resp.status_code == 404

    def test_not_suspended_returns_409(self, client):
        app.state.pool = make_pool(fetchrow_result=fake_row({"status": "active", "resume_token": RESUME_TOKEN}))
        resp = client.post("/v1/workflow/resume", json={
            "token": RESUME_TOKEN, "decision": "approved",
        })
        assert resp.status_code == 409

    def test_expired_token_returns_410(self, client):
        app.state.pool = make_pool(fetchrow_result=self._suspended_row(future_expiry=False))
        resp = client.post("/v1/workflow/resume", json={
            "token": RESUME_TOKEN, "decision": "approved",
        })
        assert resp.status_code == 410

    def test_timeout_decision_skips_expiry_check(self, client):
        # system-generated timeout: expired token should still succeed
        expired_suspended = self._suspended_row(future_expiry=False)
        active_row        = fake_row({"status": "active", "resume_token": None})
        app.state.pool = MagicMock()
        app.state.pool.fetchrow = AsyncMock(side_effect=[expired_suspended, active_row])
        app.state.pool.fetchval = AsyncMock(return_value=1)

        resp = client.post("/v1/workflow/resume", json={
            "token": RESUME_TOKEN, "decision": "timeout",
        })
        assert resp.status_code == 200


# ─────────────────────────────────────────────
# TestComplete / TestFail
# ─────────────────────────────────────────────

class TestComplete:
    def test_completes_active_instance(self, client):
        completed = fake_row({"status": "completed", "completed_at": datetime.now(timezone.utc)})
        app.state.pool = MagicMock()
        app.state.pool.fetchrow = AsyncMock(side_effect=[fake_row(), completed])
        app.state.pool.fetchval = AsyncMock(return_value=1)

        resp = client.post(
            f"/v1/workflow/instances/{INSTANCE_ID}/complete",
            json={"outcome": "resolved", "pipeline_state": {}},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"

    def test_not_found_returns_404(self, client):
        app.state.pool = make_pool(fetchrow_result=None)
        resp = client.post(
            f"/v1/workflow/instances/{INSTANCE_ID}/complete",
            json={"outcome": "resolved"},
        )
        assert resp.status_code == 404


class TestFail:
    def test_fails_active_instance(self, client):
        failed_row = fake_row({"status": "failed"})
        app.state.pool = MagicMock()
        app.state.pool.fetchrow = AsyncMock(side_effect=[fake_row(), failed_row])
        app.state.pool.fetchval = AsyncMock(return_value=1)

        resp = client.post(
            f"/v1/workflow/instances/{INSTANCE_ID}/fail",
            json={"error": "Unhandled exception in step X"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "failed"


# ─────────────────────────────────────────────
# TestCancel
# ─────────────────────────────────────────────

class TestCancel:
    def test_cancels_active_instance(self, client):
        cancelled = fake_row({"status": "cancelled"})
        app.state.pool = MagicMock()
        app.state.pool.fetchrow = AsyncMock(side_effect=[fake_row(), cancelled])
        app.state.pool.fetchval = AsyncMock(return_value=1)

        resp = client.post(
            f"/v1/workflow/instances/{INSTANCE_ID}/cancel",
            json={"cancelled_by": "operator", "reason": "Teste"},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    def test_cannot_cancel_completed_instance(self, client):
        app.state.pool = make_pool(fetchrow_result=fake_row({"status": "completed"}))
        resp = client.post(
            f"/v1/workflow/instances/{INSTANCE_ID}/cancel",
            json={"cancelled_by": "operator"},
        )
        assert resp.status_code == 409


# ─────────────────────────────────────────────
# TestList / TestDetail
# ─────────────────────────────────────────────

class TestList:
    def test_returns_list(self, client):
        app.state.pool = make_pool(fetch_result=[fake_row(), fake_row()])
        resp = client.get("/v1/workflow/instances?tenant_id=tenant-test")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_empty_list(self, client):
        app.state.pool = make_pool(fetch_result=[])
        resp = client.get("/v1/workflow/instances?tenant_id=tenant-test")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_requires_tenant_id(self, client):
        resp = client.get("/v1/workflow/instances")
        assert resp.status_code == 422

    def test_limit_capped_at_200(self, client):
        app.state.pool = make_pool(fetch_result=[])
        resp = client.get("/v1/workflow/instances?tenant_id=t&limit=500")
        assert resp.status_code == 200  # capped, not rejected


class TestDetail:
    def test_returns_instance(self, client):
        app.state.pool = make_pool(fetchrow_result=fake_row())
        resp = client.get(f"/v1/workflow/instances/{INSTANCE_ID}")
        assert resp.status_code == 200
        assert resp.json()["flow_id"] == "wf_approval_v1"

    def test_not_found_returns_404(self, client):
        app.state.pool = make_pool(fetchrow_result=None)
        resp = client.get(f"/v1/workflow/instances/{INSTANCE_ID}")
        assert resp.status_code == 404


# ─────────────────────────────────────────────
# TestHealth
# ─────────────────────────────────────────────

class TestHealth:
    def test_returns_ok(self, client):
        app.state.pool = make_pool()
        resp = client.get("/v1/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_returns_degraded_on_pg_error(self, client):
        pool = MagicMock()
        pool.fetchval = AsyncMock(side_effect=Exception("connection refused"))
        app.state.pool = pool
        resp = client.get("/v1/health")
        assert resp.status_code == 503
        assert resp.json()["postgres"] == "error"


# ─────────────────────────────────────────────
# TestTimeoutScanner
# ─────────────────────────────────────────────

class TestTimeoutScanner:
    @pytest.mark.asyncio
    async def test_scan_emits_timed_out_events(self):
        from plughub_workflow_api.timeout_job import _scan_once

        timed_out_rows = [
            {
                "id": str(uuid4()), "tenant_id": "t1", "flow_id": "wf_test",
                "current_step": "aguardar", "suspended_at": NOW_ISO,
                "status": "timed_out",
            },
            {
                "id": str(uuid4()), "tenant_id": "t2", "flow_id": "wf_test",
                "current_step": "step_b", "suspended_at": NOW_ISO,
                "status": "timed_out",
            },
        ]

        pool = MagicMock()
        pool.fetch = AsyncMock(return_value=[])  # db_timeout_expired_instances uses execute+returning

        # Patch db function directly
        with patch(
            "plughub_workflow_api.timeout_job.db_timeout_expired_instances",
            new=AsyncMock(return_value=timed_out_rows),
        ), patch(
            "plughub_workflow_api.timeout_job.emit_timed_out",
            new=AsyncMock(),
        ) as mock_emit:
            app_mock = MagicMock()
            app_mock.state.pool     = pool
            app_mock.state.settings = make_settings()
            app_mock.state.producer = None

            await _scan_once(app_mock)

            assert mock_emit.call_count == 2

    @pytest.mark.asyncio
    async def test_scan_noop_when_no_expired(self):
        from plughub_workflow_api.timeout_job import _scan_once

        with patch(
            "plughub_workflow_api.timeout_job.db_timeout_expired_instances",
            new=AsyncMock(return_value=[]),
        ), patch(
            "plughub_workflow_api.timeout_job.emit_timed_out",
            new=AsyncMock(),
        ) as mock_emit:
            app_mock = MagicMock()
            app_mock.state.pool     = MagicMock()
            app_mock.state.settings = make_settings()
            app_mock.state.producer = None

            await _scan_once(app_mock)
            mock_emit.assert_not_called()
