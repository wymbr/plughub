"""
test_caller_gate.py — AUT-62: toda rota da dialog-api exige credencial, e o tenant do
usuário é o do token.

Medido antes (2026-09-29): a leitura era ABERTA por decisão declarada, e a borda pública
publicava `/v1/dialog` — com um `X-Tenant-ID` qualquer um lia os formulários de todos os
tenants. A escrita tinha o `enforce_write`, que desligava o portão com `admin_token` vazio.

App REAL (`main.app`, sem lifespan); um ramo por código, com os controles POSITIVOS.
"""
from __future__ import annotations

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import jwt as pyjwt
import pytest
from httpx import ASGITransport, AsyncClient

from ..main import app

_SECRET = "segredo-do-teste-aut62-com-32-bytes!!"
_ADM, _SVC = "adm-aut62", "svc-aut62"
_GET = "plughub_dialog_api.router.db_get_form"
_LIST = "plughub_dialog_api.router.db_list_forms"


@pytest.fixture(autouse=True)
def _state():
    app.state.settings = SimpleNamespace(admin_token=_ADM, service_token=_SVC, jwt_secret=_SECRET)
    app.state.pool = MagicMock()
    yield


def _tok(tenant: str | None = "t1", **grants: str) -> dict[str, str]:
    cfg = {"config": {f: {"access": a, "scope": []} for f, a in grants.items()}} if grants else {}
    payload = {"sub": "u1", "module_config": cfg}
    if tenant is not None:
        payload["tenant_id"] = tenant
    return {"Authorization": "Bearer " + pyjwt.encode(payload, _SECRET, algorithm="HS256")}


async def _req(method, path, headers=None, **kw):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        return await c.request(method, path, headers=headers or {}, **kw)


_FORM = {"form_id": "f1", "default_locale": "pt-BR", "locales": ["pt-BR"], "nodes": [{"id": "n"}]}


@pytest.mark.asyncio
async def test_anonymous_read_with_tenant_header_is_401():
    """O defeito medido: header sozinho lia o formulário de qualquer tenant."""
    assert (await _req("GET", "/v1/dialog/forms", {"X-Tenant-ID": "t1"})).status_code == 401


@pytest.mark.asyncio
async def test_any_user_of_the_tenant_reads_and_tenant_comes_from_token():
    """Leitura NÃO pede campo: o Console renderiza formulário para operador que não o edita."""
    with patch(_LIST, new=AsyncMock(return_value=[])) as db:
        r = await _req("GET", "/v1/dialog/forms", _tok("t1"))
    assert r.status_code == 200, r.text
    assert db.await_args.args[1] == "t1"


@pytest.mark.asyncio
async def test_user_header_of_another_tenant_is_403():
    r = await _req("GET", "/v1/dialog/forms", {**_tok("t1"), "X-Tenant-ID": "outro"})
    assert r.status_code == 403
    assert r.json()["detail"] == "tenant_mismatch"


@pytest.mark.asyncio
async def test_service_reads_and_header_chooses_the_tenant():
    with patch(_GET, new=AsyncMock(return_value={"form_id": "f1"})) as db:
        r = await _req("GET", "/v1/dialog/forms/f1", {"X-Service-Token": _SVC, "X-Tenant-ID": "t9"})
    assert r.status_code == 200
    assert db.await_args.args[1] == "t9"


@pytest.mark.asyncio
async def test_service_does_not_write():
    """Runtime resolve formulário, nunca o edita."""
    r = await _req("POST", "/v1/dialog/forms", {"X-Service-Token": _SVC, "X-Tenant-ID": "t1"}, json=_FORM)
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_write_needs_dialog_forms_grant():
    r = await _req("POST", "/v1/dialog/forms", _tok("t1"), json=_FORM)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_write_with_grant_passes():
    with patch("plughub_dialog_api.router.db_create_form", new=AsyncMock(return_value={"ok": 1})) as db:
        r = await _req("POST", "/v1/dialog/forms", _tok("t1", dialog_forms="read_write"), json=_FORM)
    assert r.status_code == 200, r.text
    assert db.await_args.args[1] == "t1"


@pytest.mark.asyncio
async def test_admin_token_writes():
    with patch("plughub_dialog_api.router.db_create_form", new=AsyncMock(return_value={"ok": 1})):
        r = await _req("POST", "/v1/dialog/forms", {"X-Admin-Token": _ADM, "X-Tenant-ID": "t1"}, json=_FORM)
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_empty_admin_token_closes_the_door_instead_of_opening_the_gate():
    """Era o contrário: `enforce_write` com admin_token vazio LIBERAVA a escrita."""
    app.state.settings = SimpleNamespace(admin_token="", service_token="", jwt_secret=_SECRET)
    r = await _req("POST", "/v1/dialog/forms", {"X-Admin-Token": "", "X-Tenant-ID": "t1"}, json=_FORM)
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_missing_jwt_secret_is_503():
    app.state.settings = SimpleNamespace(admin_token=_ADM, service_token=_SVC, jwt_secret="")
    r = await _req("GET", "/v1/dialog/forms", _tok("t1"))
    assert r.status_code == 503


@pytest.mark.asyncio
async def test_every_route_but_health_refuses_an_anonymous_caller():
    """Censo por HTTP sobre o OpenAPI, com PISO (censo de zero rotas passa por ausência)."""
    abertas, n = [], 0
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        for path, ops in app.openapi()["paths"].items():
            for method in ops:
                if method not in {"get", "post", "put", "patch", "delete"} or path == "/v1/health":
                    continue
                n += 1
                r = await c.request(method.upper(), re.sub(r"\{[^}]+\}", "x", path),
                                    headers={"X-Tenant-ID": "t1", "content-type": "application/json"},
                                    content='"x"')
                if r.status_code != 401:
                    abertas.append(f"{method.upper()} {path} -> {r.status_code}")
    assert n >= 7, f"censo varreu só {n} rotas"
    assert not abertas, f"rotas que não recusam anônimo: {abertas}"
