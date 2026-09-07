# -*- coding: utf-8 -*-
"""O contato acaba com este outcome? UM juiz, e ele foi achado por contato real.

⚠️ **O caso que carrega peso é `escalated_human`**, e a razão é histórica: a
decisão vivia em DUAS casas e elas divergiam exatamente nele.

    process_routed   ("escalated_human","escalated_ai","transferred","suspended")
    session_resumed  != "suspended"                       <- um elemento só

O caminho do resume nasceu para o Arc 19, onde workflow retomado termina em
`complete`. Fluxo retomado que termina em `escalate` **não existia** até a RET-02
dar `delegate` ao orquestrador. A partir dali:

    escalate SEM delegate antes  ->  process_routed   ->  contato SEGUE   OK
    escalate DEPOIS de delegar   ->  session_resumed  ->  contato FECHA   BUG

Medido no contato `13484fe6` (2026-09-07): o contato entrou na fila humana e foi
retirado dela 30 ms depois (`outcome=abandoned wait_ms=21`). Para quem olhava a
tela, o atendimento "encerrou".
"""
import pytest

from plughub_orchestrator_bridge.main import (
    contato_encerra_com,
    _OUTCOMES_QUE_NAO_FECHAM,
)


# ── Os que NAO fecham — a sessao continua com outro agente ───────────────────

@pytest.mark.parametrize("outcome", [
    "escalated_human",   # o caso medido: o `escalate` ja enfileirou o destino
    "escalated_ai",
    "transferred",
    "suspended",         # Arc 19: a sessao persiste no Redis aguardando resume
])
def test_outcome_de_continuidade_nao_encerra(outcome):
    assert contato_encerra_com(outcome) is False


# ── Os que fecham ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("outcome", [
    "resolved", "failed", "abandoned", "timeout", "cancelled", "escalated",
])
def test_outcome_terminal_encerra(outcome):
    assert contato_encerra_com(outcome) is True


def test_ausencia_ENCERRA_e_isso_e_decisao():
    """Outcome vazio e fluxo que terminou sem se declarar.

    Manter o contato aberto ali o deixaria pendurado ate o TTL da sessao com o
    cliente olhando uma tela muda — o caminho barulhento e fechar. Este teste
    existe para que a escolha nao seja invertida por acidente ao mexer no
    predicado.
    """
    assert contato_encerra_com("") is True
    assert contato_encerra_com(None) is True


# ── O CASO QUE CARREGA PESO ──────────────────────────────────────────────────

def test_escalated_human_nao_encerra__a_divergencia_que_custou_um_contato():
    """Se este virar `True`, o contato volta a fechar antes de a fila recebe-lo.

    ⚠️ `escalated` (sem sufixo) ENCERRA e `escalated_human` NAO — os dois estao
    aqui de proposito, lado a lado: uma implementacao por `startswith` passaria
    no resto da suite e mudaria o desfecho de `escalated`, que e o outcome com
    que um fluxo declara que terminou escalando sem transferir a sessao.
    """
    assert contato_encerra_com("escalated_human") is False
    assert contato_encerra_com("escalated") is True


def test_a_lista_cobre_os_quatro__ninguem_a_apara_em_silencio():
    """A lista curta (`!= "suspended"`) foi o defeito. Este teste a fixa."""
    assert set(_OUTCOMES_QUE_NAO_FECHAM) == {
        "escalated_human", "escalated_ai", "transferred", "suspended",
    }
