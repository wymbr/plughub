"""
PRD-03 — o `/execute` não tem teto escondido de conexões.

A requisição ao skill-flow-service fica aberta a conversa inteira, e rodava no
`ClientSession()` compartilhado (conector padrão: 100). A 101ª conversa esperava uma
conexão livre sem timeout e sem log. O teste de comportamento segura N requisições
ABERTAS num servidor local e conta quantas chegaram; o controle com o conector padrão
prova que o instrumento reprova (para em 100).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from aiohttp import web

from plughub_orchestrator_bridge import main as bridge_mod

HELD = 150   # acima do limite padrão do aiohttp (100)


async def _count_open_requests(session: aiohttp.ClientSession) -> int:
    """Dispara HELD requisições que o servidor segura abertas; devolve quantas chegaram."""
    arrived = 0
    release = asyncio.Event()

    async def handler(_request):
        nonlocal arrived
        arrived += 1
        await release.wait()
        return web.json_response({})

    app = web.Application()
    app.router.add_post("/execute", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]

    async def one():
        async with session.post(f"http://127.0.0.1:{port}/execute", json={},
                                timeout=aiohttp.ClientTimeout(total=None)) as r:
            await r.read()

    tasks = [asyncio.create_task(one()) for _ in range(HELD)]
    for _ in range(100):                       # até 2 s para chegarem todas
        if arrived >= HELD:
            break
        await asyncio.sleep(0.02)
    seen = arrived
    release.set()
    await asyncio.gather(*tasks)
    await runner.cleanup()
    return seen


@pytest.mark.asyncio
async def test_sessao_do_execute_nao_tem_teto():
    async with bridge_mod._make_execute_http() as s:
        assert s.connector.limit == 0
        assert await _count_open_requests(s) == HELD


@pytest.mark.asyncio
async def test_controle_conector_padrao_para_em_100():
    # Testemunha: sem ela, o teste acima passaria também num instrumento que não mede nada.
    async with aiohttp.ClientSession() as s:
        assert await _count_open_requests(s) == 100


def _http_recorder(tag, calls):
    def _post(url, json=None, headers=None, timeout=None):
        calls.append((tag, url))
        resp = MagicMock()
        resp.status = 200
        resp.json = AsyncMock(return_value={"outcome": "resolved", "session_token": "T"})
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=resp)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx
    http = AsyncMock()
    http.post = MagicMock(side_effect=_post)
    return http


async def _activate(http):
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    with patch.object(bridge_mod, "resolve_flow_for_agent", new_callable=AsyncMock,
                      return_value=("skill_x", {"entry": "a", "steps": []})), \
         patch.object(bridge_mod, "_resolve_journey_root", new_callable=AsyncMock, return_value=""):
        return await bridge_mod.activate_native_agent(
            http=http, redis_client=redis, session_id="s1", customer_id="c",
            agent_type_id="skill_x", tenant_id="t", skills=[], instance_id="i-001",
        )


@pytest.mark.asyncio
async def test_execute_vai_pela_sessao_dedicada(monkeypatch):
    calls: list = []
    shared = _http_recorder("shared", calls)
    dedicated = _http_recorder("dedicated", calls)
    monkeypatch.setattr(bridge_mod, "_EXECUTE_HTTP", dedicated)
    await _activate(shared)
    execute = [tag for tag, url in calls if url.endswith("/execute")]
    assert execute == ["dedicated"]
    assert all(tag == "shared" for tag, url in calls if not url.endswith("/execute"))


@pytest.mark.asyncio
async def test_contador_em_voo_volta_a_zero_na_falha(monkeypatch):
    monkeypatch.setattr(bridge_mod, "_EXECUTE_IN_FLIGHT", 0)
    boom = AsyncMock()
    boom.post = MagicMock(side_effect=RuntimeError("down"))
    monkeypatch.setattr(bridge_mod, "_EXECUTE_HTTP", boom)
    assert await _activate(_http_recorder("shared", [])) == {}
    assert bridge_mod._EXECUTE_IN_FLIGHT == 0
