"""
test_tenant_from_token.py — TNT-01 (2026-09-29): usuário lê o PRÓPRIO tenant.

Medido antes: com o JWT do `tenant_demo`, `?tenant_id=<outro>` devolvia 200 com os dados do
outro tenant (histórico e visão 360 de um cliente que só existia lá). A regra mora no ramo de
JWT de usuário do `optional_pool_principal`, por onde passam as três dependências de principal
de pool — então cada uma é medida aqui, e cada recusa tem o seu controle.
"""
from __future__ import annotations

import time

import jwt
import pytest
from fastapi import Depends, FastAPI, Query
from fastapi.testclient import TestClient

from plughub_analytics_api import pool_auth
from plughub_analytics_api.pool_auth import (
    PoolPrincipal, optional_pool_principal, require_pool_principal, sse_pool_principal,
)

SECRET = "segredo-de-teste-tnt01-com-32-bytes!!"
SVC    = "svc-tnt01"


class _S:
    auth_jwt_secret        = SECRET
    analytics_open_access  = False
    analytics_service_token = SVC


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    monkeypatch.setattr(pool_auth, "get_settings", lambda: _S())


def _tok(tenant: str | None = "tenant_a") -> str:
    claims = {"sub": "u1", "exp": int(time.time()) + 600, "unrestricted": True, "accessible_pools": []}
    if tenant is not None:
        claims["tenant_id"] = tenant
    return jwt.encode(claims, SECRET, algorithm="HS256")


def _app() -> TestClient:
    app = FastAPI()

    def _echo(p: PoolPrincipal, tenant_id: str | None) -> dict:
        return {"principal_tenant": p.tenant_id, "sub": p.sub, "query_tenant": tenant_id}

    @app.get("/opt")
    async def opt(tenant_id: str | None = Query(None), p: PoolPrincipal = Depends(optional_pool_principal)):
        return _echo(p, tenant_id)

    @app.get("/req")
    async def req(tenant_id: str | None = Query(None), p: PoolPrincipal = Depends(require_pool_principal)):
        return _echo(p, tenant_id)

    @app.get("/sse")
    async def sse(tenant_id: str | None = Query(None), p: PoolPrincipal = Depends(sse_pool_principal)):
        return _echo(p, tenant_id)

    return TestClient(app)


def _get(c: TestClient, rota: str, tenant: str | None, tok: str | None = None, **extra):
    params = {"tenant_id": tenant} if tenant else {}
    headers = {}
    if rota == "/sse" and tok:
        params["token"] = tok                      # EventSource não manda cabeçalho
    elif tok:
        headers["Authorization"] = f"Bearer {tok}"
    headers.update(extra)
    return c.get(rota, params=params, headers=headers)


@pytest.mark.parametrize("rota", ["/opt", "/req", "/sse"])
def test_tenant_de_outro_na_query_e_recusado(rota):
    r = _get(_app(), rota, "tenant_b", _tok("tenant_a"))
    assert r.status_code == 403 and r.json()["detail"] == "tenant_mismatch"


@pytest.mark.parametrize("rota", ["/opt", "/req", "/sse"])
def test_controle_o_proprio_tenant_passa(rota):
    r = _get(_app(), rota, "tenant_a", _tok("tenant_a"))
    assert r.status_code == 200 and r.json()["principal_tenant"] == "tenant_a"


def test_controle_sem_tenant_na_query_passa_preso_ao_token():
    r = _get(_app(), "/opt", None, _tok("tenant_a"))
    assert r.status_code == 200 and r.json()["principal_tenant"] == "tenant_a"


def test_token_de_usuario_sem_tenant_e_recusado():
    r = _get(_app(), "/opt", "tenant_a", _tok(None))
    assert r.status_code == 403 and r.json()["detail"] == "user_token_without_tenant"


def test_servico_segue_escolhendo_o_tenant():
    """Controle do eixo: credencial de SERVIÇO não é usuário, e é para isso que ela existe."""
    r = _get(_app(), "/opt", "tenant_b", None, **{"x-service-token": SVC, "x-service-name": "probe"})
    assert r.status_code == 200 and r.json()["sub"] == "service:probe"
