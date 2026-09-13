"""
test_session_token_mint.py — PID-01 (2026-09-13)

O bridge pede ao mcp-server, na ATIVAÇÃO, o token ligado à sessão que as tools de
retomada exigem, e o manda no corpo do /execute — nunca no `session_context`, que o
YAML lê. Falhar não bloqueia a ativação; deixa de mandar o token, com log.
"""
import logging

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

import plughub_orchestrator_bridge.main as bridge_mod

TENANT, SID, INST, SKILL = "tenant_t", "sid-1", "inst-1", "skill_x_v1"
_FLOW = {"entry": "a", "steps": [{"id": "a", "type": "complete", "outcome": "resolved"}]}


def _ctx(resp):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _resp(status, body, text=""):
    r = MagicMock()
    r.status = status
    r.json = AsyncMock(return_value=body)
    r.text = AsyncMock(return_value=text)
    return r


def _http(mint_status=200, mint_body=None, mint_raises=None):
    chamadas = []

    def _post(url, json=None, headers=None, timeout=None):
        chamadas.append({"url": url, "json": json, "headers": headers or {}})
        if url.endswith("/internal/session-token"):
            if mint_raises:
                raise mint_raises
            return _ctx(_resp(mint_status, mint_body or {}, "recusado"))
        return _ctx(_resp(200, {"outcome": "resolved"}))

    http = AsyncMock()
    http.post = MagicMock(side_effect=_post)
    return http, chamadas


async def _ativa(http):
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    with patch.object(bridge_mod, "resolve_flow_for_agent", new_callable=AsyncMock, return_value=(SKILL, _FLOW)), \
         patch.object(bridge_mod, "_resolve_journey_root", new_callable=AsyncMock, return_value=""):
        await bridge_mod.activate_native_agent(
            http=http, redis_client=redis, session_id=SID, customer_id="cus",
            agent_type_id=SKILL, tenant_id=TENANT, skills=[], instance_id=INST,
        )


def _execute(chamadas):
    return next(c for c in chamadas if c["url"].endswith("/execute"))["json"]


@pytest.mark.asyncio
async def test_emite_e_manda_no_corpo(monkeypatch):
    monkeypatch.setattr(bridge_mod, "MCP_INTERNAL_SERVICE_TOKEN", "svc")
    http, chamadas = _http(mint_body={"session_token": "TOK"})
    await _ativa(http)
    mint = chamadas[0]
    assert mint["url"].endswith("/internal/session-token")
    assert mint["headers"] == {"x-service-token": "svc"}
    assert mint["json"] == {"tenant_id": TENANT, "session_id": SID, "instance_id": INST, "skill_id": SKILL}
    payload = _execute(chamadas)
    assert payload["session_token"] == "TOK"
    # nunca onde o YAML lê
    assert "session_token" not in payload["session_context"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kw", [{"mint_status": 503}, {"mint_raises": RuntimeError("down")}])
async def test_falha_nao_bloqueia_e_loga(monkeypatch, caplog, kw):
    monkeypatch.setattr(bridge_mod, "MCP_INTERNAL_SERVICE_TOKEN", "svc")
    http, chamadas = _http(**kw)
    with caplog.at_level(logging.ERROR):
        await _ativa(http)
    assert "session_token" not in _execute(chamadas)
    assert any("PID-01" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_sem_segredo_nem_tenta_e_loga(monkeypatch, caplog):
    monkeypatch.setattr(bridge_mod, "MCP_INTERNAL_SERVICE_TOKEN", "")
    http, chamadas = _http(mint_body={"session_token": "TOK"})
    with caplog.at_level(logging.ERROR):
        await _ativa(http)
    assert [c["url"] for c in chamadas if c["url"].endswith("/internal/session-token")] == []
    assert "session_token" not in _execute(chamadas)
    assert any("MCP_INTERNAL_SERVICE_TOKEN vazio" in r.message for r in caplog.records)
