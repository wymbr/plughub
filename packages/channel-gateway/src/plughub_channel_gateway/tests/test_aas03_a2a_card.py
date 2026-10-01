"""
AAS-03 (2026-10-01) — a borda pública do AgentCard.

PROPOSIÇÃO: `GET /a2a/{slug}/.well-known/agent-card.json` serve o card que o registry monta,
com a URL pública da env; recusa do registry vira 404 MUDO (o motivo vai ao log, nunca ao
anônimo); registry fora vira 503 e NÃO é cacheado; sem a env, 503 nomeando-a.
"""

from __future__ import annotations

import logging

import httpx
import pytest
from fastapi.testclient import TestClient

from plughub_channel_gateway import a2a_card
from plughub_channel_gateway import main as gw_main
from plughub_channel_gateway.config import Settings

CARD = {"name": "Segunda via", "version": "2026-09-30T12:00:00.000Z"}


def _transport(respostas: list[httpx.Response], vistos: list[httpx.Request]) -> httpx.MockTransport:
    def h(req: httpx.Request) -> httpx.Response:
        vistos.append(req)
        return respostas.pop(0)
    return httpx.MockTransport(h)


@pytest.fixture(autouse=True)
def _limpa():
    a2a_card.clear_cache()
    yield
    a2a_card.clear_cache()


async def _fetch(respostas, vistos):
    async with httpx.AsyncClient(transport=_transport(respostas, vistos)) as c:
        return await a2a_card.fetch_card("segunda-via", tenant_id="t1", registry_url="http://reg:3300/",
                                         base_url="https://borda", client=c)


async def test_card_ok_pergunta_ao_registry_com_tenant_e_base_e_cacheia():
    vistos: list[httpx.Request] = []
    r1 = await _fetch([httpx.Response(200, json=CARD)], vistos)
    r2 = await _fetch([], vistos)                         # sem resposta na fila: tem de vir do cache
    assert (r1.outcome, r1.card) == ("ok", CARD)
    assert r2 == r1
    assert len(vistos) == 1
    req = vistos[0]
    assert req.url.path == "/v1/a2a-cards/segunda-via"
    assert req.url.params["base_url"] == "https://borda"
    assert req.headers["x-tenant-id"] == "t1"


async def test_recusa_carrega_o_motivo_e_e_cacheada():
    vistos: list[httpx.Request] = []
    r = await _fetch([httpx.Response(404, json={"reason": "not_discoverable", "pool_id": "p"})], vistos)
    assert (r.outcome, r.reason, r.pool_id) == ("refused", "not_discoverable", "p")
    assert (await _fetch([], vistos)) == r


@pytest.mark.parametrize("resp", [httpx.Response(500, text="boom"), httpx.Response(400, json={"reason": "base_url_missing"})])
async def test_falha_do_registry_e_indisponivel_e_nao_entra_no_cache(resp):
    vistos: list[httpx.Request] = []
    r = await _fetch([resp], vistos)
    assert r.outcome == "unavailable"
    r2 = await _fetch([httpx.Response(200, json=CARD)], vistos)   # a próxima pergunta DE NOVO
    assert r2.outcome == "ok" and len(vistos) == 2


async def test_registry_inalcancavel_e_indisponivel():
    def explode(_req):
        raise httpx.ConnectError("sem rota")
    async with httpx.AsyncClient(transport=httpx.MockTransport(explode)) as c:
        r = await a2a_card.fetch_card("x", tenant_id="t", registry_url="http://reg", base_url="https://b", client=c)
    assert r.outcome == "unavailable"


# ── a rota ───────────────────────────────────────────────────────────────────

@pytest.fixture
def rota(monkeypatch):
    def arma(resultado: a2a_card.CardResult, base: str = "https://atende.exemplo"):
        chamadas: list[dict] = []

        async def _fake(slug, **kw):
            chamadas.append({"slug": slug, **kw})
            return resultado
        monkeypatch.setattr(a2a_card, "fetch_card", _fake)
        monkeypatch.setattr(gw_main, "get_settings",
                            lambda: Settings(tenant_id="tenant_x", a2a_public_base_url=base,
                                             agent_registry_url="http://reg:3300"))
        return TestClient(gw_main.app), chamadas      # sem `with`: o lifespan não roda
    return arma


def test_rota_serve_o_card_com_cache_publico(rota):
    client, chamadas = rota(a2a_card.CardResult("ok", card=CARD))
    r = client.get("/a2a/segunda-via/.well-known/agent-card.json")
    assert r.status_code == 200 and r.json() == CARD
    assert r.headers["cache-control"] == "public, max-age=30"
    assert chamadas == [{"slug": "segunda-via", "tenant_id": "tenant_x",
                         "registry_url": "http://reg:3300", "base_url": "https://atende.exemplo"}]


@pytest.mark.parametrize("motivo,nivel", [("not_discoverable", logging.INFO), ("no_current_deploy", logging.WARNING)])
def test_recusa_e_404_mudo_e_o_motivo_vai_ao_log(rota, caplog, motivo, nivel):
    client, _ = rota(a2a_card.CardResult("refused", reason=motivo, pool_id="segunda_via"))
    with caplog.at_level(logging.INFO, logger="plughub.channel-gateway.a2a-card"):
        r = client.get("/a2a/segunda-via/.well-known/agent-card.json")
    assert r.status_code == 404
    assert r.json() == {"error": "not_found"}             # sem motivo: nada de oráculo
    rec = [x for x in caplog.records if motivo in x.getMessage()]
    assert rec and rec[0].levelno == nivel


def test_registry_fora_e_503(rota):
    client, _ = rota(a2a_card.CardResult("unavailable", reason="x"))
    assert client.get("/a2a/s/.well-known/agent-card.json").status_code == 503


def test_sem_url_publica_e_503_nomeando_a_env_e_nem_pergunta(rota, caplog):
    client, chamadas = rota(a2a_card.CardResult("ok", card=CARD), base="")
    with caplog.at_level(logging.ERROR, logger="plughub.channel-gateway"):
        r = client.get("/a2a/s/.well-known/agent-card.json")
    assert r.status_code == 503 and r.json() == {"error": "a2a_not_configured"}
    assert chamadas == []
    assert any("PLUGHUB_A2A_PUBLIC_BASE_URL" in x.getMessage() for x in caplog.records)
