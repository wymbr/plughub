"""
test_router.py
Tests for the Calendar API router — tenant config and calendar timezone inheritance.
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from plughub_calendar_api.config import Settings
from plughub_calendar_api.main import app


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_settings(**kwargs) -> Settings:
    defaults = {
        "installation_id": "inst-test",
        "organization_id": "org-test",
        "database_url": "postgresql://localhost/test",
        "default_timezone": "America/Sao_Paulo",
    }
    defaults.update(kwargs)
    return Settings(**defaults)


def _make_pool() -> MagicMock:
    """Return a MagicMock that mimics asyncpg.Pool async context manager behaviour."""
    pool = MagicMock()
    return pool


def _fake_tenant_config_row(tenant_id: str, default_timezone: str) -> MagicMock:
    updated_at = datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
    data = {
        "tenant_id": tenant_id,
        "default_timezone": default_timezone,
        "updated_at": updated_at,
    }
    row = MagicMock()
    row.__getitem__ = lambda self, key: data[key]
    return row


def _fake_calendar_row() -> MagicMock:
    """Minimal calendar row for create_calendar tests."""
    data = {
        "id": "11111111-1111-1111-1111-111111111111",
        "installation_id": "inst-test",
        "organization_id": "org-test",
        "tenant_id": "tenant-abc",
        "scope": "tenant",
        "name": "Test Calendar",
        "description": "",
        "timezone": "America/New_York",
        "weekly_schedule": "[]",
        "holiday_set_ids": "[]",
        "exceptions": "[]",
        "created_at": datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
        "updated_at": datetime(2025, 1, 15, 12, 0, 0, tzinfo=timezone.utc),
    }
    row = MagicMock()
    row.__getitem__ = lambda self, key: data[key]
    return row


# ── GET /v1/tenant-config ─────────────────────────────────────────────────────

class TestGetTenantConfig:
    def setup_method(self):
        self.pool = _make_pool()
        self.settings = _make_settings()
        app.state.pool = self.pool
        app.state.settings = self.settings
        self.client = TestClient(app, raise_server_exceptions=True)

    def test_returns_default_when_no_config_row(self):
        """When tenant has no explicit config, returns platform default (America/Sao_Paulo)."""
        self.pool.fetchrow = AsyncMock(return_value=None)

        resp = self.client.get("/v1/tenant-config", params={"tenant_id": "tenant-xyz"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["tenant_id"] == "tenant-xyz"
        assert body["default_timezone"] == "America/Sao_Paulo"
        assert body["updated_at"] is None

    def test_returns_existing_config(self):
        """When tenant has an explicit config row, returns that timezone."""
        row = _fake_tenant_config_row("tenant-abc", "America/New_York")
        self.pool.fetchrow = AsyncMock(return_value=row)

        resp = self.client.get("/v1/tenant-config", params={"tenant_id": "tenant-abc"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["tenant_id"] == "tenant-abc"
        assert body["default_timezone"] == "America/New_York"
        assert body["updated_at"] is not None

    def test_requires_tenant_id(self):
        """GET /v1/tenant-config without tenant_id returns 422."""
        resp = self.client.get("/v1/tenant-config")
        assert resp.status_code == 422

    def test_passes_tenant_id_to_db(self):
        """Verifies db_get_tenant_config is called with the correct tenant_id."""
        self.pool.fetchrow = AsyncMock(return_value=None)

        self.client.get("/v1/tenant-config", params={"tenant_id": "my-tenant"})

        call_args = self.pool.fetchrow.call_args
        assert "my-tenant" in call_args.args or "my-tenant" in call_args.kwargs.values()


# ── PATCH /v1/tenant-config ───────────────────────────────────────────────────

class TestUpdateTenantConfig:
    def setup_method(self):
        self.pool = _make_pool()
        self.settings = _make_settings()
        app.state.pool = self.pool
        app.state.settings = self.settings
        self.client = TestClient(app, raise_server_exceptions=True)

    def test_saves_valid_timezone(self):
        """PATCH with a valid IANA timezone should persist and return the config."""
        row = _fake_tenant_config_row("tenant-abc", "Europe/London")
        self.pool.fetchrow = AsyncMock(return_value=row)

        resp = self.client.patch(
            "/v1/tenant-config",
            json={"tenant_id": "tenant-abc", "default_timezone": "Europe/London"},
        )

        assert resp.status_code == 200
        body = resp.json()
        assert body["tenant_id"] == "tenant-abc"
        assert body["default_timezone"] == "Europe/London"

    def test_rejects_invalid_timezone(self):
        """PATCH with an unrecognised timezone string should return HTTP 422."""
        resp = self.client.patch(
            "/v1/tenant-config",
            json={"tenant_id": "tenant-abc", "default_timezone": "Not/AReal_Zone"},
        )

        assert resp.status_code == 422

    def test_rejects_garbage_timezone(self):
        """PATCH with completely bogus timezone string → 422."""
        resp = self.client.patch(
            "/v1/tenant-config",
            json={"tenant_id": "tenant-abc", "default_timezone": "Blarg/Blarg"},
        )

        assert resp.status_code == 422

    def test_accepts_utc(self):
        """UTC is a valid timezone."""
        row = _fake_tenant_config_row("tenant-abc", "UTC")
        self.pool.fetchrow = AsyncMock(return_value=row)

        resp = self.client.patch(
            "/v1/tenant-config",
            json={"tenant_id": "tenant-abc", "default_timezone": "UTC"},
        )

        assert resp.status_code == 200
        assert resp.json()["default_timezone"] == "UTC"

    def test_accepts_sao_paulo(self):
        """America/Sao_Paulo (the platform default) is accepted."""
        row = _fake_tenant_config_row("tenant-abc", "America/Sao_Paulo")
        self.pool.fetchrow = AsyncMock(return_value=row)

        resp = self.client.patch(
            "/v1/tenant-config",
            json={"tenant_id": "tenant-abc", "default_timezone": "America/Sao_Paulo"},
        )

        assert resp.status_code == 200

    def test_accepts_asia_tokyo(self):
        """Asia/Tokyo is a valid timezone."""
        row = _fake_tenant_config_row("tenant-jp", "Asia/Tokyo")
        self.pool.fetchrow = AsyncMock(return_value=row)

        resp = self.client.patch(
            "/v1/tenant-config",
            json={"tenant_id": "tenant-jp", "default_timezone": "Asia/Tokyo"},
        )

        assert resp.status_code == 200
        assert resp.json()["default_timezone"] == "Asia/Tokyo"

    def test_upsert_called_with_correct_args(self):
        """Verifies the upsert DB function is called with tenant_id and timezone."""
        row = _fake_tenant_config_row("tenant-abc", "America/Chicago")
        self.pool.fetchrow = AsyncMock(return_value=row)

        self.client.patch(
            "/v1/tenant-config",
            json={"tenant_id": "tenant-abc", "default_timezone": "America/Chicago"},
        )

        call_args = self.pool.fetchrow.call_args
        assert "tenant-abc" in call_args.args or "tenant-abc" in str(call_args)
        assert "America/Chicago" in call_args.args or "America/Chicago" in str(call_args)

    def test_requires_both_fields(self):
        """PATCH body without tenant_id or default_timezone → 422."""
        resp = self.client.patch("/v1/tenant-config", json={})
        assert resp.status_code == 422

    def test_does_not_call_db_for_invalid_timezone(self):
        """DB should not be called when timezone validation fails."""
        self.pool.fetchrow = AsyncMock(return_value=None)

        self.client.patch(
            "/v1/tenant-config",
            json={"tenant_id": "tenant-abc", "default_timezone": "Invalid/Zone"},
        )

        self.pool.fetchrow.assert_not_called()


# ── POST /v1/calendars — timezone inheritance ─────────────────────────────────

class TestCreateCalendarTimezoneInheritance:
    def setup_method(self):
        self.pool = _make_pool()
        self.settings = _make_settings()
        app.state.pool = self.pool
        app.state.settings = self.settings
        self.client = TestClient(app, raise_server_exceptions=True)

    def _calendar_payload(self, **kwargs):
        payload = {
            "organization_id": "org-test",
            "tenant_id": "tenant-abc",
            "name": "Test Calendar",
        }
        payload.update(kwargs)
        return payload

    def test_explicit_timezone_is_used_directly(self):
        """When caller provides a timezone, it is used without consulting tenant config."""
        cal_row = _fake_calendar_row()
        # Only one fetchrow call — for the INSERT
        self.pool.fetchrow = AsyncMock(return_value=cal_row)

        resp = self.client.post(
            "/v1/calendars",
            json=self._calendar_payload(timezone="Asia/Tokyo"),
        )

        assert resp.status_code == 201
        # fetchrow called exactly once (the INSERT) — tenant config NOT queried
        assert self.pool.fetchrow.call_count == 1

    def test_missing_timezone_inherits_tenant_default(self):
        """When no timezone is provided, the tenant's configured default is used."""
        tenant_row = _fake_tenant_config_row("tenant-abc", "America/New_York")
        cal_row = _fake_calendar_row()

        # First call: db_get_tenant_config SELECT; second: INSERT calendar
        self.pool.fetchrow = AsyncMock(side_effect=[tenant_row, cal_row])

        resp = self.client.post(
            "/v1/calendars",
            json=self._calendar_payload(),  # no timezone key
        )

        assert resp.status_code == 201
        # fetchrow called twice: once for tenant config, once for INSERT
        assert self.pool.fetchrow.call_count == 2

    def test_missing_timezone_falls_back_to_platform_default(self):
        """When no timezone provided and tenant has no config, uses platform default."""
        # First call: tenant config returns None (no row); second: INSERT calendar
        cal_row = _fake_calendar_row()
        self.pool.fetchrow = AsyncMock(side_effect=[None, cal_row])

        resp = self.client.post(
            "/v1/calendars",
            json=self._calendar_payload(),
        )

        assert resp.status_code == 201
        assert self.pool.fetchrow.call_count == 2

    def test_no_tenant_id_skips_config_lookup(self):
        """When no tenant_id is in the body, skip the tenant config lookup."""
        cal_row = _fake_calendar_row()
        self.pool.fetchrow = AsyncMock(return_value=cal_row)

        payload = {
            "organization_id": "org-test",
            "name": "Org-scoped Calendar",
            "scope": "organization",
        }
        resp = self.client.post("/v1/calendars", json=payload)

        assert resp.status_code == 201
        # Only the INSERT fetchrow — no tenant config query
        assert self.pool.fetchrow.call_count == 1
