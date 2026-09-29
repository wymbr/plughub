"""
test_router.py
Unit tests for the Workflow API.

Superfície HTTP que sobra (AUT-64, 2026-09-29):
  GET  /v1/health                   → liveness
  POST /admin/backfill-events       → X-Admin-Token (fecha com segredo ausente)

Removidas, com testemunha aqui:
  POST /.../cancel                  → 404 (2026-08-07, I5 lacuna 4b)
  /v1/workflow/webhook*             → 404 (8 rotas, 2026-09-08, MOD-11)
  11 rotas de instância e proxy     → 404 (2026-09-29, AUT-64)

Coverage:
  TestHealth                — GET /v1/health
  TestTimeoutScanner        — timeout_job._scan_once
  TestWebhookRoutesRemoved  — MOD-11
  TestAut64RoutesRemoved    — AUT-64: as 11 saíram, e o censo trava a superfície
  TestAdminGateFailsClosed  — MOD-11: `_require_admin` deixou de falhar aberto
"""
from __future__ import annotations

from datetime import datetime, timezone
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
NOW_ISO = datetime.now(timezone.utc).isoformat()


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
    )


@pytest.fixture
def client():
    """Sync test client with mocked state."""
    app.state.pool     = make_pool()
    app.state.settings = make_settings()
    app.state.producer = None
    return TestClient(app)


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
        # Era `GET /v1/workflow/instances`, que saiu na AUT-64; o health é o que sobra.
        r = client.get("/v1/health")
        assert r.status_code == 200, "o app parou de responder"


# ─────────────────────────────────────────────
# TestAut64RoutesRemoved — as 11 rotas sem chamador saíram
# ─────────────────────────────────────────────

class TestAut64RoutesRemoved:
    """
    A AUT-58 achou 11 rotas respondendo ao anônimo pela borda. A medição da AUT-64
    achou zero chamadores de produto, então elas SAÍRAM em vez de ganhar portão.
    """

    ROTAS_MORTAS = [
        ("post", "/v1/workflow/trigger"),
        ("post", "/v1/workflow/resume"),
        ("get",  "/v1/workflow/instances?tenant_id=tenant-test"),
        ("get",  f"/v1/workflow/instances/{INSTANCE_ID}"),
        ("get",  f"/v1/workflow/instances/{INSTANCE_ID}/sessions"),
        ("post", f"/v1/workflow/instances/{INSTANCE_ID}/persist-suspend"),
        ("post", f"/v1/workflow/instances/{INSTANCE_ID}/complete"),
        ("post", f"/v1/workflow/instances/{INSTANCE_ID}/fail"),
        ("post", f"/v1/workflow/instances/{INSTANCE_ID}/collect/persist"),
        ("post", "/v1/workflow/collect/respond"),
        ("get",  "/v1/workflow/campaigns/c1/collects?tenant_id=tenant-test"),
    ]

    def test_as_onze_rotas_respondem_404(self, client):
        """404 = não REGISTRADA. Com header de admin: rota só gateada daria 401/403/200."""
        for metodo, url in self.ROTAS_MORTAS:
            extra = {"json": {}} if metodo == "post" else {}
            r = getattr(client, metodo)(url, headers={"X-Admin-Token": "qualquer"}, **extra)
            assert r.status_code == 404, f"{metodo.upper()} {url} respondeu {r.status_code}"

    def test_censo_a_superficie_e_exatamente_health_e_backfill(self):
        """
        Rota nova neste serviço tem de ser decisão, não herança: o censo reprova
        qualquer caminho fora desta lista. Lido do `openapi()` — nesta versão do
        FastAPI o router incluído não aparece como `APIRoute` em `app.routes`, e um
        censo por `app.routes` contaria zero e passaria (medido na AUT-59).
        """
        caminhos = {
            (m.upper(), path)
            for path, ops in app.openapi()["paths"].items()
            for m in ops
        }
        assert caminhos == {("GET", "/v1/health"), ("POST", "/admin/backfill-events")}, caminhos


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
