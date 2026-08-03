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
from plughub_pricing_api.config import Settings, get_settings


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
    """Cliente com o portão de escrita DESLIGADO de propósito.

    **Por que existe (corrigido 2026-08-03).** `require_admin` abre quando
    `settings.admin_token` é vazio (*"auth desabilitada (deploy interno)"*,
    `router.py:94`). Os testes de negócio foram escritos contra esse ramo — o comentário
    original dizia, literalmente, *"should succeed when admin_token is empty (default)"*
    — e o default É vazio… **no repositório**. No container do compose,
    `PLUGHUB_PRICING_ADMIN_TOKEN=demo_pricing_admin_token`
    (`docker-compose.demo.yml:1075`), então `get_settings()` devolvia token não-vazio e as
    **7** rotas gatilhadas passaram a responder 403. Sete vermelhos, nenhuma linha de
    código de produção errada.

    O defeito não é o 403: é o teste ter herdado a configuração do DEPLOY em vez de
    declarar a sua. Um teste assim muda de significado conforme a variável de ambiente do
    dia — e, quando muda, não avisa que mudou de assunto. Mesma família do
    `PLUGHUB_AUTH_JWT_SECRET` na analytics-api.

    Aqui a escolha é explícita: estes testes exercitam **negócio**, então o portão sai do
    caminho. O portão em si ganhou cobertura própria em `TestAdminGate` — que antes não
    existia em lugar nenhum, ou seja, ninguém verificava que um token ERRADO é recusado.
    """
    app.dependency_overrides[get_settings] = lambda: Settings(admin_token="", jwt_secret="")
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_settings, None)


@pytest.fixture
def gated_client():
    """Cliente com o portão LIGADO — `admin_token` e `jwt_secret` fixos no teste."""
    app.dependency_overrides[get_settings] = lambda: Settings(
        admin_token=_GATE_TOKEN, jwt_secret=_GATE_SECRET,
    )
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_settings, None)


_GATE_TOKEN  = "token_de_teste_nao_vazio"
_GATE_SECRET = "segredo_hs256_de_teste_com_32_chars!"


def _bearer(module_config: dict | None = None, exp_delta: int = 3600) -> str:
    """JWT HS256 mínimo, montado à mão — o router valida em stdlib, o teste também."""
    import base64, hashlib, hmac, json, time

    def _b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    header  = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64(json.dumps({
        "sub": "u1",
        "module_config": module_config or {},
        "exp": int(time.time()) + exp_delta,
    }).encode())
    sig = _b64(hmac.new(_GATE_SECRET.encode(), f"{header}.{payload}".encode(),
                        hashlib.sha256).digest())
    return f"{header}.{payload}.{sig}"


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
    def test_upsert_resource_open_when_admin_token_unset(self, mock_upsert, client):
        # Nome anterior: `test_upsert_resource_requires_admin` — que era o oposto do que
        # ele fazia. Sem header nenhum e com `admin_token` vazio, o portão ABRE; o teste
        # nunca exerceu "requires admin". Quem exerce é `TestAdminGate`, abaixo.
        mock_upsert.return_value = MOCK_RESOURCE
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


# ── TestAdminGate ──────────────────────────────────────────────────────────────

class TestAdminGate:
    """O portão de escrita (`require_admin`), que até 2026-08-03 não tinha teste NENHUM.

    Ele governa alteração de recursos contratados e ativação de pool de reserva — ou
    seja, **o que o cliente é cobrado**. Quando os 7 testes ficaram vermelhos por causa
    do `admin_token` do compose, o reflexo seria neutralizar o ambiente e seguir; o achado
    real foi que a única coisa que exercitava aquele código era um acidente de
    configuração, e pelo ramo ABERTO.

    Cada caso afirma o status EXATO: 403 (sem credencial / sem o campo ABAC), 401
    (assinatura inválida, token expirado) e 200. Colapsar em "não-2xx" faria a suíte
    passar por um gate que recusa tudo — inclusive o admin legítimo.
    """

    _WRITE = ("/v1/pricing/reserve/t1/peak_pool/activate", {})

    def test_no_credential_is_403(self, gated_client):
        r = gated_client.post(self._WRITE[0])
        assert r.status_code == 403

    def test_wrong_admin_token_is_403(self, gated_client):
        r = gated_client.post(self._WRITE[0], headers={"X-Admin-Token": "chute"})
        assert r.status_code == 403

    @patch("plughub_pricing_api.router.pricing_db.set_reserve_active", new_callable=AsyncMock)
    @patch("plughub_pricing_api.router.pricing_db.record_activation", new_callable=AsyncMock)
    def test_correct_admin_token_passes(self, mock_record, mock_set, gated_client):
        """Controle positivo: sem ele, os 403 acima passariam num gate que nega tudo."""
        mock_set.return_value    = 2
        mock_record.return_value = MOCK_LOG
        r = gated_client.post(self._WRITE[0], headers={"X-Admin-Token": _GATE_TOKEN})
        assert r.status_code == 200

    @patch("plughub_pricing_api.router.pricing_db.set_reserve_active", new_callable=AsyncMock)
    @patch("plughub_pricing_api.router.pricing_db.record_activation", new_callable=AsyncMock)
    def test_bearer_with_config_plataforma_passes(self, mock_record, mock_set, gated_client):
        mock_set.return_value    = 2
        mock_record.return_value = MOCK_LOG
        tok = _bearer({"config": {"plataforma": {"access": "read_write"}}})
        r = gated_client.post(self._WRITE[0], headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 200

    def test_bearer_without_the_field_is_403(self, gated_client):
        tok = _bearer({"config": {"usuarios": {"access": "read_write"}}})
        r = gated_client.post(self._WRITE[0], headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 403

    def test_bearer_read_only_is_403(self, gated_client):
        """`plataforma` em `read_only` lê, não escreve — rank 1 < 2."""
        tok = _bearer({"config": {"plataforma": {"access": "read_only"}}})
        r = gated_client.post(self._WRITE[0], headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 403

    def test_tampered_signature_is_401(self, gated_client):
        """401, não 403: assinatura quebrada é falha de AUTENTICAÇÃO."""
        tok = _bearer({"config": {"plataforma": {"access": "read_write"}}})
        h, p, sig = tok.split(".")
        r = gated_client.post(self._WRITE[0],
                              headers={"Authorization": f"Bearer {h}.{p}.{sig[:-2]}xy"})
        assert r.status_code == 401

    def test_expired_bearer_is_401(self, gated_client):
        tok = _bearer({"config": {"plataforma": {"access": "read_write"}}}, exp_delta=-60)
        r = gated_client.post(self._WRITE[0], headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 401


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
