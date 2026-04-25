"""
test_admin.py
Unit tests for admin RBAC (auth.py) and consolidated query (admin_query.py).

Strategy:
  - TestPrincipal: verify role/tenant logic
  - TestRequirePrincipal: decode valid/invalid JWTs, missing token, expired, wrong role
  - TestQueryConsolidated: mock CH client with successive results for
    by_channel query (sessions) + by_pool queries (sessions + sentiment)
  - Error path: CH raises → returns empty lists with error key
"""
from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest

from ..admin_query import query_consolidated
from ..auth import Principal, require_principal

SECRET = "test_secret"
TENANT = "tenant_telco"


# ── TestPrincipal ─────────────────────────────────────────────────────────────

class TestPrincipal:
    def test_admin_is_admin(self):
        p = Principal(role="admin", tenant_id=None, sub="admin@co")
        assert p.is_admin
        assert not p.is_operator

    def test_operator_is_operator(self):
        p = Principal(role="operator", tenant_id=TENANT, sub="op@co")
        assert p.is_operator
        assert not p.is_admin

    def test_admin_effective_tenant_passes_through_none(self):
        p = Principal(role="admin", tenant_id=None, sub="a")
        assert p.effective_tenant(None) is None

    def test_admin_effective_tenant_passes_through_value(self):
        p = Principal(role="admin", tenant_id=None, sub="a")
        assert p.effective_tenant(TENANT) == TENANT

    def test_operator_effective_tenant_ignores_requested(self):
        """Operator always returns their own tenant, never the caller's override."""
        p = Principal(role="operator", tenant_id=TENANT, sub="op@co")
        assert p.effective_tenant("other_tenant") == TENANT
        assert p.effective_tenant(None) == TENANT


# ── TestRequirePrincipal ──────────────────────────────────────────────────────

def _make_token(payload: dict, secret: str = SECRET) -> str:
    return jwt.encode(payload, secret, algorithm="HS256")


def _make_credentials(token: str) -> MagicMock:
    creds = MagicMock()
    creds.credentials = token
    return creds


class TestRequirePrincipal:
    @pytest.fixture(autouse=True)
    def _patch_settings(self, monkeypatch):
        settings = MagicMock()
        settings.admin_jwt_secret = SECRET
        monkeypatch.setattr(
            "plughub_analytics_api.auth.get_settings",
            lambda: settings,
        )

    async def test_valid_admin_token(self):
        token = _make_token({"sub": "admin@co", "role": "admin"})
        p = await require_principal(_make_credentials(token))
        assert p.is_admin
        assert p.sub == "admin@co"

    async def test_valid_operator_token(self):
        token = _make_token({"sub": "op@co", "role": "operator", "tenant_id": TENANT})
        p = await require_principal(_make_credentials(token))
        assert p.is_operator
        assert p.tenant_id == TENANT

    async def test_missing_token_raises_401(self):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            await require_principal(None)
        assert exc_info.value.status_code == 401

    async def test_invalid_signature_raises_401(self):
        from fastapi import HTTPException
        token = _make_token({"sub": "x", "role": "admin"}, secret="wrong_secret")
        with pytest.raises(HTTPException) as exc_info:
            await require_principal(_make_credentials(token))
        assert exc_info.value.status_code == 401

    async def test_expired_token_raises_401(self):
        from fastapi import HTTPException
        token = _make_token({"sub": "x", "role": "admin", "exp": int(time.time()) - 3600})
        with pytest.raises(HTTPException) as exc_info:
            await require_principal(_make_credentials(token))
        assert exc_info.value.status_code == 401

    async def test_operator_without_tenant_id_raises_403(self):
        from fastapi import HTTPException
        token = _make_token({"sub": "op@co", "role": "operator"})  # no tenant_id
        with pytest.raises(HTTPException) as exc_info:
            await require_principal(_make_credentials(token))
        assert exc_info.value.status_code == 403

    async def test_unknown_role_raises_403(self):
        from fastapi import HTTPException
        token = _make_token({"sub": "x", "role": "superuser"})
        with pytest.raises(HTTPException) as exc_info:
            await require_principal(_make_credentials(token))
        assert exc_info.value.status_code == 403

    async def test_default_role_is_operator_but_requires_tenant(self):
        """Token with no role field defaults to 'operator' — must have tenant_id."""
        from fastapi import HTTPException
        token = _make_token({"sub": "x", "tenant_id": TENANT})  # role defaults to operator
        p = await require_principal(_make_credentials(token))
        assert p.is_operator
        assert p.tenant_id == TENANT


# ── TestQueryConsolidated ─────────────────────────────────────────────────────

def _ch_result(col_names: list[str], rows: list[list]) -> MagicMock:
    r = MagicMock()
    r.column_names = col_names
    r.result_rows  = rows
    return r


def _make_client(*query_results) -> MagicMock:
    client = MagicMock()
    client.query = MagicMock(side_effect=list(query_results))
    return client


class TestQueryConsolidated:
    # by_channel cols: tenant_id, channel, outcome, sessions, avg_handle_ms
    _CH_COLS = ["tenant_id", "channel", "outcome", "sessions", "avg_handle_ms"]
    # by_pool sessions cols: tenant_id, pool_id, sessions, avg_handle_ms
    _POOL_COLS = ["tenant_id", "pool_id", "sessions", "avg_handle_ms"]
    # sentiment cols: tenant_id, pool_id, avg_sentiment, sample_count
    _SENT_COLS = ["tenant_id", "pool_id", "avg_sentiment", "sample_count"]

    async def test_returns_required_keys(self):
        client = _make_client(
            _ch_result(self._CH_COLS, []),
            _ch_result(self._POOL_COLS, []),
            _ch_result(self._SENT_COLS, []),
        )
        result = await query_consolidated(client, "plughub", None)
        assert "scope" in result
        assert "period" in result
        assert "by_channel" in result
        assert "by_pool" in result

    async def test_admin_scope_is_all_tenants(self):
        client = _make_client(
            _ch_result(self._CH_COLS, []),
            _ch_result(self._POOL_COLS, []),
            _ch_result(self._SENT_COLS, []),
        )
        result = await query_consolidated(client, "plughub", tenant_id=None)
        assert result["scope"] == "all_tenants"

    async def test_operator_scope_is_tenant_id(self):
        client = _make_client(
            _ch_result(self._CH_COLS, []),
            _ch_result(self._POOL_COLS, []),
            _ch_result(self._SENT_COLS, []),
        )
        result = await query_consolidated(client, "plughub", tenant_id=TENANT)
        assert result["scope"] == TENANT

    async def test_by_channel_aggregated_correctly(self):
        """Two rows for the same (tenant, channel) should be collapsed."""
        client = _make_client(
            _ch_result(self._CH_COLS, [
                [TENANT, "webchat", "resolved",    100, 40000.0],
                [TENANT, "webchat", "transferred",  20,  None],
            ]),
            _ch_result(self._POOL_COLS, []),
            _ch_result(self._SENT_COLS, []),
        )
        result = await query_consolidated(client, "plughub", tenant_id=TENANT)
        ch = result["by_channel"]
        assert len(ch) == 1
        entry = ch[0]
        assert entry["channel"]  == "webchat"
        assert entry["sessions"] == 120
        assert entry["by_outcome"]["resolved"]    == 100
        assert entry["by_outcome"]["transferred"] == 20
        # avg_handle_ms: only resolved row has value → 40000
        assert entry["avg_handle_ms"] == 40000

    async def test_by_pool_with_sentiment_overlay(self):
        client = _make_client(
            _ch_result(self._CH_COLS, []),
            _ch_result(self._POOL_COLS, [
                [TENANT, "retencao_humano", 80, 38000.0],
            ]),
            _ch_result(self._SENT_COLS, [
                [TENANT, "retencao_humano", 0.42, 300],
            ]),
        )
        result = await query_consolidated(client, "plughub", tenant_id=TENANT)
        pools = result["by_pool"]
        assert len(pools) == 1
        p = pools[0]
        assert p["pool_id"]                == "retencao_humano"
        assert p["sessions"]               == 80
        assert p["avg_handle_ms"]          == 38000
        assert p["avg_sentiment"]          == 0.42
        assert p["sentiment_sample_count"] == 300

    async def test_by_pool_no_sentiment_leaves_none(self):
        """Pool rows without matching sentiment rows get avg_sentiment=None."""
        client = _make_client(
            _ch_result(self._CH_COLS, []),
            _ch_result(self._POOL_COLS, [
                [TENANT, "retencao_humano", 10, None],
            ]),
            _ch_result(self._SENT_COLS, []),  # no sentiment for this pool
        )
        result = await query_consolidated(client, "plughub", tenant_id=TENANT)
        p = result["by_pool"][0]
        assert p["avg_sentiment"] is None
        assert p["sentiment_sample_count"] == 0
        assert p["avg_handle_ms"] is None

    async def test_multiple_channels_two_tenants(self):
        """Admin query: two tenants, two channels each."""
        rows = [
            ["tenant_a", "webchat",  "resolved", 50, 30000.0],
            ["tenant_b", "whatsapp", "resolved", 30, 25000.0],
        ]
        client = _make_client(
            _ch_result(self._CH_COLS, rows),
            _ch_result(self._POOL_COLS, []),
            _ch_result(self._SENT_COLS, []),
        )
        result = await query_consolidated(client, "plughub", tenant_id=None)
        assert result["scope"] == "all_tenants"
        ch_channels = {(e["tenant_id"], e["channel"]) for e in result["by_channel"]}
        assert ("tenant_a", "webchat")  in ch_channels
        assert ("tenant_b", "whatsapp") in ch_channels

    async def test_error_returns_empty_with_error_key(self):
        client = MagicMock()
        client.query = MagicMock(side_effect=Exception("ch down"))
        result = await query_consolidated(client, "plughub", tenant_id=TENANT)
        assert result["by_channel"] == []
        assert result["by_pool"]    == []
        assert result.get("error")  == "data_unavailable"
