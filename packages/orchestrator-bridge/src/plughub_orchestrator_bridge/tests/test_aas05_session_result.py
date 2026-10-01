"""
test_aas05_session_result.py — AAS-05 (2026-10-01): o resultado terminal da sessão.

PROPOSIÇÃO: quando o fluxo do agente PRINCIPAL termina num `complete`, o bridge grava
`{t}:session:{sid}:result` com o TTL da sessão e o veredicto do resultado contra o
`output_schema` do contrato A2A do pool — e não grava nada para conferência (pipeline isolado)
nem para fim que não veio de `complete` (escalar, estacionar).
"""
from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import plughub_orchestrator_bridge.main as bridge_mod
from plughub_orchestrator_bridge import session_result as sr

TENANT, SID, POOL, SKILL = "tenant_t", "sid-1", "segunda_via", "skill_x"
SCHEMA = {"type": "object", "properties": {"linha": {"type": "string"}}, "required": ["linha"]}
POOL_A2A = {"pool_id": POOL, "a2a": {"output_schema": SCHEMA}}


# ── o veredicto ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("pool,result,esperado", [
    (None,                    {"from": "r", "value": {}}, {"checked": False, "reason": "registry_unavailable"}),
    ({"pool_id": POOL},       {"from": "r", "value": {}}, {"checked": False, "reason": "no_contract"}),
    (POOL_A2A,                None,                       {"checked": False, "reason": "result_not_declared"}),
    (POOL_A2A,                {"from": "r", "missing": True}, {"checked": False, "reason": "result_missing"}),
    (POOL_A2A,                {"from": "r", "value": {"linha": "123"}}, {"checked": True, "valid": True}),
])
def test_veredicto(pool, result, esperado):
    assert sr.check_contract(pool, result) == esperado


def test_invalido_nomeia_o_caminho_e_o_motivo():
    v = sr.check_contract(POOL_A2A, {"from": "r", "value": {"linha": 7}})
    assert v["checked"] is True and v["valid"] is False
    assert v["errors"] == ["linha: 7 is not of type 'string'"]
    v2 = sr.check_contract(POOL_A2A, {"from": "r", "value": {}})
    assert v2["errors"] == ["(raiz): 'linha' is a required property"]


def test_schema_malformado_nao_derruba():
    v = sr.check_contract({"a2a": {"output_schema": {"type": "nao-existe"}}}, {"from": "r", "value": 1})
    assert v["checked"] is False and v["reason"] == "schema_invalid"


async def test_grava_documento_com_ttl_da_sessao_e_avisa_invalido(caplog):
    redis = AsyncMock()
    async def _pool(t, p):
        assert (t, p) == (TENANT, POOL)
        return POOL_A2A
    with caplog.at_level(logging.WARNING, logger="plughub.bridge.session_result"):
        doc = await sr.persist_session_result(
            redis, tenant_id=TENANT, session_id=SID, pool_id=POOL, skill_id=SKILL, deploy_version="v1",
            terminal={"step_id": "fim", "outcome": "resolved", "issue_status": "ok",
                      "result": {"from": "r", "value": {"linha": 7}}},
            ttl_s=14400, fetch_pool=_pool)
    chave, valor = redis.set.call_args.args
    assert chave == f"{TENANT}:session:{SID}:result" and redis.set.call_args.kwargs == {"ex": 14400}
    gravado = json.loads(valor)
    assert gravado == doc
    assert (gravado["outcome"], gravado["issue_status"], gravado["step_id"]) == ("resolved", "ok", "fim")
    assert gravado["result"] == {"from": "r", "value": {"linha": 7}}       # o inválido NÃO é descartado
    assert gravado["contract"]["valid"] is False
    assert any("NÃO cumpre o output_schema" in r.getMessage() for r in caplog.records)


async def test_falha_de_escrita_e_error_e_nao_levanta(caplog):
    redis = AsyncMock()
    redis.set.side_effect = RuntimeError("redis fora")
    with caplog.at_level(logging.ERROR, logger="plughub.bridge.session_result"):
        doc = await sr.persist_session_result(
            redis, tenant_id=TENANT, session_id=SID, pool_id="", skill_id=SKILL, deploy_version="",
            terminal={"step_id": "fim", "outcome": "resolved"}, ttl_s=10, fetch_pool=AsyncMock())
    assert doc is None and any("não gravei" in r.getMessage() for r in caplog.records)


# ── o ponto de ligação: activate_native_agent ────────────────────────────────

def _ctx(resp):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm


def _http(execute_body):
    def _post(url, json=None, headers=None, timeout=None):
        r = MagicMock()
        r.status = 200
        r.json = AsyncMock(return_value=execute_body if url.endswith("/execute") else {})
        r.text = AsyncMock(return_value="")
        return _ctx(r)
    http = AsyncMock()
    http.post = MagicMock(side_effect=_post)
    return http


async def _ativa(execute_body, **kw):
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    persist = AsyncMock()
    flow = {"entry": "a", "steps": [{"id": "a", "type": "complete", "outcome": "resolved"}]}
    with patch.object(bridge_mod, "resolve_flow_for_agent", new_callable=AsyncMock, return_value=(SKILL, flow)), \
         patch.object(bridge_mod, "_resolve_journey_root", new_callable=AsyncMock, return_value=""), \
         patch.object(bridge_mod, "mint_session_token", new_callable=AsyncMock, return_value=""), \
         patch.object(bridge_mod._session_result, "persist_session_result", persist):
        await bridge_mod.activate_native_agent(
            http=_http(execute_body), redis_client=redis, session_id=SID, customer_id="c",
            agent_type_id=SKILL, tenant_id=TENANT, skills=[], instance_id="i", pool_id=POOL, **kw)
    return persist


TERMINAL = {"step_id": "fim", "outcome": "resolved", "result": {"from": "r", "value": {"linha": "1"}}}


async def test_principal_que_termina_em_complete_grava():
    persist = await _ativa({"outcome": "resolved", "terminal": TERMINAL, "deploy_version": "dv"})
    persist.assert_awaited_once()
    kw = persist.await_args.kwargs
    assert (kw["tenant_id"], kw["session_id"], kw["pool_id"], kw["skill_id"]) == (TENANT, SID, POOL, SKILL)
    assert kw["terminal"] == TERMINAL and kw["deploy_version"] == "dv"
    assert kw["ttl_s"] == bridge_mod._stl()


async def test_conferencia_nao_fala_pela_sessao():
    persist = await _ativa({"outcome": "resolved", "terminal": TERMINAL}, conference_id="conf-1")
    persist.assert_not_awaited()


async def test_fim_que_nao_veio_de_complete_nao_grava():
    persist = await _ativa({"outcome": "escalated_human"})
    persist.assert_not_awaited()
