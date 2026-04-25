"""
test_router.py
Integration tests for the Pricing API router.
Uses httpx.AsyncClient + FastAPI TestClient with a mocked DB pool.
"""
from __future__ import annotations

import pytest
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from httpx import AsyncClient, ASGITransport

from plughub_pricing_api.main import app
from plughub_pricing_api.config import Settings


# ── Fixtures ───────────────────────────────────────────────────────────────────

MOCK_RESOURCE = {
    "id":              "res-uuid-1",
    "tenant_id":       "t1",
    "installation_id": "default",
    "resource_type":   "ai_agent",
    "quantity":        5,
    "pool_type":       "base",
    "reserve_pool_id": None,
    "active":          True,
    "billing_unit":    "monthly",
    "label":           "AI Agent",
    "created_at":      "2026-01-01T00:00:00",
    "updated_at":      "2026-01-01T00:00:00",
}

MOCK_LOG = {
    "id":                "log-uuid-1",
    "tenant_id":         "t1",
    "reserve_pool_id":   "peak_pool",
    "activation_date":   "2026-01-10",
    "deactivation_date": None,
    "activated_by":      "operator",
    "created_at":        "2026-01-10T09:00:00",
}


def make_mock_pool():
    pool = AsyncMock()
    pool.fetchval = AsyncMock(return_value=1)
    return pool


@pytest.fixture(autouse=True)
def mock_app_pool():
    pool = make_mock_pool()
    app.state.pg_pool = pool
    yield pool


@pytest.fixture
def client():
    return TestClient(app)


# ── TestHealth ─────────────────────────────────────────────────────────────────

class TestHealth:
    def test_health_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


# ── TestGetInvoice ─────────────────────────────────────────────────────────────

class TestGetInvoice:
    @patch("plughub_pricing_api.calculator.pricing_db.list_resources", new_callable=AsyncMock)
    @patch("plughub_pricing_api.router.load_price_table", new_callable=AsyncMock)
    def test_invoice_json(self, mock_prices, mock_resources, client):
        mock_prices.return_value   = {}
        mock_resources.return_value = [MOCK_RESOURCE]

        r = client.get("/v1/pricing/invoice/t1?cycle_start=2026-01-01&cycle_end=2026-01-31")
        assert r.status_code == 200
        data = r.json()
        assert data["tenant_id"]   == "t1"
        assert "grand_total"        in data
        assert "base_items"         in data
        assert "reserve_groups"     in data

    @patch("plughub_pricing_api.calculator.pricing_db.list_resources", new_callable=AsyncMock)
    @patch("plughub_pricing_api.router.load_price_table", new_callable=AsyncMock)
    def test_invoice_xlsx(self, mock_prices, mock_resources, client):
        mock_prices.return_value    = {}
        mock_resources.return_value = []

        r = client.get("/v1/pricing/invoice/t1?format=xlsx")
        assert r.status_code == 200
        assert "spreadsheetml" in r.headers["content-type"]
        assert r.content[:2] == b"PK"  # ZIP magic bytes

    @patch("plughub_pricing_api.calculator.pricing_db.list_resources", new_callable=AsyncMock)
    @patch("plughub_pricing_api.router.load_price_table", new_callable=AsyncMock)
    def test_invoice_default_cycle(self, mock_prices, mock_resources, client):
        mock_prices.return_value    = {}
        mock_resources.return_value = []

        r = client.get("/v1/pricing/invoice/t1")
        assert r.status_code == 200
        data = r.json()
        # cycle_start should be first day of current month
        today = date.today()
        assert data["cycle_start"] == today.replace(day=1).isoformat()

    @patch("plughub_pricing_api.calculator.pricing_db.list_resources", new_callable=AsyncMock)
    @patch("plughub_pricing_api.router.load_price_table", new_callable=AsyncMock)
    def test_invoice_with_base_items(self, mock_prices, mock_resources, client):
        mock_prices.return_value    = {"unit_prices": {"ai_agent": 100.0}, "currency": "BRL"}
        mock_resources.return_value = [MOCK_RESOURCE]

        r = client.get("/v1/pricing/invoice/t1?cycle_start=2026-01-01&cycle_end=2026-01-31")
        data = r.json()
        assert len(data["base_items"]) == 1
        assert data["base_items"][0]["resource_type"] == "ai_agent"
        assert data["base_items"][0]["quantity"] == 5
        assert data["base_total"] == pytest.approx(500.0)


# ── TestResources ──────────────────────────────────────────────────────────────

class TestResources:
    @patch("plughub_pricing_api.router.pricing_db.list_resources", new_callable=AsyncMock)
    def test_list_resources(self, mock_list, client):
        mock_list.return_value = [MOCK_RESOURCE]
        r = client.get("/v1/pricing/resources/t1")
        assert r.status_code == 200
        data = r.json()
        assert data["tenant_id"] == "t1"
        assert len(data["resources"]) == 1

    @patch("plughub_pricing_api.router.pricing_db.upsert_resource", new_callable=AsyncMock)
    def test_upsert_resource_requires_admin(self, mock_upsert, client):
        mock_upsert.return_value = MOCK_RESOURCE
        # No admin token — should succeed when admin_token is empty (default)
        r = client.post("/v1/pricing/resources/t1", json={
            "resource_type": "ai_agent",
            "quantity": 5,
        })
        assert r.status_code == 200

    @patch("plughub_pricing_api.router.pricing_db.upsert_resource", new_callable=AsyncMock)
    def test_upsert_resource_blocked_wrong_token(self, mock_upsert, client):
        mock_upsert.return_value = MOCK_RESOURCE

        # Use FastAPI dependency_overrides to inject a Settings with admin_token set
        from plughub_pricing_api.router import get_settings as router_get_settings

        def override_settings():
            return Settings(admin_token="secret123")

        app.dependency_overrides[router_get_settings] = override_settings
        try:
            r = client.post(
                "/v1/pricing/resources/t1",
                json={"resource_type": "ai_agent", "quantity": 5},
                headers={"X-Admin-Token": "wrong"},
            )
            assert r.status_code == 403
        finally:
            app.dependency_overrides.pop(router_get_settings, None)

    @patch("plughub_pricing_api.router.pricing_db.delete_resource", new_callable=AsyncMock)
    def test_delete_resource_not_found(self, mock_delete, client):
        mock_delete.return_value = False
        r = client.delete("/v1/pricing/resources/t1/nonexistent-id")
        assert r.status_code == 404

    @patch("plughub_pricing_api.router.pricing_db.delete_resource", new_callable=AsyncMock)
    def test_delete_resource_ok(self, mock_delete, client):
        mock_delete.return_value = True
        r = client.delete("/v1/pricing/resources/t1/res-uuid-1")
        assert r.status_code == 200
        assert r.json()["deleted"] is True


# ── TestReserveActivation ──────────────────────────────────────────────────────

class TestReserveActivation:
    @patch("plughub_pricing_api.router.pricing_db.set_reserve_active", new_callable=AsyncMock)
    @patch("plughub_pricing_api.router.pricing_db.record_activation", new_callable=AsyncMock)
    def test_activate_ok(self, mock_record, mock_set, client):
        mock_set.return_value    = 2   # 2 resources updated
        mock_record.return_value = MOCK_LOG

        r = client.post("/v1/pricing/reserve/t1/peak_pool/activate")
        assert r.status_code == 200
        data = r.json()
        assert data["activated"] is True
        assert data["resources_updated"] == 2

    @patch("plughub_pricing_api.router.pricing_db.set_reserve_active", new_callable=AsyncMock)
    @patch("plughub_pricing_api.router.pricing_db.record_activation", new_callable=AsyncMock)
    def test_activate_pool_not_found(self, mock_record, mock_set, client):
        mock_set.return_value = 0   # pool not found
        r = client.post("/v1/pricing/reserve/t1/unknown_pool/activate")
        assert r.status_code == 404

    @patch("plughub_pricing_api.router.pricing_db.set_reserve_active", new_callable=AsyncMock)
    @patch("plughub_pricing_api.router.pricing_db.record_deactivation", new_callable=AsyncMock)
    def test_deactivate_ok(self, mock_deact, mock_set, client):
        mock_set.return_value   = 2
        mock_deact.return_value = True

        r = client.post("/v1/pricing/reserve/t1/peak_pool/deactivate")
        assert r.status_code == 200
        assert r.json()["deactivated"] is True

    @patch("plughub_pricing_api.router.pricing_db.set_reserve_active", new_callable=AsyncMock)
    @patch("plughub_pricing_api.router.pricing_db.record_deactivation", new_callable=AsyncMock)
    def test_deactivate_pool_not_found(self, mock_deact, mock_set, client):
        mock_set.return_value = 0
        r = client.post("/v1/pricing/reserve/t1/unknown/deactivate")
        assert r.status_code == 404


# ── TestActivationLog ─────────────────────────────────────────────────────────

class TestActivationLog:
    @patch("plughub_pricing_api.router.pricing_db.list_activation_log", new_callable=AsyncMock)
    def test_get_all_logs(self, mock_log, client):
        mock_log.return_value = [MOCK_LOG]
        r = client.get("/v1/pricing/reserve/t1/activity")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] == 1
        assert data["logs"][0]["reserve_pool_id"] == "peak_pool"

    @patch("plughub_pricing_api.router.pricing_db.list_activation_log", new_callable=AsyncMock)
    def test_get_logs_filtered_by_pool(self, mock_log, client):
        mock_log.return_value = [MOCK_LOG]
        r = client.get("/v1/pricing/reserve/t1/activity?reserve_pool_id=peak_pool")
        assert r.status_code == 200
        # Verify the pool_id filter was passed
        mock_log.assert_called_once()
        call_args = mock_log.call_args
        assert call_args.args[2] == "peak_pool"
