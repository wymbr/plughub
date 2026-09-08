"""
test_grants.py — o guard de RANK (MOD-02 / E2 da emenda de 2026-09-08).

⚠️ O QUE ESTES TESTES PRECISAM PROVAR, E POR QUE O NEGATIVO SOZINHO NAO BASTA
-----------------------------------------------------------------------------
A regra da casa, escrita depois de cinco dos sete passos do arco ABAC terem os
testes ao redor da fronteira quebrados: *"ao fechar um portao, escreva o caso que
prova que ele DEIXA ALGUEM PASSAR — o negativo sozinho passa pelo motivo errado"*.
Um `violacoes(...) != []` fica verde se o predicado devolver violacao para tudo,
inclusive para o master. Por isso cada recusa aqui tem uma permissao ao lado.
"""
from __future__ import annotations

from plughub_auth_api import grants


def _claims(module_config=None, pools=None):
    return {"module_config": module_config or {}, "accessible_pools": pools or []}


MASTER = _claims({"config": {"permissions": {"access": "read_write", "scope": []}}})
# Delegado tipico: administra pessoas, atende contato, ve relatorio.
DELEGADO = _claims(
    {
        "config": {"users": {"access": "read_write", "scope": []}},
        "contacts": {"operacao": {"access": "read_write", "scope": []},
                     "visualizar": {"access": "read_only", "scope": []}},
        "evaluation": {"report": {"access": "read_only", "scope": []}},
    },
    pools=["sac", "retencao"],
)


# ── master: passa por tudo (TESTEMUNHA DE PRESENCA) ───────────────────────────

def test_master_concede_qualquer_campo():
    assert grants.e_master(MASTER)
    assert grants.violacoes(
        MASTER,
        module_config={"billing": {"gerenciar": {"access": "read_write", "scope": []}}},
        accessible_pools=["qualquer_pool"],
    ) == []


def test_delegado_nao_e_master():
    assert not grants.e_master(DELEGADO)


# ── rank ──────────────────────────────────────────────────────────────────────

def test_concede_o_que_detem_no_mesmo_nivel():
    """A permissao que faz o resto valer: sem ela, um predicado que nega tudo passa."""
    assert grants.violacoes(
        DELEGADO,
        module_config={"contacts": {"operacao": {"access": "read_write", "scope": []}}},
    ) == []


def test_recusa_nivel_acima_do_proprio():
    fora = grants.violacoes(
        DELEGADO,
        module_config={"contacts": {"visualizar": {"access": "read_write", "scope": []}}},
    )
    assert len(fora) == 1
    # A recusa NOMEIA o campo e os dois lados — "forbidden" seco manda adivinhar
    # qual dos 44 campos barrou.
    assert "contacts.visualizar" in fora[0] and "read_only" in fora[0]


def test_recusa_campo_que_nao_detem():
    fora = grants.violacoes(
        DELEGADO,
        module_config={"billing": {"gerenciar": {"access": "read_write", "scope": []}}},
    )
    assert len(fora) == 1 and "billing.gerenciar" in fora[0]


def test_conceder_none_nao_e_conceder():
    assert grants.violacoes(
        DELEGADO,
        module_config={"billing": {"gerenciar": {"access": "none", "scope": []}}},
    ) == []


def test_access_desconhecido_recusa_nas_duas_pontas():
    """Rank desconhecido = 0. Do lado de quem concede, nega; do lado de quem detem,
    tambem — o oposto do `.get(x, 0)` da divergencia 4, que LIBERAVA."""
    assert grants.violacoes(
        DELEGADO,
        module_config={"contacts": {"operacao": {"access": "full_access", "scope": []}}},
    ) == []  # rank 0 no pretendido: nao concede nada, logo nada a barrar
    esquisito = _claims({"contacts": {"operacao": {"access": "full_access", "scope": []}}})
    fora = grants.violacoes(
        esquisito,
        module_config={"contacts": {"operacao": {"access": "read_only", "scope": []}}},
    )
    assert len(fora) == 1  # quem "detem" valor desconhecido nao alcanca nada


# ── a maquina de conceder se protege sozinha, sem lista de excecao ────────────

def test_delegado_nao_concede_config_permissions():
    fora = grants.violacoes(
        DELEGADO,
        module_config={"config": {"permissions": {"access": "read_write", "scope": []}}},
    )
    assert len(fora) == 1 and "config.permissions" in fora[0]


def test_propagacao_de_config_users_e_permitida_e_isso_e_decidido():
    """Nao e escalacao: ninguem passa do proprio teto. O numero de portadores cresce,
    o conjunto de capacidades do tenant nao. Quem torna isso visivel e o censo."""
    assert grants.violacoes(
        DELEGADO,
        module_config={"config": {"users": {"access": "read_write", "scope": []}}},
    ) == []


# ── o EFEITO, nunca o literal: `roles` expande em preset ──────────────────────

PRESETS = {
    "admin": {"config": {"permissions": {"access": "read_write", "scope": []}}},
    "operator": {"contacts": {"operacao": {"access": "read_write", "scope": []}}},
}


def test_role_admin_nao_atravessa_com_module_config_vazio():
    """O furo que o guard fecharia sem ver: `{role: admin, module_config: {}}`."""
    fora = grants.violacoes(
        DELEGADO, module_config={}, roles=["admin"], presets=PRESETS,
    )
    assert len(fora) == 1 and "config.permissions" in fora[0]


def test_role_dentro_do_proprio_teto_passa():
    assert grants.violacoes(
        DELEGADO, roles=["operator"], presets=PRESETS,
    ) == []


# ── escopo: `[]` = GLOBAL aqui, e NENHUM em accessible_pools ──────────────────

ESCOPADO = _claims(
    {"config": {"users": {"access": "read_write", "scope": []}},
     "evaluation": {"revisar": {"access": "read_write", "scope": ["pool:sac"]}}},
    pools=["sac"],
)


def test_escopo_contido_passa():
    assert grants.violacoes(
        ESCOPADO,
        module_config={"evaluation": {"revisar": {"access": "read_write",
                                                 "scope": ["pool:sac"]}}},
    ) == []


def test_conceder_global_tendo_recorte_recusa():
    fora = grants.violacoes(
        ESCOPADO,
        module_config={"evaluation": {"revisar": {"access": "read_write", "scope": []}}},
    )
    assert len(fora) == 1 and "GLOBAL" in fora[0]


def test_escopo_fora_do_proprio_recusa():
    fora = grants.violacoes(
        ESCOPADO,
        module_config={"evaluation": {"revisar": {"access": "read_write",
                                                 "scope": ["pool:outro"]}}},
    )
    assert len(fora) == 1 and "outro" in fora[0]


def test_alias_pool_prefixado_e_cru_sao_o_mesmo():
    assert grants.violacoes(
        ESCOPADO,
        module_config={"evaluation": {"revisar": {"access": "read_write",
                                                 "scope": ["sac"]}}},
    ) == []


# ── accessible_pools: semantica OPOSTA para lista vazia ──────────────────────

def test_pools_subconjunto_passa():
    assert grants.violacoes(DELEGADO, accessible_pools=["sac"]) == []


def test_pools_fora_do_escopo_recusa():
    fora = grants.violacoes(DELEGADO, accessible_pools=["sac", "cobranca"])
    assert len(fora) == 1 and "cobranca" in fora[0]


def test_pools_vazio_nao_concede_nada_e_nao_viola():
    """`[]` em accessible_pools e NENHUM pool (AUT-03) — conceder isso nao excede."""
    assert grants.violacoes(DELEGADO, accessible_pools=[]) == []


def test_quem_nao_tem_pool_nenhum_nao_concede_pool():
    sem_pool = _claims({"config": {"users": {"access": "read_write", "scope": []}}})
    fora = grants.violacoes(sem_pool, accessible_pools=["sac"])
    assert len(fora) == 1 and "nenhum" in fora[0]


# ── `atual`: manter o que ja existe nao e conceder ───────────────────────────

def test_campo_acima_do_proprio_passa_se_ja_estava():
    """Sem isto, o delegado nao consegue editar NINGUEM que tenha um grant acima do
    dele — nem para mexer num campo que ele alcanca —, porque o PUT reenvia o config
    inteiro. O que se julga e o AUMENTO."""
    atual = {"billing": {"gerenciar": {"access": "read_write", "scope": []}}}
    assert grants.violacoes(
        DELEGADO,
        module_config={"billing": {"gerenciar": {"access": "read_write", "scope": []}},
                       "contacts": {"operacao": {"access": "read_write", "scope": []}}},
        atual=atual,
    ) == []


def test_aumentar_campo_que_ja_existia_ainda_recusa():
    atual = {"contacts": {"visualizar": {"access": "read_only", "scope": []}}}
    fora = grants.violacoes(
        DELEGADO,
        module_config={"contacts": {"visualizar": {"access": "read_write", "scope": []}}},
        atual=atual,
    )
    assert len(fora) == 1 and "contacts.visualizar" in fora[0]
