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
  TestTrigger           — POST /v1/workflow/trigger
  TestPersistSuspend    — POST /v1/workflow/instances/{id}/persist-suspend
  TestResume            — POST /v1/workflow/resume
  TestComplete          — POST /v1/workflow/instances/{id}/complete
  TestFail              — POST /v1/workflow/instances/{id}/fail
  TestCancel            — POST /v1/workflow/instances/{id}/cancel
  TestList              — GET  /v1/workflow/instances
  TestDetail            — GET  /v1/workflow/instances/{id}
  TestHealth            — GET  /v1/health
  TestTimeoutScanner    — timeout_job._scan_once
  TestWebhookCRUD       — POST/GET/PATCH/rotate/DELETE /v1/workflow/webhooks
  TestWebhookTrigger    — POST /v1/workflow/webhook/{id} (public endpoint)
  TestWebhookDeliveries — GET  /v1/workflow/webhooks/{id}/deliveries
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
        "id":                  uuid4(),
        "installation_id":     "inst-001",
        "organization_id":     "org-001",
        "tenant_id":           "tenant-test",
        "flow_id":             "wf_approval_v1",
        "session_id":          None,
        "origin_session_id":   None,
        "pool_id":             None,
        "campaign_id":         None,
        "status":              "active",
        "current_step":        "aguardar_aprovacao",
        "pipeline_state":      json.dumps({}),
        "suspend_reason":      None,
        "resume_token":        None,
        "resume_expires_at":   None,
        "suspended_at":        None,
        "resumed_at":          None,
        "completed_at":        None,
        "outcome":             None,
        "created_at":          datetime.now(timezone.utc),
        "metadata":            json.dumps({}),
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

    def test_duplicate_resume_token_is_idempotent(self, client):
        """
        B2-01: If persist-suspend is called twice with the same resume_token
        (engine crashed between persistSuspend and saving expiresKey), the second
        call must succeed and return the ORIGINAL resume_expires_at — not a new one.
        """
        original_expires = datetime.now(timezone.utc) + timedelta(hours=48)

        # First call: instance is active → transitions to suspended
        active_row = fake_row({"status": "active"})
        # Second call (retry): instance is already suspended with same token
        # db_suspend_instance UPDATE preserves resume_expires_at via CASE expression
        already_suspended_row = fake_row({
            "status":            "suspended",
            "resume_token":      RESUME_TOKEN,
            "suspend_reason":    "approval",
            "resume_expires_at": original_expires,
            "suspended_at":      datetime.now(timezone.utc) - timedelta(seconds=5),
        })

        suspended_row = fake_row({
            "status":            "suspended",
            "resume_token":      RESUME_TOKEN,
            "suspend_reason":    "approval",
            "resume_expires_at": original_expires,
            "suspended_at":      datetime.now(timezone.utc),
        })

        app.state.pool = MagicMock()
        # Each persist-suspend call: fetchrow×2 (db_get_instance + db_suspend_instance)
        # Request 1: active → suspended
        # Request 2 (retry): already suspended → preserved
        app.state.pool.fetchrow = AsyncMock(side_effect=[
            active_row,            # request 1: db_get_instance
            suspended_row,         # request 1: db_suspend_instance
            already_suspended_row, # request 2 (retry): db_get_instance
            already_suspended_row, # request 2 (retry): db_suspend_instance (preserves expires)
        ])
        app.state.pool.fetchval = AsyncMock(return_value=1)

        payload = {
            "step_id":        "aguardar_aprovacao",
            "resume_token":   RESUME_TOKEN,
            "reason":         "approval",
            "timeout_hours":  48,
            "business_hours": True,
            # entity_id is required to trigger the calculate_deadline path
            # (business_hours=True but no entity_id → wall-clock fallback, non-deterministic)
            "entity_id":      INSTANCE_ID,
        }

        with patch(
            "plughub_workflow_api.router.calculate_deadline",
            new=AsyncMock(return_value=original_expires)
        ):
            resp1 = client.post(
                f"/v1/workflow/instances/{INSTANCE_ID}/persist-suspend",
                json=payload,
            )
            resp2 = client.post(
                f"/v1/workflow/instances/{INSTANCE_ID}/persist-suspend",
                json=payload,
            )

        assert resp1.status_code == 200
        assert resp2.status_code == 200
        # Both responses must carry the same resume_expires_at
        assert resp1.json()["resume_expires_at"] == resp2.json()["resume_expires_at"]


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
            "plughub_workflow_api.timeout_job.db_timeout_expired_collects",
            new=AsyncMock(return_value=[]),
        ), patch(
            "plughub_workflow_api.timeout_job.emit_timed_out",
            new=AsyncMock(),
        ) as mock_emit:
            app_mock = MagicMock()
            pool = MagicMock()
            pool.fetch  = AsyncMock(return_value=[])
            pool.execute = AsyncMock(return_value="UPDATE 0")
            app_mock.state.pool     = pool
            app_mock.state.settings = make_settings()
            app_mock.state.producer = None

            await _scan_once(app_mock)
            mock_emit.assert_not_called()


# ─────────────────────────────────────────────
# Webhook helpers
# ─────────────────────────────────────────────

WEBHOOK_ID     = str(uuid4())
ADMIN_TOKEN    = "secret-admin-token"
PLAIN_TOKEN    = "plughub_wh_" + "A" * 43      # fake plain token (correct prefix format)
TOKEN_PREFIX   = PLAIN_TOKEN[:16]               # "plughub_wh_AAAAA"

import hashlib as _hashlib
TOKEN_HASH = _hashlib.sha256(PLAIN_TOKEN.encode()).hexdigest()


def fake_webhook(overrides: dict = {}) -> MagicMock:
    """Fake webhook row matching _row_to_webhook() field access."""
    row = MagicMock()
    defaults = {
        "id":                uuid4(),
        "tenant_id":         "tenant-test",
        "flow_id":           "wf_approval_v1",
        "description":       "Test webhook",
        "token_prefix":      TOKEN_PREFIX,
        "active":            True,
        "trigger_count":     0,
        "last_triggered_at": None,
        "context_override":  "{}",          # JSON string as asyncpg returns
        "created_at":        datetime.now(timezone.utc),
        "updated_at":        datetime.now(timezone.utc),
    }
    defaults.update(overrides)
    row.__getitem__ = lambda self, key: defaults[key]
    row.keys = lambda: defaults.keys()
    return row


def fake_delivery(overrides: dict = {}) -> MagicMock:
    """Fake delivery row matching _row_to_delivery() field access."""
    row = MagicMock()
    defaults = {
        "id":           uuid4(),
        "webhook_id":   uuid4(),
        "tenant_id":    "tenant-test",
        "triggered_at": datetime.now(timezone.utc),
        "status_code":  202,
        "payload_hash": "abc123",
        "instance_id":  uuid4(),
        "error":        None,
        "latency_ms":   42,
    }
    defaults.update(overrides)
    row.__getitem__ = lambda self, key: defaults[key]
    row.keys = lambda: defaults.keys()
    return row


def make_admin_settings() -> Settings:
    return Settings(
        installation_id="inst-001",
        organization_id="org-001",
        database_url="postgresql://x:x@localhost/x",
        kafka_enabled=False,
        calendar_api_url="http://calendar:3700",
        admin_token=ADMIN_TOKEN,
    )


@pytest.fixture
def admin_client():
    """Sync test client with admin_token configured."""
    app.state.pool     = make_pool()
    app.state.settings = make_admin_settings()
    app.state.producer = None
    return TestClient(app)


# ─────────────────────────────────────────────
# TestWebhookCRUD
# ─────────────────────────────────────────────

class TestWebhookCRUD:
    """Admin-protected CRUD endpoints for webhook registration."""

    def test_create_webhook_returns_201_with_token(self, admin_client):
        """POST /v1/workflow/webhooks → 201; response includes plain token."""
        app.state.pool = make_pool(fetchrow_result=fake_webhook())
        resp = admin_client.post(
            "/v1/workflow/webhooks",
            json={
                "tenant_id":        "tenant-test",
                "flow_id":          "wf_approval_v1",
                "description":      "Salesforce trigger",
                "context_override": {"source": "salesforce"},
            },
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "token" in data                      # plain token returned once
        assert data["token"].startswith("plughub_wh_")
        assert data["flow_id"] == "wf_approval_v1"

    def test_create_webhook_missing_admin_token_returns_401(self, admin_client):
        """No X-Admin-Token header → 401."""
        resp = admin_client.post(
            "/v1/workflow/webhooks",
            json={"tenant_id": "t", "flow_id": "wf_test"},
        )
        assert resp.status_code == 401

    def test_create_webhook_wrong_token_returns_401(self, admin_client):
        """Wrong X-Admin-Token → 401."""
        resp = admin_client.post(
            "/v1/workflow/webhooks",
            json={"tenant_id": "t", "flow_id": "wf_test"},
            headers={"X-Admin-Token": "wrong-token"},
        )
        assert resp.status_code == 401

    def test_list_webhooks_returns_records(self, admin_client):
        """GET /v1/workflow/webhooks → list of webhook objects."""
        app.state.pool = make_pool(fetch_result=[fake_webhook(), fake_webhook()])
        resp = admin_client.get(
            "/v1/workflow/webhooks?tenant_id=tenant-test",
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_list_webhooks_active_filter(self, admin_client):
        """GET /v1/workflow/webhooks?active=true → filtered list."""
        app.state.pool = make_pool(fetch_result=[fake_webhook()])
        resp = admin_client.get(
            "/v1/workflow/webhooks?tenant_id=tenant-test&active=true",
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_get_webhook_returns_detail(self, admin_client):
        """GET /v1/workflow/webhooks/{id} → webhook detail."""
        app.state.pool = make_pool(fetchrow_result=fake_webhook())
        resp = admin_client.get(
            f"/v1/workflow/webhooks/{WEBHOOK_ID}",
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )
        assert resp.status_code == 200
        assert resp.json()["flow_id"] == "wf_approval_v1"

    def test_get_webhook_not_found_returns_404(self, admin_client):
        """GET /v1/workflow/webhooks/{id} with unknown id → 404."""
        app.state.pool = make_pool(fetchrow_result=None)
        resp = admin_client.get(
            f"/v1/workflow/webhooks/{WEBHOOK_ID}",
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )
        assert resp.status_code == 404

    def test_patch_webhook_deactivates(self, admin_client):
        """PATCH /v1/workflow/webhooks/{id} → active=False in response."""
        inactive_wh = fake_webhook({"active": False})
        # fetchrow called twice: db_get_webhook + db_update_webhook
        pool = MagicMock()
        pool.fetchrow = AsyncMock(side_effect=[fake_webhook(), inactive_wh])
        pool.fetchval = AsyncMock(return_value=1)
        app.state.pool = pool

        resp = admin_client.patch(
            f"/v1/workflow/webhooks/{WEBHOOK_ID}",
            json={"active": False},
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )
        assert resp.status_code == 200
        assert resp.json()["active"] is False

    def test_rotate_token_returns_new_plain_token(self, admin_client):
        """POST /v1/workflow/webhooks/{id}/rotate → response includes fresh plain token."""
        pool = MagicMock()
        pool.fetchrow = AsyncMock(side_effect=[fake_webhook(), fake_webhook()])
        pool.fetchval = AsyncMock(return_value=1)
        app.state.pool = pool

        resp = admin_client.post(
            f"/v1/workflow/webhooks/{WEBHOOK_ID}/rotate",
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "token" in data
        assert data["token"].startswith("plughub_wh_")

    def test_delete_webhook_returns_204(self, admin_client):
        """DELETE /v1/workflow/webhooks/{id} → 204 No Content."""
        pool = MagicMock()
        # db_delete_webhook calls pool.execute() and checks result == "DELETE 1"
        pool.execute  = AsyncMock(return_value="DELETE 1")
        pool.fetchrow = AsyncMock(return_value=None)
        pool.fetchval = AsyncMock(return_value=1)
        app.state.pool = pool

        resp = admin_client.delete(
            f"/v1/workflow/webhooks/{WEBHOOK_ID}",
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )
        assert resp.status_code == 204

    def test_delete_webhook_not_found_returns_404(self, admin_client):
        """DELETE /v1/workflow/webhooks/{id} for unknown id → 404."""
        pool = MagicMock()
        # "DELETE 0" means no row was matched → router raises 404
        pool.execute  = AsyncMock(return_value="DELETE 0")
        pool.fetchrow = AsyncMock(return_value=None)
        app.state.pool = pool

        resp = admin_client.delete(
            f"/v1/workflow/webhooks/{WEBHOOK_ID}",
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )
        assert resp.status_code == 404


# ─────────────────────────────────────────────
# TestWebhookTrigger
# ─────────────────────────────────────────────

class TestWebhookTrigger:
    """Public trigger endpoint: POST /v1/workflow/webhook/{id}."""

    def _pool_for_valid_trigger(self):
        """
        Pool that returns a valid active webhook on token-hash lookup, then
        an instance row on db_create_instance, then None on db_record_delivery.
        """
        webhook_row  = fake_webhook()   # active=True
        instance_row = fake_row()       # new WorkflowInstance

        pool = MagicMock()
        # Calls in order: db_get_webhook_by_token_hash → db_create_instance → db_record_delivery
        pool.fetchrow  = AsyncMock(side_effect=[webhook_row, instance_row, fake_delivery()])
        pool.execute   = AsyncMock(return_value="UPDATE 1")
        pool.fetchval  = AsyncMock(return_value=1)
        return pool

    def test_valid_token_returns_202_and_instance(self, client):
        """Correct X-Webhook-Token → 202 with instance_id."""
        app.state.pool = self._pool_for_valid_trigger()

        with patch("plughub_workflow_api.webhooks.verify_token", return_value=True):
            resp = client.post(
                f"/v1/workflow/webhook/{WEBHOOK_ID}",
                json={"customer_id": "cust-123"},
                headers={"X-Webhook-Token": PLAIN_TOKEN},
            )

        assert resp.status_code == 202
        data = resp.json()
        assert data["status"]  == "accepted"
        assert "instance_id"   in data
        assert data["flow_id"] == "wf_approval_v1"

    def test_missing_token_returns_401(self, client):
        """No X-Webhook-Token header → 401."""
        app.state.pool = make_pool()
        resp = client.post(f"/v1/workflow/webhook/{WEBHOOK_ID}", json={})
        assert resp.status_code == 401

    def test_invalid_token_returns_401(self, client):
        """X-Webhook-Token not found in DB → 401."""
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=None)   # token hash not found
        app.state.pool = pool

        resp = client.post(
            f"/v1/workflow/webhook/{WEBHOOK_ID}",
            json={},
            headers={"X-Webhook-Token": "plughub_wh_invalid"},
        )
        assert resp.status_code == 401

    def test_inactive_webhook_returns_403_and_logs_delivery(self, client):
        """Active=False webhook → 403; delivery record is written with status_code=403."""
        inactive_wh = fake_webhook({"active": False})
        delivery_row = fake_delivery({"status_code": 403})

        pool = MagicMock()
        # db_get_webhook_by_token_hash → inactive webhook
        # db_record_delivery (403 log) → delivery row (execute is called for trigger_count update)
        pool.fetchrow  = AsyncMock(side_effect=[inactive_wh, delivery_row])
        pool.execute   = AsyncMock(return_value="UPDATE 1")
        app.state.pool = pool

        with patch("plughub_workflow_api.webhooks.verify_token", return_value=True):
            resp = client.post(
                f"/v1/workflow/webhook/{WEBHOOK_ID}",
                json={},
                headers={"X-Webhook-Token": PLAIN_TOKEN},
            )

        assert resp.status_code == 403
        # The delivery INSERT must have been called
        pool.fetchrow.assert_awaited()

    def test_body_merged_with_context_override(self, client):
        """
        Webhook context_override={'env': 'prod'} + body {'customer_id': 'c1'}
        → pipeline_state.contact_context should contain both keys.
        The db_create_instance call receives the merged dict.
        """
        import json as _json

        wh_with_override = fake_webhook({"context_override": '{"env": "prod"}'})
        instance_row     = fake_row()
        delivery_row     = fake_delivery()

        captured_payload: dict = {}

        async def capture_create(pool_arg, payload):
            captured_payload.update(payload)
            return {
                "id":             str(uuid4()),
                "tenant_id":      "tenant-test",
                "flow_id":        "wf_approval_v1",
                "installation_id": "inst-001",
                "organization_id": "org-001",
                "session_id":     None,
                "origin_session_id": None,
                "pool_id":        None,
                "status":         "active",
                "current_step":   None,
                "pipeline_state": _json.dumps(payload.get("pipeline_state", {})),
                "suspend_reason": None,
                "resume_token":   None,
                "resume_expires_at": None,
                "suspended_at":   None,
                "resumed_at":     None,
                "completed_at":   None,
                "created_at":     datetime.now(timezone.utc).isoformat(),
                "metadata":       _json.dumps({}),
            }

        pool = MagicMock()
        pool.fetchrow = AsyncMock(side_effect=[wh_with_override, delivery_row])
        pool.execute  = AsyncMock(return_value="UPDATE 1")  # for trigger_count increment (2xx)
        app.state.pool = pool

        with patch("plughub_workflow_api.webhooks.verify_token", return_value=True), \
             patch("plughub_workflow_api.router.db_create_instance",
                   new=AsyncMock(side_effect=capture_create)):
            resp = client.post(
                f"/v1/workflow/webhook/{WEBHOOK_ID}",
                json={"customer_id": "c1"},
                headers={"X-Webhook-Token": PLAIN_TOKEN},
            )

        assert resp.status_code == 202
        ctx = captured_payload["pipeline_state"]["contact_context"]
        assert ctx.get("env")         == "prod"    # from context_override
        assert ctx.get("customer_id") == "c1"      # from inbound body


# ─────────────────────────────────────────────
# TestWebhookDeliveries
# ─────────────────────────────────────────────

class TestWebhookDeliveries:
    """Delivery log endpoint: GET /v1/workflow/webhooks/{id}/deliveries."""

    def test_returns_delivery_records(self, admin_client):
        """GET /deliveries → list of delivery dicts."""
        pool = MagicMock()
        # fetchrow ×1 for db_get_webhook, then fetch for db_list_deliveries
        pool.fetchrow = AsyncMock(return_value=fake_webhook())
        pool.fetch    = AsyncMock(return_value=[fake_delivery(), fake_delivery()])
        app.state.pool = pool

        resp = admin_client.get(
            f"/v1/workflow/webhooks/{WEBHOOK_ID}/deliveries",
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )
        assert resp.status_code == 200
        records = resp.json()
        assert len(records) == 2
        assert "status_code" in records[0]
        assert "latency_ms"  in records[0]

    def test_returns_empty_list_when_no_deliveries(self, admin_client):
        """No deliveries yet → empty list."""
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=fake_webhook())
        pool.fetch    = AsyncMock(return_value=[])
        app.state.pool = pool

        resp = admin_client.get(
            f"/v1/workflow/webhooks/{WEBHOOK_ID}/deliveries",
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_returns_404_for_unknown_webhook(self, admin_client):
        """Webhook not found → 404 before listing deliveries."""
        app.state.pool = make_pool(fetchrow_result=None)
        resp = admin_client.get(
            f"/v1/workflow/webhooks/{WEBHOOK_ID}/deliveries",
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )
        assert resp.status_code == 404

    def test_limit_capped_at_200(self, admin_client):
        """limit=500 → silently capped at 200; request succeeds."""
        pool = MagicMock()
        pool.fetchrow = AsyncMock(return_value=fake_webhook())
        pool.fetch    = AsyncMock(return_value=[])
        app.state.pool = pool

        resp = admin_client.get(
            f"/v1/workflow/webhooks/{WEBHOOK_ID}/deliveries?limit=500",
            headers={"X-Admin-Token": ADMIN_TOKEN},
        )
        assert resp.status_code == 200
