"""
test_router.py
Unit tests for the Workflow API router — Arc 19 Fase D.

Strategy:
  - asyncpg pool is replaced with a MagicMock whose fetchrow/fetch/execute
    methods return controlled fake data
  - kafka producer is None (disabled)
  - httpx.AsyncClient is patched to mock channel-gateway calls
  - calendar_client.calculate_deadline is patched to avoid HTTP calls

Arc 19 Fase D behaviour summary:
  POST /v1/workflow/trigger         → proxies to channel-gateway → 201
  POST /.../persist-suspend         → 410 Gone (deprecated)
  POST /v1/workflow/resume
      with tenant_id                → proxies to channel-gateway → 200
      without tenant_id             → legacy PostgreSQL path → 200
  POST /.../complete                → 410 Gone
  POST /.../fail                    → 410 Gone
  POST /.../cancel                  → 404 (rota REMOVIDA em 2026-08-07, I5 lacuna 4b)
  /v1/workflow/webhook*             → 404 (8 rotas REMOVIDAS em 2026-09-08, MOD-11)
  POST /.../collect/persist         → 410 Gone
  POST /v1/workflow/collect/respond → 410 Gone
  GET  /v1/workflow/instances       → read-only, kept as-is
  GET  /v1/workflow/instances/{id}  → read-only, kept as-is
  GET  /v1/health                   → kept as-is

Coverage:
  TestTrigger           — POST /v1/workflow/trigger proxies to channel-gateway
  TestPersistSuspend    — returns 410
  TestResume            — proxy path (tenant_id) + legacy path (no tenant_id)
  TestDeprecated        — complete, fail, cancel, collect/persist, collect/respond → 410
  TestList              — GET  /v1/workflow/instances (unchanged)
  TestDetail            — GET  /v1/workflow/instances/{id} (unchanged)
  TestHealth            — GET  /v1/health (unchanged)
  TestTimeoutScanner    — timeout_job._scan_once (unchanged)
  TestWebhookRoutesRemoved  — MOD-11: as 8 rotas de webhook sairam (404), com
                              controle positivo de que o app segue servindo
  TestAdminGateFailsClosed  — MOD-11: `_require_admin` deixou de falhar aberto
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
        channel_gateway_url="http://channel-gateway:8010",
    )


@pytest.fixture
def client():
    """Sync test client with mocked state."""
    app.state.pool     = make_pool()
    app.state.settings = make_settings()
    app.state.producer = None
    return TestClient(app)


# ─────────────────────────────────────────────
# TestTrigger — Arc 19 Fase D: proxy to channel-gateway
# ─────────────────────────────────────────────

class TestTrigger:
    def _mock_gw_ok(self):
        """Returns a mock httpx response for a successful channel-gateway call."""
        mock_resp = MagicMock()
        mock_resp.status_code = 201
        mock_resp.json.return_value = {"session_id": "sess-abc-123"}
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    def test_proxies_to_channel_gateway_and_returns_201(self, client):
        mock_resp = self._mock_gw_ok()
        with patch("plughub_workflow_api.router.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_client

            resp = client.post("/v1/workflow/trigger", json={
                "tenant_id":    "tenant-test",
                "flow_id":      "skill_portabilidade_v1",
                "trigger_type": "manual",
                "metadata":     {"invoice_id": "INV-001"},
            })

        assert resp.status_code == 201
        data = resp.json()
        assert data["session_id"] == "sess-abc-123"
        # Verify proxy was called with correct URL and body
        call_kwargs = mock_client.post.call_args
        assert "skill_portabilidade_v1" in call_kwargs[0][0]
        sent_body = call_kwargs[1]["json"]
        assert sent_body["tenant_id"] == "tenant-test"
        assert sent_body["trigger_type"] == "api"   # "manual" is normalized to "api"

    def test_trigger_type_manual_normalized_to_api(self, client):
        mock_resp = self._mock_gw_ok()
        with patch("plughub_workflow_api.router.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_client

            resp = client.post("/v1/workflow/trigger", json={
                "tenant_id": "t1", "flow_id": "skill_test_v1",
                "trigger_type": "manual",
            })

        assert resp.status_code == 201
        body = mock_client.post.call_args[1]["json"]
        assert body["trigger_type"] == "api"

    def test_non_manual_trigger_type_preserved(self, client):
        mock_resp = self._mock_gw_ok()
        with patch("plughub_workflow_api.router.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_client

            resp = client.post("/v1/workflow/trigger", json={
                "tenant_id": "t1", "flow_id": "skill_test_v1",
                "trigger_type": "scheduled",
            })

        assert resp.status_code == 201
        body = mock_client.post.call_args[1]["json"]
        assert body["trigger_type"] == "scheduled"

    def test_metadata_fields_forwarded(self, client):
        mock_resp = self._mock_gw_ok()
        with patch("plughub_workflow_api.router.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_client

            resp = client.post("/v1/workflow/trigger", json={
                "tenant_id":        "t1",
                "flow_id":          "skill_test_v1",
                "origin_session_id": "orig-sess",
                "pool_id":           "retencao_webhook",
                "context":           {"key": "val"},
                "session_id":        "cust-sess",
            })

        assert resp.status_code == 201
        body = mock_client.post.call_args[1]["json"]
        assert body["customer_id"]          == "cust-sess"
        meta = body["metadata"] or {}
        assert meta.get("origin_session_id") == "orig-sess"
        assert meta.get("pool_id")           == "retencao_webhook"
        assert meta.get("context")           == {"key": "val"}

    def test_missing_required_fields_returns_422(self, client):
        resp = client.post("/v1/workflow/trigger", json={"tenant_id": "t1"})
        assert resp.status_code == 422

    def test_gateway_error_returns_502(self, client):
        import httpx
        with patch("plughub_workflow_api.router.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(side_effect=Exception("connection refused"))
            mock_cls.return_value = mock_client

            resp = client.post("/v1/workflow/trigger", json={
                "tenant_id": "t1", "flow_id": "skill_test_v1",
            })

        assert resp.status_code == 502


# ─────────────────────────────────────────────
# TestPersistSuspend — Arc 19 Fase D: 410 Gone
# ─────────────────────────────────────────────

class TestPersistSuspend:
    def test_returns_410_always(self, client):
        resp = client.post(
            f"/v1/workflow/instances/{INSTANCE_ID}/persist-suspend",
            json={
                "step_id": "s", "resume_token": "tok", "reason": "approval",
                "timeout_hours": 24, "business_hours": False,
            },
        )
        assert resp.status_code == 410

    def test_returns_410_even_when_valid_payload(self, client):
        # body does not matter — endpoint always 410 now
        resp = client.post(
            f"/v1/workflow/instances/{INSTANCE_ID}/persist-suspend",
            json={
                "step_id": "s", "resume_token": "tok", "reason": "approval",
                "timeout_hours": 24, "business_hours": False,
            },
        )
        assert resp.status_code == 410


# ─────────────────────────────────────────────
# TestResume — Arc 19 Fase D: dual-path
# ─────────────────────────────────────────────

class TestResume:
    """
    With tenant_id → proxy to channel-gateway WebhookAdapter (Arc 19).
    Without tenant_id → legacy PostgreSQL path (backward compat).
    """

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

    def _mock_gw_ok(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"session_id": "sess-resumed"}
        mock_resp.raise_for_status = MagicMock()
        return mock_resp

    # ── Arc 19 path (with tenant_id) ──────────────────────────────────────────

    def test_webhook_resume_proxies_to_channel_gateway(self, client):
        mock_resp = self._mock_gw_ok()
        with patch("plughub_workflow_api.router.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(return_value=mock_resp)
            mock_cls.return_value = mock_client

            resp = client.post("/v1/workflow/resume", json={
                "token":     RESUME_TOKEN,
                "decision":  "approved",
                "payload":   {"approved_by": "maria"},
                "tenant_id": "tenant-test",
            })

        assert resp.status_code == 200
        assert resp.json()["session_id"] == "sess-resumed"
        call_url = mock_client.post.call_args[0][0]
        assert f"resume/{RESUME_TOKEN}" in call_url
        body = mock_client.post.call_args[1]["json"]
        assert body["tenant_id"] == "tenant-test"
        assert body["payload"]["decision"] == "approved"

    def test_webhook_resume_gateway_error_returns_502(self, client):
        with patch("plughub_workflow_api.router.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client.post = AsyncMock(side_effect=Exception("gateway down"))
            mock_cls.return_value = mock_client

            resp = client.post("/v1/workflow/resume", json={
                "token":     RESUME_TOKEN,
                "decision":  "approved",
                "tenant_id": "t1",
            })

        assert resp.status_code == 502

    # ── Legacy path (without tenant_id) ──────────────────────────────────────

    def test_legacy_approves_and_returns_200(self, client):
        suspended  = self._suspended_row()
        active_row = fake_row({"status": "active", "resume_token": None})
        app.state.pool = MagicMock()
        app.state.pool.fetchrow = AsyncMock(side_effect=[suspended, active_row])
        app.state.pool.fetchval = AsyncMock(return_value=1)

        resp = client.post("/v1/workflow/resume", json={
            "token":    RESUME_TOKEN,
            "decision": "approved",
            "payload":  {"approved_by": "maria"},
            # no tenant_id → legacy path
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["decision"] == "approved"
        assert "wait_duration_ms" in data

    def test_legacy_token_not_found_returns_404(self, client):
        app.state.pool = make_pool(fetchrow_result=None)
        resp = client.post("/v1/workflow/resume", json={
            "token": "nonexistent", "decision": "approved",
        })
        assert resp.status_code == 404

    def test_legacy_not_suspended_returns_409(self, client):
        app.state.pool = make_pool(fetchrow_result=fake_row({"status": "active", "resume_token": RESUME_TOKEN}))
        resp = client.post("/v1/workflow/resume", json={
            "token": RESUME_TOKEN, "decision": "approved",
        })
        assert resp.status_code == 409

    def test_legacy_expired_token_returns_410(self, client):
        app.state.pool = make_pool(fetchrow_result=self._suspended_row(future_expiry=False))
        resp = client.post("/v1/workflow/resume", json={
            "token": RESUME_TOKEN, "decision": "approved",
        })
        assert resp.status_code == 410

    def test_legacy_timeout_decision_skips_expiry_check(self, client):
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
# TestDeprecated — Arc 19 Fase D: all 410 Gone
# ─────────────────────────────────────────────

class TestDeprecated:
    """
    All lifecycle mutating endpoints that were replaced by channel-gateway
    session management return 410 Gone.
    """

    def test_complete_returns_410(self, client):
        resp = client.post(
            f"/v1/workflow/instances/{INSTANCE_ID}/complete",
            json={"outcome": "resolved"},
        )
        assert resp.status_code == 410

    def test_fail_returns_410(self, client):
        resp = client.post(
            f"/v1/workflow/instances/{INSTANCE_ID}/fail",
            json={"error": "some error"},
        )
        assert resp.status_code == 410

    def test_cancel_route_is_gone(self, client):
        """
        A rota /cancel foi REMOVIDA em 2026-08-07 (I5, lacuna 4b) — não é mais
        410, é 404 de rota inexistente.

        A asserção é 404 e NÃO `!= 410` de propósito: `!= 410` passaria também
        se alguém reintroduzisse a rota com outro código, que é exatamente a
        regressão que este teste existe para pegar. O que se afirma é que o
        caminho não está registrado no router.
        """
        resp = client.post(
            f"/v1/workflow/instances/{INSTANCE_ID}/cancel",
            json={"cancelled_by": "operator"},
        )
        assert resp.status_code == 404, (
            f"esperado 404 (rota removida), veio {resp.status_code}. "
            "Se a rota voltou, ler o motivo da remoção em router.py § Cancel."
        )

    def test_collect_persist_returns_410(self, client):
        resp = client.post(
            f"/v1/workflow/instances/{INSTANCE_ID}/collect/persist",
            json={
                "step_id": "s", "collect_token": "tok",
                "target": {"type": "customer", "id": "c1"},
                "interaction": "text", "prompt": "p",
            },
        )
        assert resp.status_code == 410

    def test_collect_respond_returns_410(self, client):
        resp = client.post(
            "/v1/workflow/collect/respond",
            json={"collect_token": "tok", "response_data": {}},
        )
        assert resp.status_code == 410


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
# TestWebhookRoutesRemoved — MOD-11: o registro de webhook mudou de casa
# ─────────────────────────────────────────────
#
# Substituem `TestWebhookCRUD`, `TestWebhookTrigger` e `TestWebhookDeliveries`,
# que exercitavam as 8 rotas removidas em 2026-09-08. Foram SUBSTITUIDAS e nao
# apagadas: apagar deixaria a remocao sem testemunha, e a proxima pessoa que
# "restaurasse" o CRUD nao encontraria nada vermelho.
#
# O registro unico de endereco de webhook e o `ChannelEndpoint` do agent-registry
# (`adr-webhook-endpoint-single-registry`), editado em `/config/channels`.

class TestWebhookRoutesRemoved:
    """As 8 rotas de webhook nao existem mais NESTE servico."""

    ROTAS_MORTAS = [
        ("post",   "/v1/workflow/webhooks"),
        ("get",    "/v1/workflow/webhooks?tenant_id=tenant-test"),
        ("get",    "/v1/workflow/webhooks/wh-1"),
        ("patch",  "/v1/workflow/webhooks/wh-1"),
        ("post",   "/v1/workflow/webhooks/wh-1/rotate"),
        ("delete", "/v1/workflow/webhooks/wh-1"),
        ("get",    "/v1/workflow/webhooks/wh-1/deliveries"),
        ("post",   "/v1/workflow/webhook/wh-1"),
    ]

    def test_as_oito_rotas_respondem_404(self, client):
        """
        404 = a rota nao esta REGISTRADA. Mandamos o header de admin de proposito:
        se alguma voltasse apenas GATEADA, ela responderia 401/403/200 e nao 404 —
        e o teste distingue "removida" de "fechada", que sao fatos diferentes.
        """
        for metodo, url in self.ROTAS_MORTAS:
            r = getattr(client, metodo)(url, headers={"X-Admin-Token": "qualquer"})
            assert r.status_code == 404, f"{metodo.upper()} {url} respondeu {r.status_code}"

    def test_controle_POSITIVO_o_app_continua_servindo(self, client):
        """
        Sem este controle, um app que falhasse ao carregar daria 404 em TUDO e o
        teste acima passaria pelo motivo errado — o modo de falha que a § Postura
        chama de "teste que nao pode reprovar".
        """
        r = client.get("/v1/workflow/instances?tenant_id=tenant-test")
        assert r.status_code == 200, "a rota de leitura que SOBREVIVE parou de responder"


# ─────────────────────────────────────────────
# TestAdminGateFailsClosed — MOD-11: o portao administrativo deixou de falhar aberto
# ─────────────────────────────────────────────

class TestAdminGateFailsClosed:
    """
    `_require_admin` era `if settings.admin_token and x_admin_token != ...`, e o
    `and` fazia do segredo AUSENTE um no-op. Medido no container: nenhuma env de
    admin configurada, logo o portao nunca recusou ninguem.
    """

    def _client(self, admin_token: str):
        app.state.pool     = make_pool()
        app.state.settings = make_settings().model_copy(update={"admin_token": admin_token})
        app.state.producer = None
        return TestClient(app, raise_server_exceptions=False)

    def test_sem_segredo_configurado_a_rota_fica_INDISPONIVEL(self):
        """Era 200 para qualquer um. Agora 503 — falha de config, nao veredicto."""
        r = self._client("").post("/admin/backfill-events?tenant_id=tenant-test")
        assert r.status_code == 503, f"o portao voltou a falhar aberto: {r.status_code}"

    def test_com_segredo_configurado_token_errado_e_401(self):
        r = self._client("segredo-certo").post(
            "/admin/backfill-events?tenant_id=tenant-test",
            headers={"X-Admin-Token": "token-errado"},
        )
        assert r.status_code == 401

    def test_controle_POSITIVO_o_token_certo_ATRAVESSA(self):
        """
        Sem este caso, um portao que recusasse TODO MUNDO passaria nos dois acima —
        o negativo sozinho passa pelo motivo errado (§ Security, 2026-08-27).
        """
        r = self._client("segredo-certo").post(
            "/admin/backfill-events?tenant_id=tenant-test",
            headers={"X-Admin-Token": "segredo-certo"},
        )
        assert r.status_code not in (401, 503), f"o token correto foi barrado: {r.status_code}"
