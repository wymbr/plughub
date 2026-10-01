"""
test_agent_principals.py — AAS-04 (2026-10-01): o principal externo `partner`.

PROPOSIÇÃO: só quem tem `config.agents` administra principal; o tenant é o do TOKEN; conceder
pool exige deter o pool (o master passa) e que o pool exponha A2A a `partner`; a credencial
sai em claro uma vez e só existe como hash; a introspecção é só de serviço, responde no formato
RFC 7662 e não aceita credencial desconhecida, desativada ou com serviço sem token.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from plughub_auth_api import agent_principals as ap
from plughub_auth_api.config import Settings, get_settings
from plughub_auth_api.jwt_utils import create_access_token
from plughub_auth_api.main import build_app

from test_router import TEST_SETTINGS, mock_pool  # noqa: F401  (fixture)

SETTINGS = Settings(**{**TEST_SETTINGS.model_dump(), "service_token": "svc-token-de-teste",
                       "agent_registry_url": "http://registry.test"})
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
PID = uuid.UUID("aaaaaaaa-0000-0000-0000-0000000000aa")


def _row(**over):
    base = {"agent_principal_id": PID, "tenant_id": "tenant_test", "kind": "partner", "origin": "external",
            "display_name": "ERP do parceiro", "allowed_pools": ["segunda_via"], "credential_prefix": "pha_abcdefgh",
            "credential_rotated_at": NOW, "active": True, "created_by": "admin@test", "created_at": NOW,
            "updated_at": NOW, "last_authenticated_at": None}
    return {**base, **over}


def _token(agents: str | None = "read_write", pools: list[str] | None = None, master: bool = False, tenant="tenant_test"):
    mc: dict = {"config": {}}
    if agents:
        mc["config"]["agents"] = {"access": agents}
    if master:
        mc["config"]["permissions"] = {"access": "read_write"}
    return create_access_token(
        user_id=str(uuid.uuid4()), tenant_id=tenant, email="admin@test", name="Admin", roles=["admin"],
        accessible_pools=pools or [], settings=TEST_SETTINGS, module_config=mc)


@pytest.fixture()
def client(mock_pool):  # noqa: F811
    app = build_app()
    app.dependency_overrides[get_settings] = lambda: SETTINGS
    with patch("plughub_auth_api.main.asyncpg.create_pool", new=AsyncMock(return_value=mock_pool)), \
         patch("plughub_auth_api.main.db_mod.ensure_schema", new=AsyncMock()), \
         patch("plughub_auth_api.main.ensure_permissions_schema", new=AsyncMock()), \
         patch("plughub_auth_api.main._register_platform_modules", new=AsyncMock()), \
         patch("plughub_auth_api.main.db_mod.seed_admin_if_absent", new=AsyncMock(return_value=False)), \
         patch("plughub_auth_api.router.get_settings", return_value=TEST_SETTINGS), \
         patch("plughub_auth_api.main.get_settings", return_value=TEST_SETTINGS), \
         patch("plughub_auth_api.agent_principals.db_mod.registrar_admin_de_usuario", new=AsyncMock()):
        with TestClient(app, raise_server_exceptions=True) as c:
            c.app.state.pool = mock_pool
            yield c, mock_pool


def _registry(monkeypatch, pools: dict[str, tuple[int, dict]] | None = None, explode: bool = False):
    """O registry de mentira: pool_id → (status, corpo)."""
    vistos: list[str] = []

    def h(req: httpx.Request) -> httpx.Response:
        if explode:
            raise httpx.ConnectError("fora")
        pid = req.url.path.rsplit("/", 1)[-1]
        vistos.append(pid)
        assert req.headers["x-tenant-id"] == "tenant_test"   # tenant do token, nunca da query
        st, body = (pools or {}).get(pid, (404, {}))
        return httpx.Response(st, json=body)

    real = httpx.AsyncClient
    monkeypatch.setattr(ap.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(h), **kw))
    return vistos


EXPOSTO = (200, {"purpose": "contact", "channel_types": ["a2a"], "a2a": {"principal_kinds": ["partner"]}})
H = lambda tok: {"Authorization": f"Bearer {tok}"}   # noqa: E731


# ── criação ──────────────────────────────────────────────────────────────────

def test_cria_devolve_a_credencial_uma_vez_e_grava_so_o_hash(client, monkeypatch):
    c, pool = client
    _registry(monkeypatch, {"segunda_via": EXPOSTO})
    pool.fetchrow.return_value = _row()
    r = c.post("/auth/v1/agent-principals", json={"display_name": "ERP do parceiro", "allowed_pools": ["segunda_via"]},
               headers=H(_token(pools=["segunda_via"])))
    assert r.status_code == 201, r.text
    cred = r.json()["credential"]
    assert cred.startswith("pha_") and len(cred) > 40
    args = pool.fetchrow.call_args.args
    assert args[1] == "tenant_test"                             # tenant do token
    assert args[4] == hashlib.sha256(cred.encode()).hexdigest()  # só o hash vai ao banco
    assert cred not in [a for a in args if isinstance(a, str)]
    assert "credential_hash" not in r.json()


@pytest.mark.parametrize("acesso,esperado", [(None, 403), ("read_only", 403)])
def test_cria_exige_config_agents_read_write(client, acesso, esperado):
    c, pool = client
    r = c.post("/auth/v1/agent-principals", json={"display_name": "x", "allowed_pools": ["p"]}, headers=H(_token(acesso)))
    assert r.status_code == esperado
    pool.fetchrow.assert_not_called()


def test_sem_bearer_e_401(client):
    c, _ = client
    assert c.get("/auth/v1/agent-principals").status_code == 401


def test_conceder_pool_que_nao_detem_e_403_e_nem_pergunta_ao_registry(client, monkeypatch):
    c, pool = client
    vistos = _registry(monkeypatch, {"segunda_via": EXPOSTO})
    r = c.post("/auth/v1/agent-principals", json={"display_name": "x", "allowed_pools": ["segunda_via"]},
               headers=H(_token(pools=["outro"])))
    assert r.status_code == 403 and "segunda_via" in r.text
    assert vistos == [] and not pool.fetchrow.called


def test_master_concede_pool_fora_do_proprio_escopo(client, monkeypatch):
    c, pool = client
    _registry(monkeypatch, {"segunda_via": EXPOSTO})
    pool.fetchrow.return_value = _row()
    r = c.post("/auth/v1/agent-principals", json={"display_name": "x", "allowed_pools": ["segunda_via"]},
               headers=H(_token(pools=[], master=True)))
    assert r.status_code == 201


@pytest.mark.parametrize("corpo,trecho", [
    ((404, {}), "não existe"),
    ((200, {"purpose": "internal", "channel_types": ["a2a"], "a2a": {"principal_kinds": ["partner"]}}), "contato"),
    ((200, {"purpose": "contact", "channel_types": ["webchat"], "a2a": None}), "não expõe"),
    ((200, {"purpose": "contact", "channel_types": ["a2a"], "a2a": {"principal_kinds": ["customer_agent"]}}), "partner"),
])
def test_pool_que_nao_expoe_a2a_a_partner_e_422(client, monkeypatch, corpo, trecho):
    c, pool = client
    _registry(monkeypatch, {"segunda_via": corpo})
    r = c.post("/auth/v1/agent-principals", json={"display_name": "x", "allowed_pools": ["segunda_via"]},
               headers=H(_token(master=True)))
    assert r.status_code == 422 and trecho in r.text
    assert not pool.fetchrow.called


def test_registry_fora_recusa_503_nunca_aprova(client, monkeypatch):
    c, pool = client
    _registry(monkeypatch, explode=True)
    r = c.post("/auth/v1/agent-principals", json={"display_name": "x", "allowed_pools": ["segunda_via"]},
               headers=H(_token(master=True)))
    assert r.status_code == 503 and not pool.fetchrow.called


def test_corpo_nao_escolhe_tenant_nem_tipo(client):
    c, _ = client
    r = c.post("/auth/v1/agent-principals",
               json={"display_name": "x", "allowed_pools": ["p"], "tenant_id": "outro", "kind": "customer_agent"},
               headers=H(_token(master=True)))
    assert r.status_code == 422


# ── edição e rotação ─────────────────────────────────────────────────────────

def test_tirar_pool_nao_exige_deter_nem_consulta_registry(client, monkeypatch):
    c, pool = client
    vistos = _registry(monkeypatch)
    pool.fetchrow.side_effect = [_row(allowed_pools=["a", "b"]), _row(allowed_pools=["a"])]
    r = c.put(f"/auth/v1/agent-principals/{PID}", json={"allowed_pools": ["a"]}, headers=H(_token(pools=[])))
    assert r.status_code == 200 and vistos == []


def test_acrescentar_pool_passa_pela_regua_so_do_que_entra(client, monkeypatch):
    c, pool = client
    vistos = _registry(monkeypatch, {"b": EXPOSTO})
    pool.fetchrow.side_effect = [_row(allowed_pools=["a"]), _row(allowed_pools=["a", "b"])]
    r = c.put(f"/auth/v1/agent-principals/{PID}", json={"allowed_pools": ["a", "b"]}, headers=H(_token(pools=["b"])))
    assert r.status_code == 200 and vistos == ["b"]


def test_rotacionar_troca_o_hash_e_devolve_a_nova(client):
    c, pool = client
    pool.fetchrow.side_effect = [_row(), _row()]
    r = c.post(f"/auth/v1/agent-principals/{PID}/credential", headers=H(_token()))
    assert r.status_code == 200
    nova = r.json()["credential"]
    assert pool.fetchrow.call_args.args[2] == hashlib.sha256(nova.encode()).hexdigest()


def test_principal_de_outro_tenant_e_404(client):
    c, pool = client
    pool.fetchrow.return_value = None
    r = c.get(f"/auth/v1/agent-principals/{PID}", headers=H(_token(tenant="tenant_outro")))
    assert r.status_code == 404
    assert pool.fetchrow.call_args.args[2] == "tenant_outro"   # a busca é presa ao tenant do token


# ── introspecção ─────────────────────────────────────────────────────────────

def _intro(c, cred="pha_x", tok="svc-token-de-teste"):
    return c.post("/auth/v1/agent-principals/introspect", json={"credential": cred},
                  headers={"x-service-token": tok} if tok is not None else {})


def test_introspeccao_ativa_devolve_tenant_e_pools_da_credencial(client):
    c, pool = client
    pool.fetchrow.return_value = _row()
    r = _intro(c, "pha_segredo")
    assert r.status_code == 200
    assert r.json() == {"active": True, "sub": str(PID), "subject_type": "agent", "tenant_id": "tenant_test",
                        "kind": "partner", "origin": "external", "display_name": "ERP do parceiro",
                        "allowed_pools": ["segunda_via"]}
    assert pool.fetchrow.call_args.args[1] == hashlib.sha256(b"pha_segredo").hexdigest()
    pool.execute.assert_awaited()          # last_authenticated_at


@pytest.mark.parametrize("linha", [None, _row(active=False)])
def test_introspeccao_desconhecida_ou_desativada_e_inativa(client, linha):
    c, pool = client
    pool.fetchrow.return_value = linha
    r = _intro(c)
    assert r.status_code == 200 and r.json() == {"active": False}


@pytest.mark.parametrize("tok", [None, "errado"])
def test_introspeccao_so_de_servico(client, tok):
    c, pool = client
    assert _intro(c, tok=tok).status_code == 401
    pool.fetchrow.assert_not_called()


def test_introspeccao_sem_token_configurado_e_503(client):
    c, pool = client
    c.app.dependency_overrides[get_settings] = lambda: Settings(**{**SETTINGS.model_dump(), "service_token": ""})
    assert _intro(c, tok="").status_code == 503
    pool.fetchrow.assert_not_called()


def test_introspeccao_anonima_sem_corpo_e_401_e_nao_422(client):
    """A credencial vem antes do corpo: o anônimo não aprende o formato da rota."""
    c, pool = client
    r = c.post("/auth/v1/agent-principals/introspect")
    assert r.status_code == 401
    pool.fetchrow.assert_not_called()
