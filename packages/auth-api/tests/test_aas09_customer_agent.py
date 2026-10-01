"""
test_aas09_customer_agent.py — AAS-09 (2026-10-01): o token que o PRÓPRIO cliente gera.

PROPOSIÇÕES (adr-a2a-server-binding D6, D9, D13):
  · emitir, retirar e revogar são portas SÓ de serviço — sem `X-Service-Token`, 401;
  · a emissão NÃO cria credencial: cria um código de retirada, guardado só como hash; e recusa
    pool que não admite `customer_agent` ou que não declara a política (validade e cota nunca
    têm default), prova com mecanismo desconhecido e mandato malformado;
  · vários pools num token: vale a política MAIS RESTRITA de cada eixo;
  · a credencial NASCE na retirada, uma vez; código inexistente, vencido ou já usado = 410 igual;
  · revogar só alcança tokens DO titular;
  · a introspecção carrega titular, prova, mandato, validade e cota — e token vencido é inativo;
  · o admin do tenant só desliga o token do cliente: não rotaciona, não alarga, não renomeia.
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from plughub_auth_api import agent_principals as ap

from test_agent_principals import H, NOW, PID, SETTINGS, _row, _token, client  # noqa: F401
from test_router import mock_pool  # noqa: F401

SVC = {"X-Service-Token": "svc-token-de-teste"}
POL = {"validity_days": 7, "max_active_tasks": 2, "max_tasks_per_day": 20}


def _pool(kinds=("customer_agent",), pol: dict | None = POL) -> tuple[int, dict]:
    a2a: dict = {"principal_kinds": list(kinds)}
    if pol is not None:
        a2a["customer_agent"] = pol
    return 200, {"purpose": "contact", "channel_types": ["a2a"], "a2a": a2a}


def _registry(monkeypatch, pools: dict[str, tuple[int, dict]]):
    def h(req: httpx.Request) -> httpx.Response:
        assert req.headers["x-tenant-id"] == "tenant_test"
        st, body = pools.get(req.url.path.rsplit("/", 1)[-1], (404, {}))
        return httpx.Response(st, json=body)
    real = httpx.AsyncClient
    monkeypatch.setattr(ap.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(h), **kw))


def _grant(**over) -> dict:
    body = {"tenant_id": "tenant_test", "customer_id": "cli-1",
            "proof": {"mechanism": "otp", "verified_at": NOW.isoformat(), "session_id": "s-1"},
            "allowed_pools": ["segunda_via"], "mandate": ["consultar"]}
    return {**body, **over}


def _ca_row(**over) -> dict:
    return _row(**{"kind": "customer_agent", "display_name": "assistente do cliente", "customer_id": "cli-1",
                   "proof_mechanism": "otp", "proof_verified_at": NOW, "proof_session_id": "s-1",
                   "mandate": ["consultar"], "expires_at": datetime.now(timezone.utc) + timedelta(days=7),
                   "max_active_tasks": 2, "max_tasks_per_day": 20, **over})


# ── emissão ──────────────────────────────────────────────────────────────────

class TestEmissao:
    def test_sem_credencial_de_servico_e_401(self, client):
        c, _ = client
        assert c.post("/auth/v1/agent-principals/customer-grants", json=_grant()).status_code == 401
        # nem token de usuário com config.agents abre a porta de serviço
        assert c.post("/auth/v1/agent-principals/customer-grants", json=_grant(),
                      headers=H(_token("read_write", master=True))).status_code == 401

    def test_cria_codigo_de_retirada_guardado_so_como_hash(self, client, monkeypatch):
        c, pool = client
        _registry(monkeypatch, {"segunda_via": _pool()})
        pool.fetchrow = AsyncMock(return_value={"grant_id": uuid.uuid4(),
                                                "pickup_expires_at": NOW + timedelta(minutes=10)})
        r = c.post("/auth/v1/agent-principals/customer-grants", json=_grant(), headers=SVC)
        assert r.status_code == 201
        code = r.json()["pickup_code"]
        assert code.startswith("pkc_")
        args = pool.fetchrow.await_args.args
        assert hashlib.sha256(code.encode()).hexdigest() in args and code not in args
        assert "INSERT INTO auth.customer_agent_grants" in args[0]
        assert "agent_principals" not in args[0]          # a credencial NÃO nasce aqui
        assert r.json()["mandate"] == ["consultar"] and r.json()["validity_days"] == 7

    def test_varios_pools_vale_a_politica_mais_restrita(self, client, monkeypatch):
        c, pool = client
        _registry(monkeypatch, {"a": _pool(pol={"validity_days": 30, "max_active_tasks": 1, "max_tasks_per_day": 50}),
                                "b": _pool(pol={"validity_days": 3, "max_active_tasks": 5, "max_tasks_per_day": 10})})
        pool.fetchrow = AsyncMock(return_value={"grant_id": uuid.uuid4(), "pickup_expires_at": NOW})
        r = c.post("/auth/v1/agent-principals/customer-grants", json=_grant(allowed_pools=["a", "b"]), headers=SVC)
        assert r.status_code == 201
        assert (r.json()["validity_days"], r.json()["max_active_tasks"], r.json()["max_tasks_per_day"]) == (3, 1, 10)

    @pytest.mark.parametrize("pool_resp,motivo", [
        (_pool(kinds=("partner",)), "não admite principal_kind 'customer_agent'"),
        (_pool(pol=None), "não declara a política customer_agent"),
        ((404, {}), "não existe"),
    ])
    def test_pool_que_nao_emite_e_recusado_e_nada_grava(self, client, monkeypatch, pool_resp, motivo):
        c, pool = client
        _registry(monkeypatch, {"segunda_via": pool_resp})
        pool.fetchrow = AsyncMock()
        r = c.post("/auth/v1/agent-principals/customer-grants", json=_grant(), headers=SVC)
        assert r.status_code == 422 and r.json()["detail"]["reason"] == "pool_not_customer_agent"
        assert motivo in r.json()["detail"]["pools"][0]
        pool.fetchrow.assert_not_awaited()

    @pytest.mark.parametrize("over,reason", [
        ({"proof": {"mechanism": "palpite", "verified_at": NOW.isoformat(), "session_id": "s"}}, "proof_mechanism_unknown"),
        ({"mandate": ["Contratar Tudo!"]}, "mandate_invalid"),
    ])
    def test_prova_ou_mandato_malformados_sao_recusados(self, client, monkeypatch, over, reason):
        c, pool = client
        _registry(monkeypatch, {"segunda_via": _pool()})
        pool.fetchrow = AsyncMock()
        r = c.post("/auth/v1/agent-principals/customer-grants", json=_grant(**over), headers=SVC)
        assert r.status_code == 422 and r.json()["detail"]["reason"] == reason
        pool.fetchrow.assert_not_awaited()


# ── retirada ─────────────────────────────────────────────────────────────────

class TestRetirada:
    def test_credencial_nasce_na_retirada_uma_vez_e_so_como_hash(self, client):
        c, pool = client
        g = {"grant_id": uuid.uuid4(), "tenant_id": "tenant_test", "customer_id": "cli-1", "display_name": "x",
             "allowed_pools": ["segunda_via"], "proof_mechanism": "otp", "proof_verified_at": NOW,
             "proof_session_id": "s-1", "mandate": ["consultar"], "validity_days": 7,
             "max_active_tasks": 2, "max_tasks_per_day": 20}
        pool.conn.fetchrow = AsyncMock(side_effect=[g, _ca_row()])
        r = c.post("/auth/v1/agent-principals/customer-grants/redeem",
                   json={"tenant_id": "tenant_test", "pickup_code": "pkc_x"}, headers=SVC)
        assert r.status_code == 200
        cred = r.json()["credential"]
        assert cred.startswith("pha_") and r.json()["customer_id"] == "cli-1"
        upd, ins = pool.conn.fetchrow.await_args_list
        assert "redeemed_at IS NULL" in upd.args[0] and "pickup_expires_at > now()" in upd.args[0]
        assert hashlib.sha256(b"pkc_x").hexdigest() in upd.args
        assert "'customer_agent'" in ins.args[0]
        assert hashlib.sha256(cred.encode()).hexdigest() in ins.args and cred not in ins.args

    def test_codigo_indisponivel_e_410_e_nada_nasce(self, client):
        c, pool = client
        pool.conn.fetchrow = AsyncMock(side_effect=[None, {"redeemed_at": NOW}])
        r = c.post("/auth/v1/agent-principals/customer-grants/redeem",
                   json={"tenant_id": "tenant_test", "pickup_code": "pkc_x"}, headers=SVC)
        assert r.status_code == 410 and r.json()["detail"] == "pickup_unavailable"
        assert pool.conn.fetchrow.await_count == 2      # o UPDATE e a leitura do motivo — nenhum INSERT

    def test_retirada_sem_credencial_de_servico_e_401(self, client):
        c, _ = client
        r = c.post("/auth/v1/agent-principals/customer-grants/redeem",
                   json={"tenant_id": "tenant_test", "pickup_code": "pkc_x"})
        assert r.status_code == 401


# ── revogação ────────────────────────────────────────────────────────────────

class TestRevogacao:
    def test_revoga_so_os_do_titular(self, client):
        c, pool = client
        pool.fetch = AsyncMock(return_value=[{"agent_principal_id": PID, "display_name": "x"}])
        r = c.post("/auth/v1/agent-principals/customer/revoke",
                   json={"tenant_id": "tenant_test", "customer_id": "cli-1"}, headers=SVC)
        assert r.status_code == 200 and r.json()["revoked"] == [str(PID)]
        sql, *args = pool.fetch.await_args.args
        assert "customer_id = $2" in sql and "kind = 'customer_agent'" in sql and "tenant_id = $1" in sql
        assert args == ["tenant_test", "cli-1"]

    def test_um_so_por_id_ainda_preso_ao_titular(self, client):
        c, pool = client
        pool.fetch = AsyncMock(return_value=[])
        r = c.post("/auth/v1/agent-principals/customer/revoke",
                   json={"tenant_id": "tenant_test", "customer_id": "cli-1", "principal_id": str(PID)}, headers=SVC)
        assert r.status_code == 200 and r.json()["revoked"] == []
        sql, *args = pool.fetch.await_args.args
        assert "customer_id = $2" in sql and args == ["tenant_test", "cli-1", str(PID)]


# ── introspecção ─────────────────────────────────────────────────────────────

class TestIntrospeccao:
    def test_customer_agent_leva_titular_prova_mandato_validade_e_cota(self, client):
        c, pool = client
        pool.fetchrow = AsyncMock(return_value=_ca_row())
        r = c.post("/auth/v1/agent-principals/introspect", json={"credential": "pha_x"}, headers=SVC)
        b = r.json()
        assert b["active"] is True and b["kind"] == "customer_agent" and b["customer_id"] == "cli-1"
        assert b["proof_mechanism"] == "otp" and b["mandate"] == ["consultar"]
        assert b["max_active_tasks"] == 2 and b["max_tasks_per_day"] == 20 and isinstance(b["exp"], int)

    def test_vencido_e_inativo(self, client):
        c, pool = client
        pool.fetchrow = AsyncMock(return_value=_ca_row(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
        r = c.post("/auth/v1/agent-principals/introspect", json={"credential": "pha_x"}, headers=SVC)
        assert r.json() == {"active": False}

    def test_controle_partner_nao_ganha_campos_de_titular(self, client):
        c, pool = client
        pool.fetchrow = AsyncMock(return_value=_row())
        b = c.post("/auth/v1/agent-principals/introspect", json={"credential": "pha_x"}, headers=SVC).json()
        assert b["active"] is True and b["kind"] == "partner" and "customer_id" not in b and "exp" not in b


# ── administração ───────────────────────────────────────────────────────────

class TestAdminSoDesliga:
    @pytest.mark.parametrize("corpo", [{"allowed_pools": ["outro"]}, {"display_name": "novo"}])
    def test_admin_nao_alarga_nem_renomeia_token_do_cliente(self, client, corpo):
        c, pool = client
        pool.fetchrow = AsyncMock(return_value=_ca_row())
        r = c.put(f"/auth/v1/agent-principals/{PID}", json=corpo, headers=H(_token("read_write", master=True)))
        assert r.status_code == 409
        assert pool.fetchrow.await_count == 1             # só a leitura; nenhum UPDATE

    def test_admin_nao_rotaciona_token_do_cliente(self, client):
        c, pool = client
        pool.fetchrow = AsyncMock(return_value=_ca_row())
        r = c.post(f"/auth/v1/agent-principals/{PID}/credential", headers=H(_token("read_write", master=True)))
        assert r.status_code == 409 and pool.fetchrow.await_count == 1

    def test_controle_admin_desliga_token_do_cliente(self, client):
        c, pool = client
        pool.fetchrow = AsyncMock(side_effect=[_ca_row(), _ca_row(active=False)])
        r = c.put(f"/auth/v1/agent-principals/{PID}", json={"active": False}, headers=H(_token("read_write", master=True)))
        assert r.status_code == 200 and r.json()["active"] is False
