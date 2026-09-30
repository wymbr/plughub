"""
test_caller_gate.py — AUT-60: toda rota da mailing-api exige credencial, e o tenant do
usuário é o do token.

Medido antes (2026-09-29, `probe_route_anon_sweep.sh`): 22 de 23 rotas decidiam só com o
`X-Tenant-ID` — pela borda pública, com um header que qualquer um escreve, `GET
/v1/mailings` e `/v1/campaigns` devolviam as listas, e `drain` contatava cliente.

Os testes usam o app REAL (`main.app`, sem lifespan) e afirmam um ramo por código,
inclusive os POSITIVOS — sem eles, um portão que recusasse tudo passaria.
"""
from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock, patch

import jwt as pyjwt
import pytest
from httpx import ASGITransport, AsyncClient

from ..config import get_settings
from ..main import app

_SECRET = "segredo-do-teste-aut60-com-32-bytes!!"
_SVC = "svc-do-teste-aut60"


@pytest.fixture(autouse=True)
def _gate(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "jwt_secret", _SECRET, raising=False)
    monkeypatch.setattr(s, "service_token", _SVC, raising=False)
    app.state.pool = MagicMock()
    yield


def _tok(tenant: str | None = "t1", **fields: str) -> dict[str, str]:
    cfg = {f: {"access": a, "scope": []} for f, a in fields.items()}
    payload = {"sub": "u1", "module_config": {"outbound": cfg}}
    if tenant is not None:
        payload["tenant_id"] = tenant
    return {"Authorization": "Bearer " + pyjwt.encode(payload, _SECRET, algorithm="HS256")}


async def _req(method, path, headers=None, **kw):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.request(method, path, headers=headers or {}, **kw)


_LIST = "plughub_mailing_api.router.db_list_mailings"


@pytest.mark.asyncio
async def test_anonymous_with_tenant_header_is_401():
    """O defeito medido: o header sozinho era a credencial."""
    r = await _req("GET", "/v1/mailings", {"X-Tenant-ID": "t1"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_configurar_reads_and_tenant_comes_from_token():
    with patch(_LIST, new=AsyncMock(return_value=[])) as db:
        r = await _req("GET", "/v1/mailings", _tok("t1", configurar="read_write"))
    assert r.status_code == 200, r.text
    assert db.await_args.args[1] == "t1"


@pytest.mark.asyncio
async def test_operacao_read_only_can_read():
    with patch(_LIST, new=AsyncMock(return_value=[])):
        r = await _req("GET", "/v1/mailings", _tok(operacao="read_only"))
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_operacao_read_only_cannot_write():
    r = await _req("POST", "/v1/mailings", _tok(operacao="read_only"), json={"name": "x"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_no_outbound_grant_is_403():
    r = await _req("GET", "/v1/mailings", _tok(operacao="none"))
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_header_tenant_of_another_tenant_is_refused():
    r = await _req("GET", "/v1/mailings", {**_tok("t1", configurar="read_write"), "X-Tenant-ID": "outro"})
    assert r.status_code == 403
    assert r.json()["detail"] == "tenant_mismatch"


@pytest.mark.asyncio
async def test_user_token_without_tenant_is_401():
    r = await _req("GET", "/v1/mailings", _tok(None, configurar="read_write"))
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_agent_actions_are_service_only():
    """Drenar é contatar cliente; nenhuma tela o faz. Usuário com o grant máximo: 403."""
    r = await _req("POST", "/v1/campaigns/c1/drain", _tok(configurar="read_write", operacao="read_write"), json={})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_service_passes_and_header_chooses_the_tenant():
    with patch(_LIST, new=AsyncMock(return_value=[])) as db:
        r = await _req("GET", "/v1/mailings", {"X-Service-Token": _SVC, "X-Tenant-ID": "t9"})
    assert r.status_code == 200
    assert db.await_args.args[1] == "t9"


@pytest.mark.asyncio
async def test_wrong_service_token_is_401():
    r = await _req("GET", "/v1/mailings", {"X-Service-Token": "errado", "X-Tenant-ID": "t1"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_empty_service_secret_closes_the_door(monkeypatch):
    monkeypatch.setattr(get_settings(), "service_token", "", raising=False)
    r = await _req("GET", "/v1/mailings", {"X-Service-Token": "", "X-Tenant-ID": "t1"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_missing_jwt_secret_is_503(monkeypatch):
    monkeypatch.setattr(get_settings(), "jwt_secret", "", raising=False)
    r = await _req("GET", "/v1/mailings", _tok(configurar="read_write"))
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_every_route_but_health_refuses_an_anonymous_caller():
    """Censo por HTTP sobre o OpenAPI do app REAL, com PISO: um censo que varre zero rotas
    passa por ausência de amostra (medido na AUT-59, com o censo por `app.routes`)."""
    abertas, n = [], 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        for path, ops in app.openapi()["paths"].items():
            for method in ops:
                if method not in {"get", "post", "put", "patch", "delete"} or path == "/v1/health":
                    continue
                n += 1
                url = re.sub(r"\{[^}]+\}", "x", path)
                r = await c.request(method.upper(), url, headers={"X-Tenant-ID": "t1",
                                    "content-type": "application/json"}, content='"x"')
                if r.status_code != 401:
                    abertas.append(f"{method.upper()} {path} → {r.status_code}")
    assert n >= 20, f"censo varreu só {n} rotas"
    assert not abertas, f"rotas que não recusam anônimo: {abertas}"


@pytest.mark.asyncio
async def test_data_subject_export_is_service_only():
    """AUD-03: o dossiê do titular sai por aqui para a analytics-api, que confere o DPO.
    Usuário com o grant máximo de outbound não lê os contatos de uma pessoa: 403."""
    r = await _req("POST", "/v1/data-subject/export",
                   _tok(configurar="read_write", operacao="read_write"), json={"customer_ids": ["c"]})
    assert r.status_code == 403
    with patch("plughub_mailing_api.router.db_subject_export",
               new=AsyncMock(return_value={"entries": [], "deliveries": [], "contact_log": []})) as db:
        r = await _req("POST", "/v1/data-subject/export", {"X-Service-Token": _SVC, "X-Tenant-ID": "t9"},
                       json={"customer_ids": ["c"], "contact_values": ["+55"]})
    assert r.status_code == 200, r.text
    assert db.await_args.args[1:] == ("t9", ["c"], ["+55"])


@pytest.mark.asyncio
async def test_aud06_data_subject_erase_is_service_only_and_needs_a_marker():
    """AUD-06: eliminar é mais que ler — só a analytics-api, que confere o DPO em escrita.
    E o marcador tem forma: sem ela, um id qualquer viraria o `customer_id` das linhas."""
    body = {"customer_ids": ["c"], "contact_values": ["+55"], "marker": "erased:abc"}
    r = await _req("POST", "/v1/data-subject/erase",
                   _tok(configurar="read_write", operacao="read_write"), json=body)
    assert r.status_code == 403
    with patch("plughub_mailing_api.router.db_subject_erase",
               new=AsyncMock(return_value={"entries_anonymized": 1, "contact_log_anonymized": 2})) as db:
        bad = await _req("POST", "/v1/data-subject/erase", {"X-Service-Token": _SVC, "X-Tenant-ID": "t9"},
                         json={**body, "marker": "cus_outro"})
        r = await _req("POST", "/v1/data-subject/erase", {"X-Service-Token": _SVC, "X-Tenant-ID": "t9"},
                       json=body)
    assert bad.status_code == 422
    assert r.status_code == 200, r.text
    assert db.await_args.args[1:] == ("t9", ["c"], ["+55"], "erased:abc")
