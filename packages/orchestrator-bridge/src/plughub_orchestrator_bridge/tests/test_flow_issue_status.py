# -*- coding: utf-8 -*-
"""SFE-02 — o `issue_status` declarado no `complete` chega ao segmento de IA.

Medido em 2026-09-17/25: 0 segmentos `native`/`ai` com `issue_status`, contra 37 de 96
`complete` do repositório que o declaravam. O campo morria no parse do schema; depois do
conserto ele viaja no RunResult do engine, e este arquivo cobre a última ponta: o bridge.

Duas partes, e só as duas juntas reprovam o defeito:

  1. o leitor (`_flow_issue_status`) — devolve o declarado e NUNCA inventa um default;
  2. a fiação — TODO `participant_left` nativo passa `issue_status=`. Conferido pela AST
     da chamada, não por texto: um grep acharia o nome num comentário e ficaria verde.
     São QUATRO publicadores (ativação, agente de fila, janela de resume do webhook e o
     especialista de conferência externo, G5). O quarto foi achado por ESTE teste, depois
     de o conserto ter coberto os três que a leitura do código tinha encontrado.
"""
import ast
import inspect

import pytest

from plughub_orchestrator_bridge import main
from plughub_orchestrator_bridge.main import _flow_issue_status


# ── 1. o leitor ──────────────────────────────────────────────────────────────

def test_devolve_o_motivo_declarado():
    assert _flow_issue_status({"outcome": "failed", "issue_status": "outbound_drain_failed"}) \
        == "outbound_drain_failed"


@pytest.mark.parametrize("agent_result", [
    None,
    {},
    {"outcome": "resolved"},
    {"outcome": "resolved", "issue_status": ""},
    {"outcome": "resolved", "issue_status": None},
    {"outcome": "resolved", "issue_status": 42},
])
def test_ausente_ou_invalido_e_NONE__nunca_um_default(agent_result):
    """`None` é "não declarado". Um default plausível ("resolvido", "ok") faria o
    relatório contar como explicado um fechamento que não disse nada."""
    assert _flow_issue_status(agent_result) is None


# ── 2. a fiação ──────────────────────────────────────────────────────────────

def _chamadas_participant_left_nativo():
    arvore = ast.parse(inspect.getsource(main))
    for no in ast.walk(arvore):
        if not (isinstance(no, ast.Call) and getattr(no.func, "id", "") == "_publish_participant_event"):
            continue
        kw = {k.arg: k.value for k in no.keywords if k.arg}
        tipo = kw.get("event_type")
        agente = kw.get("agent_type")
        if (isinstance(tipo, ast.Constant) and tipo.value == "participant_left"
                and isinstance(agente, ast.Constant) and agente.value == "native"):
            yield no.lineno, kw


def test_ha_publicadores_nativos_para_conferir():
    """Testemunha: sem ela, uma AST que não achasse chamada nenhuma passaria abaixo."""
    assert len(list(_chamadas_participant_left_nativo())) >= 4


def test_todo_participant_left_nativo_passa_issue_status():
    faltando = [linha for linha, kw in _chamadas_participant_left_nativo() if "issue_status" not in kw]
    assert faltando == [], (
        f"participant_left nativo sem issue_status nas linhas {faltando} de main.py — "
        "o motivo declarado no `complete` morre ali (SFE-02)"
    )
