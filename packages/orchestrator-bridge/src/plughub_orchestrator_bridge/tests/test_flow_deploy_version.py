# -*- coding: utf-8 -*-
"""SFE-03 — o segmento carimba a versão que o engine EXECUTOU, não o `current` do pool.

Com o pin, uma sessão retomada depois de um promote roda a versão em que nasceu, e o
`_pool_deploy_version_cache` do bridge já está na nova. O carimbo do cache faria o
segmento afirmar a versão que ele não rodou. Três partes, e só juntas reprovam:

  1. o leitor (`_flow_deploy_version`) — devolve o executado; ausente ⇒ ``""`` (o cache
     volta a responder, como antes do pin);
  2. a fiação — todo `participant_left` nativo que nasce de um RunResult do engine passa
     `deploy_version=_flow_deploy_version(...)`. O G5 (especialista externo) fica de fora
     porque o evento dele vem do mcp-server, não de um run do engine;
  3. o lançamento — `activate_native_agent` manda a identidade resolvida no payload,
     senão o engine fixa ``""`` e o log de retomada não sabe dizer que o pool mudou.
"""
import ast
import inspect

import pytest

from plughub_orchestrator_bridge import main
from plughub_orchestrator_bridge.main import _flow_deploy_version


# ── 1. o leitor ──────────────────────────────────────────────────────────────

def test_devolve_a_versao_executada():
    assert _flow_deploy_version({"outcome": "resolved", "deploy_version": "2026-08-12T21:24:21.972Z"}) \
        == "2026-08-12T21:24:21.972Z"


@pytest.mark.parametrize("agent_result", [None, {}, {"outcome": "resolved"}, {"deploy_version": 42}])
def test_ausente_e_vazio__o_cache_volta_a_responder(agent_result):
    assert _flow_deploy_version(agent_result) == ""


# ── 2. a fiação ──────────────────────────────────────────────────────────────

def _publicadores_de_run_do_engine():
    arvore = ast.parse(inspect.getsource(main))
    for no in ast.walk(arvore):
        if not (isinstance(no, ast.Call) and getattr(no.func, "id", "") == "_publish_participant_event"):
            continue
        kw = {k.arg: k.value for k in no.keywords if k.arg}
        tipo, agente = kw.get("event_type"), kw.get("agent_type")
        if not (isinstance(tipo, ast.Constant) and tipo.value == "participant_left"
                and isinstance(agente, ast.Constant) and agente.value == "native"):
            continue
        # G5: o motivo vem de `msg` (evento do mcp-server), não de um RunResult.
        iss = kw.get("issue_status")
        if (isinstance(iss, ast.Call) and iss.args and isinstance(iss.args[0], ast.Name)
                and iss.args[0].id == "msg"):
            continue
        yield no.lineno, kw


def test_ha_publicadores_para_conferir():
    """Testemunha: uma AST que não achasse nada passaria no teste abaixo."""
    assert len(list(_publicadores_de_run_do_engine())) >= 3


def test_todo_publicador_carimba_a_versao_executada():
    faltando = []
    for linha, kw in _publicadores_de_run_do_engine():
        v = kw.get("deploy_version")
        if not (isinstance(v, ast.Call) and getattr(v.func, "id", "") == "_flow_deploy_version"):
            faltando.append(linha)
    assert faltando == [], (
        f"participant_left nativo sem deploy_version=_flow_deploy_version(...) nas linhas "
        f"{faltando} — numa retomada o segmento afirmaria a versão que NÃO rodou (SFE-03)"
    )


# ── 3. o lançamento ──────────────────────────────────────────────────────────

def test_activate_manda_a_identidade_do_deploy():
    fonte = ast.parse(inspect.getsource(main.activate_native_agent))
    chaves = {
        alvo.slice.value
        for no in ast.walk(fonte) if isinstance(no, ast.Assign)
        for alvo in no.targets
        if isinstance(alvo, ast.Subscript) and isinstance(alvo.value, ast.Name)
        and alvo.value.id == "payload" and isinstance(alvo.slice, ast.Constant)
    }
    assert "deploy_version" in chaves, "o engine fixaria a versão sem identidade"
    assert "config" in chaves, "testemunha: a leitura da AST achou as outras chaves do payload"
