"""
WHK-01 — o gatilho por pool só cria sessão para pool que EXISTE.

Cada recusa tem o seu controle: um portão que recusasse tudo passaria em todo "recusou",
então o pool que existe tem de virar sessão, uma vez, no tenant pedido.
"""
from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from plughub_channel_gateway import main as gw_main
from plughub_channel_gateway.pool_existence import pool_existence


# ── o veredito ───────────────────────────────────────────────────────────────

def _transport(status: int | None, seen: list):
    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        if status is None:
            raise httpx.ConnectError("recusada")
        return httpx.Response(status, json={})
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
@pytest.mark.parametrize("status, esperado", [
    (200, "exists"), (404, "not_found"), (500, "unavailable"), (401, "unavailable"), (None, "unavailable"),
])
async def test_veredito_por_resposta_do_registry(status, esperado):
    seen: list = []
    v, motivo = await pool_existence(tenant_id="t1", pool_id="p1", agent_registry_url="http://reg",
                                     service_token="svc", transport=_transport(status, seen))
    assert v == esperado
    assert (motivo == "") == (esperado == "exists"), "recusa sem motivo é degradação muda"
    req = seen[0]
    assert req.url.path == "/v1/pools/p1"
    assert req.headers["x-tenant-id"] == "t1" and req.headers["x-service-token"] == "svc"


@pytest.mark.asyncio
async def test_sem_url_do_registry_nao_e_existe():
    v, motivo = await pool_existence(tenant_id="t1", pool_id="p1", agent_registry_url="")
    assert v == "unavailable" and "agent_registry_url" in motivo


# ── a rota ───────────────────────────────────────────────────────────────────

class _Adapter:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def handle_trigger(self, **kw):
        self.calls.append(kw)
        return "sess-nova"


@pytest.fixture
def rota(monkeypatch):
    adapter = _Adapter()
    monkeypatch.setattr(gw_main, "_webhook_adapter", adapter)
    veredito: dict = {"v": ("exists", "")}

    async def fake(**kw):
        veredito["kw"] = kw
        return veredito["v"]
    monkeypatch.setattr(gw_main, "pool_existence", fake)
    client = TestClient(gw_main.app)          # sem `with`: o lifespan (Kafka, Redis) não roda
    return client, adapter, veredito


def _post(client, pool="p1", tenant="t1"):
    return client.post(f"/v1/channels/webhook/pool/{pool}", json={"tenant_id": tenant})


def test_pool_inexistente_404_e_nenhuma_sessao(rota):
    client, adapter, veredito = rota
    veredito["v"] = ("not_found", "pool 'p1' não existe no tenant 't1'")
    r = _post(client)
    assert r.status_code == 404 and r.json()["detail"]["error"] == "pool_not_found"
    assert adapter.calls == []


def test_registry_fora_503_e_nenhuma_sessao(rota):
    client, adapter, veredito = rota
    veredito["v"] = ("unavailable", "registry inalcançável: recusada")
    r = _post(client)
    assert r.status_code == 503 and r.json()["detail"]["error"] == "pool_unverified"
    assert adapter.calls == []


def test_controle_pool_existente_vira_uma_sessao_no_tenant_pedido(rota):
    client, adapter, veredito = rota
    r = _post(client, pool="p1", tenant="t9")
    assert r.status_code == 201 and r.json() == {"session_id": "sess-nova"}
    assert len(adapter.calls) == 1
    assert adapter.calls[0]["pool_id"] == "p1" and adapter.calls[0]["tenant_id"] == "t9"
    assert veredito["kw"]["tenant_id"] == "t9", "a existência se confere no tenant do PEDIDO"
