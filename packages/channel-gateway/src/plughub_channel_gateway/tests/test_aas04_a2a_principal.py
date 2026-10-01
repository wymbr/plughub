"""
AAS-04 (2026-10-01) — quem chama pelo canal `a2a`, e a porta antes da execução.

PROPOSIÇÃO: a credencial é conferida no auth-api (introspecção, com token de serviço) e o
veredicto fica 30 s em cache pela CHAVE HASH — nunca o texto; falha de conferência é 503 e não
é cacheada. A porta `POST /a2a/{slug}` autentica ANTES de revelar o slug, toma o tenant da
credencial e o exige igual ao da instalação, e só aceita pool que o principal tem — e quem passa
recebe `UnsupportedOperationError`, porque a execução é a AAS-06.
"""

from __future__ import annotations

import hashlib
import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from plughub_channel_gateway import a2a_principal as apr
from plughub_channel_gateway import main as gw_main
from plughub_channel_gateway.config import Settings
from plughub_channel_gateway.endpoint_resolver import ResolvedEndpoint

ATIVO = {"active": True, "sub": "p-1", "subject_type": "agent", "tenant_id": "tenant_x",
         "kind": "partner", "display_name": "ERP", "allowed_pools": ["segunda_via"]}


@pytest.fixture(autouse=True)
def _limpa():
    apr.clear_cache()
    yield
    apr.clear_cache()


async def _auth(respostas, vistos, header="Bearer pha_segredo", url="http://auth:3200", tok="svc"):
    def h(req):
        vistos.append(req)
        r = respostas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r
    async with httpx.AsyncClient(transport=httpx.MockTransport(h)) as c:
        return await apr.authenticate(header, auth_api_url=url, service_token=tok, client=c)


async def test_credencial_ativa_traz_o_tenant_dela_e_cacheia_pelo_hash():
    vistos: list[httpx.Request] = []
    r1 = await _auth([httpx.Response(200, json=ATIVO)], vistos)
    r2 = await _auth([], vistos)
    assert r1.outcome == "ok" and r1.principal.tenant_id == "tenant_x"
    assert r1.principal.allowed_pools == frozenset({"segunda_via"})
    assert r2 == r1 and len(vistos) == 1
    req = vistos[0]
    assert req.url.path == "/auth/v1/agent-principals/introspect"
    assert req.headers["x-service-token"] == "svc"
    assert list(apr._cache) == [hashlib.sha256(b"pha_segredo").hexdigest()]   # nunca o texto


async def test_inativa_e_invalid_e_cacheada():
    vistos: list[httpx.Request] = []
    r = await _auth([httpx.Response(200, json={"active": False})], vistos)
    assert r.outcome == "invalid"
    assert (await _auth([], vistos)) == r


@pytest.mark.parametrize("resp", [httpx.Response(503, text="x"), httpx.Response(401, text="x"),
                                  httpx.ConnectError("fora")])
async def test_falha_de_conferencia_e_unavailable_e_nao_cacheia(resp):
    vistos: list[httpx.Request] = []
    assert (await _auth([resp], vistos)).outcome == "unavailable"
    assert (await _auth([httpx.Response(200, json=ATIVO)], vistos)).outcome == "ok"


@pytest.mark.parametrize("header", ["", "Basic abc", "Bearer ", "pha_sem_esquema"])
async def test_sem_bearer_e_missing_sem_perguntar(header):
    vistos: list[httpx.Request] = []
    assert (await _auth([], vistos, header=header)).outcome == "missing"
    assert vistos == []


async def test_gateway_sem_url_ou_token_de_servico_e_unavailable():
    vistos: list[httpx.Request] = []
    assert (await _auth([], vistos, tok="")).outcome == "unavailable"
    assert (await _auth([], vistos, url="")).outcome == "unavailable"
    assert vistos == []


async def test_ativo_sem_tenant_nao_e_principal():
    vistos: list[httpx.Request] = []
    assert (await _auth([httpx.Response(200, json={**ATIVO, "tenant_id": ""})], vistos)).outcome == "invalid"


# ── a porta ──────────────────────────────────────────────────────────────────

@pytest.fixture
def porta(monkeypatch):
    def arma(auth: apr.AuthResult, ep: ResolvedEndpoint | None = None):
        visto = {"resolve": []}

        async def _authf(header, **kw):
            visto["header"] = header
            return auth

        async def _res(**kw):
            visto["resolve"].append(kw)
            return ep or ResolvedEndpoint("segunda_via", "external", False, None, "found")
        monkeypatch.setattr(apr, "authenticate", _authf)
        monkeypatch.setattr(gw_main, "resolve_endpoint", _res)
        monkeypatch.setattr(gw_main, "get_settings", lambda: Settings(
            tenant_id="tenant_x", auth_api_url="http://auth", auth_api_service_token="svc",
            agent_registry_url="http://reg"))
        return TestClient(gw_main.app), visto
    return arma


OK = apr.AuthResult("ok", principal=apr.Principal("p-1", "tenant_x", "partner", "ERP", frozenset({"segunda_via"})))
RPC = {"jsonrpc": "2.0", "id": 7, "method": "message/send", "params": {}}


def test_controle_principal_com_o_pool_passa_e_chega_ao_adapter(porta, monkeypatch):
    """Desde a AAS-06 quem passa pela porta chega à EXECUÇÃO — e com o tenant e o pool que a
    porta conferiu, nunca com os do corpo (D7)."""
    c, visto = porta(OK)
    recebido = {}

    class _Svc:
        async def handle(self, caller, req):
            recebido["caller"], recebido["req"] = caller, req
            return {"jsonrpc": "2.0", "id": req.get("id"), "result": {"ok": True}}
    monkeypatch.setattr(gw_main, "_a2a_service", lambda: _Svc())
    r = c.post("/a2a/segunda-via", json=RPC, headers={"Authorization": "Bearer pha_x", "A2A-Version": "1.0"})
    assert r.status_code == 200 and r.json() == {"jsonrpc": "2.0", "id": 7, "result": {"ok": True}}
    cl = recebido["caller"]
    assert (cl.sub, cl.tenant_id, cl.pool_id, cl.kind, cl.slug) == (
        "p-1", "tenant_x", "segunda_via", "partner", "segunda-via")
    kw = visto["resolve"][0]
    assert (kw["channel"], kw["identifier"], kw["tenant_id"]) == ("a2a", "segunda-via", "tenant_x")
    assert kw["allowed_origins"] == frozenset({"external"})


@pytest.mark.parametrize("res", [apr.AuthResult("missing"), apr.AuthResult("invalid")])
def test_sem_credencial_valida_e_401_e_o_slug_nem_e_consultado(porta, res):
    c, visto = porta(res)
    r = c.post("/a2a/segunda-via", json=RPC)
    assert r.status_code == 401 and r.headers["www-authenticate"].startswith("Bearer")
    assert visto["resolve"] == []                       # anônimo não aprende se o slug existe


def test_conferencia_fora_e_503(porta):
    c, visto = porta(apr.AuthResult("unavailable", reason="x"))
    assert c.post("/a2a/s", json=RPC, headers={"Authorization": "Bearer y"}).status_code == 503
    assert visto["resolve"] == []


def test_credencial_de_outro_tenant_e_403_e_nao_resolve_no_tenant_dela(porta, caplog):
    outro = apr.AuthResult("ok", principal=apr.Principal("p-2", "tenant_y", "partner", "X", frozenset({"segunda_via"})))
    c, visto = porta(outro)
    with caplog.at_level(logging.WARNING, logger="plughub.channel-gateway"):
        r = c.post("/a2a/segunda-via", json=RPC, headers={"Authorization": "Bearer y"})
    assert r.status_code == 403 and visto["resolve"] == []
    assert any("tenant_y" in x.getMessage() for x in caplog.records)


def test_pool_fora_dos_permitidos_e_403(porta):
    c, _ = porta(OK, ResolvedEndpoint("outro_pool", "external", False, None, "found"))
    assert c.post("/a2a/s", json=RPC, headers={"Authorization": "Bearer y"}).status_code == 403


@pytest.mark.parametrize("outcome,code", [("not_found", 404), ("origin_refused", 404), ("unavailable", 503)])
def test_endereco_que_nao_resolve(porta, outcome, code):
    c, _ = porta(OK, ResolvedEndpoint(None, None, False, None, outcome))
    assert c.post("/a2a/s", json=RPC, headers={"Authorization": "Bearer y"}).status_code == code


def test_corpo_que_nao_e_json_e_parse_error(porta):
    c, _ = porta(OK)
    r = c.post("/a2a/segunda-via", content=b"{nao json", headers={"Authorization": "Bearer y",
                                                                 "content-type": "application/json"})
    assert r.json()["error"]["code"] == -32700
