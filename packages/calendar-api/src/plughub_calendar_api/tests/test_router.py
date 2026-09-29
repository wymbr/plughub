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

# Credencial de ESCRITA. As doze rotas de escrita ganharam portao dual
# (`_require_calendars_write`, 2026-08-28) e estes testes ficaram para tras dele:
# mediam 401 e afirmavam 200/201/422. Nao e' defeito do portao — e' o padrao que o
# `CLAUDE.md` ja registra em cinco dos sete passos da consolidacao de autorizacao:
# ao mover uma fronteira, os testes que a cercam param de medir o que dizem medir.
#
# ⚠️ O header vai no CLIENTE, e nao em treze call sites, porque treze e o numero de
# lugares onde alguem esqueceria. Quem cobre a ausencia de credencial e' a classe
# `TestWriteGate` no fim do arquivo — sem ela, um portao removido deixaria esta
# suite inteira VERDE.
_ADMIN_TOKEN   = "test-calendar-admin-token"
_WRITE_HEADERS = {"X-Admin-Token": _ADMIN_TOKEN}


def _make_settings(**kwargs) -> Settings:
    defaults = {
        "installation_id": "inst-test",
        "organization_id": "org-test",
        "database_url": "postgresql://localhost/test",
        "default_timezone": "America/Sao_Paulo",
        "admin_token": _ADMIN_TOKEN,
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
    """Minimal calendar row for create_calendar tests.

    "Minimal" tem de significar *todas as colunas que `_row_to_calendar` lê* — não as
    que o teste usa. `always_open` entrou no schema (`db.py:57`) e o mapeamento passou a
    lê-la; este dublê não a fornecia, e os 4 testes de timezone quebravam com
    `KeyError: 'always_open'` — falha em testes que nada têm a ver com o campo.
    Não apareceu antes porque a coleta do arquivo abortava por falta de `httpx`
    (dependência do `starlette.testclient`), então a suíte inteira nunca rodou.
    """
    data = {
        "id": "11111111-1111-1111-1111-111111111111",
        "installation_id": "inst-test",
        "organization_id": "org-test",
        "tenant_id": "tenant-abc",
        "scope": "tenant",
        "name": "Test Calendar",
        "description": "",
        "timezone": "America/New_York",
        "always_open": False,
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
        self.client = TestClient(app, raise_server_exceptions=True,
                                 headers=_WRITE_HEADERS)   # AUT-63: leitura também exige credencial

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
        self.client = TestClient(app, raise_server_exceptions=True,
                                 headers=_WRITE_HEADERS)

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
        self.client = TestClient(app, raise_server_exceptions=True,
                                 headers=_WRITE_HEADERS)

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


# ── O portao de escrita RECUSA sem credencial ────────────────────────────────
#
# Esta classe existe pelo motivo inverso do resto do arquivo: as outras medem que a
# rota FUNCIONA, e por isso passariam identicas se o portao fosse removido amanha.
# Aqui o `TestClient` e' deliberadamente SEM header — e' a unica assercao da suite
# que fica vermelha quando a fronteira some.
class TestWriteGate:
    def setup_method(self):
        self.pool     = _make_pool()
        self.settings = _make_settings()
        app.state.pool     = self.pool
        app.state.settings = self.settings
        self.client = TestClient(app, raise_server_exceptions=True)   # SEM credencial

    def test_patch_tenant_config_sem_credencial_recusa(self):
        self.pool.fetchrow = AsyncMock(return_value=None)
        resp = self.client.patch(
            "/v1/tenant-config",
            json={"tenant_id": "tenant-abc", "default_timezone": "America/Chicago"},
        )
        assert resp.status_code in (401, 403), resp.status_code
        # E a recusa acontece ANTES do handler — nenhuma escrita foi tentada.
        self.pool.fetchrow.assert_not_called()

    def test_post_calendar_sem_credencial_recusa(self):
        self.pool.fetchrow = AsyncMock(return_value=None)
        resp = self.client.post(
            "/v1/calendars",
            json={"organization_id": "org-test", "tenant_id": "tenant-abc",
                  "name": "Test Calendar", "timezone": "Asia/Tokyo"},
        )
        assert resp.status_code in (401, 403), resp.status_code
        self.pool.fetchrow.assert_not_called()

    def test_token_errado_recusa(self):
        """Testemunha de que o portao COMPARA — nao basta mandar qualquer header."""
        self.pool.fetchrow = AsyncMock(return_value=None)
        resp = self.client.patch(
            "/v1/tenant-config",
            headers={"X-Admin-Token": "token-errado"},
            json={"tenant_id": "tenant-abc", "default_timezone": "America/Chicago"},
        )
        assert resp.status_code in (401, 403), resp.status_code


# ── AUT-63 — leitura, motor, tenant e posse de linha ───────────────────────────
import re as _re
import jwt as _pyjwt
from unittest.mock import patch as _patch

_SECRET = "segredo-do-teste-aut63-com-32-bytes!!"
_SVC = "svc-aut63"


def _user(tenant="tenant-abc", **grants):
    cfg = {"config": {f: {"access": a, "scope": []} for f, a in grants.items()}} if grants else {}
    tok = _pyjwt.encode({"sub": "u1", "tenant_id": tenant, "module_config": cfg}, _SECRET, algorithm="HS256")
    return {"Authorization": f"Bearer {tok}"}


class TestCallerGate:
    """AUT-63: leituras e motor respondiam ao anônimo pela borda; as rotas por id não
    olhavam de quem era a linha. Um ramo por código, com os controles POSITIVOS."""

    def setup_method(self):
        self.pool = _make_pool()
        app.state.pool = self.pool
        app.state.settings = _make_settings(jwt_secret=_SECRET, service_token=_SVC)
        self.client = TestClient(app, raise_server_exceptions=True)

    def test_anonymous_read_is_401(self):
        r = self.client.get("/v1/tenant-config", params={"tenant_id": "tenant-abc"})
        assert r.status_code == 401

    def test_anonymous_engine_is_401(self):
        r = self.client.get("/v1/engine/is-open-calendar", params={"calendar_id": "x"})
        assert r.status_code == 401

    def test_user_reads_own_tenant_without_calendar_grant(self):
        """Pools/Agendas/Outbound/Campanhas escolhem calendário sem ter config.calendars."""
        self.pool.fetchrow = AsyncMock(return_value=None)
        r = self.client.get("/v1/tenant-config", params={"tenant_id": "tenant-abc"}, headers=_user())
        assert r.status_code == 200, r.text

    def test_user_query_tenant_of_another_is_403(self):
        r = self.client.get("/v1/tenant-config", params={"tenant_id": "outro"}, headers=_user())
        assert r.status_code == 403
        assert r.json()["detail"] == "tenant_mismatch"

    def test_user_body_tenant_of_another_is_403(self):
        r = self.client.patch("/v1/tenant-config", headers=_user(calendars="read_write"),
                              json={"tenant_id": "outro", "default_timezone": "UTC"})
        assert r.status_code == 403

    def test_user_write_needs_calendars_grant(self):
        r = self.client.patch("/v1/tenant-config", headers=_user(),
                              json={"tenant_id": "tenant-abc", "default_timezone": "UTC"})
        assert r.status_code == 403

    def test_service_reads_engine(self):
        cal = {"id": "c1", "tenant_id": "tenant-zz", "timezone": "UTC", "always_open": True,
               "weekly_schedule": [], "holiday_set_ids": [], "exceptions": []}
        with _patch("plughub_calendar_api.router.db_get_calendar", new=AsyncMock(return_value=cal)),              _patch("plughub_calendar_api.router.db_get_holidays_for_sets", new=AsyncMock(return_value=[])):
            r = self.client.get("/v1/engine/is-open-calendar", params={"calendar_id": "c1"},
                                headers={"X-Service-Token": _SVC})
        assert r.status_code == 200, r.text

    def test_service_does_not_write(self):
        r = self.client.patch("/v1/tenant-config", headers={"X-Service-Token": _SVC},
                              json={"tenant_id": "tenant-abc", "default_timezone": "UTC"})
        assert r.status_code == 403

    def test_calendar_of_another_tenant_by_id_is_404(self):
        alheio = {"id": "c1", "tenant_id": "outro", "name": "x"}
        with _patch("plughub_calendar_api.router.db_get_calendar", new=AsyncMock(return_value=alheio)):
            r = self.client.get("/v1/calendars/c1", headers=_user())
        assert r.status_code == 404

    def test_org_scope_calendar_is_visible(self):
        org = {"id": "c1", "tenant_id": None, "name": "org"}
        with _patch("plughub_calendar_api.router.db_get_calendar", new=AsyncMock(return_value=org)):
            r = self.client.get("/v1/calendars/c1", headers=_user())
        assert r.status_code == 200

    def test_patch_calendar_of_another_tenant_is_404_and_writes_nothing(self):
        alheio = {"id": "c1", "tenant_id": "outro"}
        upd = AsyncMock(return_value={"id": "c1"})
        with _patch("plughub_calendar_api.router.db_get_calendar", new=AsyncMock(return_value=alheio)),              _patch("plughub_calendar_api.router.db_update_calendar", new=upd):
            r = self.client.patch("/v1/calendars/c1", headers=_user(calendars="read_write"), json={"name": "y"})
        assert r.status_code == 404
        upd.assert_not_awaited()

    def test_every_route_but_health_refuses_an_anonymous_caller(self):
        """Censo HTTP sobre o OpenAPI, com PISO (censo de zero rotas passa por ausência)."""
        abertas, n = [], 0
        for path, ops in app.openapi()["paths"].items():
            for method in ops:
                if method not in {"get", "post", "put", "patch", "delete"} or path == "/v1/health":
                    continue
                n += 1
                r = self.client.request(method.upper(), _re.sub(r"\{[^}]+\}", "x", path),
                                        headers={"content-type": "application/json"}, content='"x"')
                if r.status_code != 401:
                    abertas.append(f"{method.upper()} {path} -> {r.status_code}")
        assert n >= 20, f"censo varreu só {n} rotas"
        assert not abertas, f"rotas que não recusam anônimo: {abertas}"
