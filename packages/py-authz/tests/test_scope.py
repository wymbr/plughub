"""
Tabela-verdade do resolvedor de ESCOPO de pool.

POR QUE ESTA SUÍTE É SEPARADA DA `test_authz.py`
================================================
São dois eixos, e confundi-los já custou um defeito: em 2026-08-27 o claim
`unrestricted` (ESCOPO — *"quais linhas eu alcanço"*) chegou a liberar o menu
(CAPACIDADE — *"quais funções eu exerço"*), e `probe@`, com zero grants, passou a
enxergar o módulo de Auditoria LGPD. Arquivo separado é o lembrete estrutural.

O QUE ELA PROTEGE
=================
Havia TRÊS cópias deste resolvedor (`analytics-api/pool_auth`, `channel-gateway/auth`,
`evaluation-api/router`), e o `probe_authz_single_verifier.sh` não contava nenhuma —
ele conta quem decodifica JWT, e estas três só consomem claims já decodificados.

O ponto de urgência é o **passo 3** do plano de `accessible_pools`, que inverte o
significado de `[]` (hoje "todos", depois "nenhum"). Por isso os testes abaixo cobrem
os **DOIS** estados do interruptor `LEGACY_EMPTY_MEANS_UNRESTRICTED`: no dia da
inversão não se descobre a tabela-verdade, ela já está escrita — vira-se a flag e o
que era `xfail` conceitual passa a ser o caminho vivo.
"""
from __future__ import annotations

import logging

import pytest

import plughub_authz
from plughub_authz import LEGACY_UNRESTRICTED_MARK, pool_in_scope, resolve_scope


@pytest.fixture
def legado_ligado(monkeypatch):
    """Estado de HOJE: `[]` significa todos os pools."""
    monkeypatch.setattr(plughub_authz, "LEGACY_EMPTY_MEANS_UNRESTRICTED", True)


@pytest.fixture
def legado_invertido(monkeypatch):
    """Estado do PASSO 3: `[]` significa nenhum pool."""
    monkeypatch.setattr(plughub_authz, "LEGACY_EMPTY_MEANS_UNRESTRICTED", False)


# ── ramo 1: o RESTRITIVO vence, sempre ───────────────────────────────────────

def test_lista_nao_vazia_decide(legado_ligado):
    assert resolve_scope({"accessible_pools": ["sac", "retencao"]}, "t") == ["sac", "retencao"]


def test_lista_nao_vazia_VENCE_o_unrestricted(legado_ligado):
    """O ramo mais importante da ordem, e o motivo de ele vir primeiro.

    Um `unrestricted` setado por engano não pode ALARGAR o domínio de um operador
    escopado: alargamento não aparece na tela como erro — aparece como linhas a mais
    num relatório que ninguém confere. O oposto (restringir demais) o usuário reclama
    no mesmo dia.
    """
    claims = {"accessible_pools": ["sac"], "unrestricted": True}
    assert resolve_scope(claims, "t") == ["sac"]
    assert pool_in_scope(claims, "outro") is False


def test_lista_e_copiada_nao_aliasada(legado_ligado):
    """O chamador não pode mutar o claim por acidente (a analytics adiciona os
    espelhos `-int` em cima do resultado)."""
    claims = {"accessible_pools": ["sac"]}
    out = resolve_scope(claims, "t")
    out.append("invadido")
    assert claims["accessible_pools"] == ["sac"]


# ── o claim REMOVIDO (2026-08-31) ────────────────────────────────────────────
# Sob ABAC total nao existe porta larga POR CLAIM: pools sao do TENANT (criados pelo
# usuario), nao da plataforma, entao escopo de usuario e sempre ENUMERADO. `None`
# sobrevive so para principal de SISTEMA, construido explicitamente.

def test_claim_unrestricted_nao_concede_com_legado_DESLIGADO(legado_invertido):
    """A testemunha que importa. Com o legado desligado, o token que ainda carregue
    `unrestricted: True` e lista vazia recebe **nenhum pool** — nao o tenant inteiro.

    E este teste que fica VERMELHO se alguem reintroduzir o ramo. Sem ele a volta da
    porta larga passaria despercebida, porque com o legado LIGADO os dois desenhos
    devolvem `None` pelo mesmo caminho.
    """
    assert resolve_scope({"unrestricted": True, "accessible_pools": []}, "t") == []


@pytest.mark.parametrize("valor", [True, "true", 1, "1", "yes", {}, [], None])
def test_nenhum_valor_do_claim_concede(legado_invertido, valor):
    """Nenhum valor concede — `True` inclusive. O claim nao e mais lido."""
    assert resolve_scope({"unrestricted": valor, "accessible_pools": []}, "t") == []

# ── o legado, CONTADO (cai na AUT-03) ────────────────────────────────────────────────

def test_legado_devolve_irrestrito_E_AVISA(legado_ligado, caplog):
    with caplog.at_level(logging.WARNING, logger="plughub.authz"):
        assert resolve_scope({"sub": "u_velho", "accessible_pools": []}, "header") is None

    avisos = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert avisos, "ramo legado passou EM SILENCIO — o passo 3 perde o inventario"
    msg = avisos[0].getMessage()
    assert LEGACY_UNRESTRICTED_MARK in msg
    assert "header" in msg, "log nao nomeia a origem: " + repr(msg)
    assert "u_velho" in msg, "log nao nomeia o usuario a decidir: " + repr(msg)


def test_log_distingue_claim_ausente_de_claim_false(legado_ligado, caplog):
    """Duas populações diferentes, e só a segunda é decisão de alguém: token VELHO
    (emissor não conhecia o claim) × usuário que de fato não tem escopo declarado."""
    with caplog.at_level(logging.WARNING, logger="plughub.authz"):
        resolve_scope({"sub": "a"}, "x")                            # claim ausente
        resolve_scope({"sub": "b", "unrestricted": False}, "x")     # claim presente/false
    msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(msgs) == 2
    assert "claim_presente=False" in msgs[0]
    assert "claim_presente=True" in msgs[1]


def test_claims_ausentes_nao_explodem(legado_ligado):
    """Chamadores com Bearer opcional passam `None` (evaluation-api faz isso)."""
    assert resolve_scope(None, "t") is None
    assert pool_in_scope(None, "sac") is True


# ── o PASSO 3, já escrito ────────────────────────────────────────────────────

def test_passo3_lista_vazia_passa_a_significar_NENHUM(legado_invertido, caplog):
    """Com o interruptor virado, a MESMA entrada devolve domínio vazio.

    E não avisa: depois da inversão, "sem recorte declarado ⇒ nenhum pool" é a
    resposta correta, não uma degradação. O sintoma vira relatório vazio, que o
    usuário reclama no mesmo dia — o oposto do vazamento mudo.
    """
    with caplog.at_level(logging.WARNING, logger="plughub.authz"):
        assert resolve_scope({"sub": "u", "accessible_pools": []}, "header") == []
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_passo3_nao_toca_quem_ja_e_explicito(legado_invertido):
    """A inversão só alcança o ramo legado. Quem declarou escopo responde igual antes
    e depois — é isso que a torna aplicável. (O `unrestricted` saiu em 2026-08-31 e
    não responde mais nada; a testemunha disso está acima.)"""
    assert resolve_scope({"accessible_pools": ["sac"]}, "t") == ["sac"]


def test_passo3_pool_in_scope_fecha(legado_invertido):
    assert pool_in_scope({"sub": "u", "accessible_pools": []}, "sac") is False


# ── pool_in_scope ────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("claims", "pool", "esperado"),
    [
        ({"accessible_pools": ["sac"]},              "sac",   True),
        ({"accessible_pools": ["sac"]},              "outro", False),
        ({"accessible_pools": ["sac", "retencao"]},  "retencao", True),
        ({"unrestricted": True},                     "qualquer", True),
    ],
)
def test_pool_in_scope_tabela(legado_ligado, claims, pool, esperado):
    assert pool_in_scope(claims, pool) is esperado
