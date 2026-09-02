# -*- coding: utf-8 -*-
"""CNS-05 — o root `core.*` é reservado à plataforma.

── O que foi MEDIDO antes de existir portão (2026-09-01) ────────────────────────

Um `PUT /config/masking/context_map` com `tenant_id` de tenant, declarando
`contexto.core.contact.invadido`, voltava **HTTP 200**. E a leitura de volta mostrava
que o dano era maior que a invasão: o override SUBSTITUI a chave inteira, então aquele
tenant passava a ter **1 folha no lugar das 94** da plataforma. Declarar no `core` e
apagar o mapa eram o mesmo gesto.

Até aqui a reserva existia em decisão (CNS-02), em documentação (`CLAUDE.md`, a spec) e
em nada mais — "promessa sem mecanismo", que é a família que este repositório cataloga.

── Por que estes casos, e não outros ───────────────────────────────────────────

O par que dá valor ao portão é **C-1 × C-2**: recusar o `core` de tenant só significa
alguma coisa junto com a prova de que o tenant continua escrevendo o que é dele. Um
portão que recusasse tudo passaria em C-1 sozinho.

C-3 e C-4 guardam a fronteira do escopo — o seed da plataforma escreve `core` no
`__global__` e **tem de continuar podendo**, senão o portão quebra o provisionamento;
e outras chaves do namespace `masking` não são assunto deste portão.
"""
import pytest
from fastapi import HTTPException

from plughub_config_api.router import _reject_tenant_context_map, RESERVED_CONTEXT_ROOT

TENANT = "tenant_probe"


def _mapa(*roots: str) -> dict:
    return {
        "mode": "audit",
        "dynamic_prefixes": ["agent."],
        "contexto": {r: {"dom": {"campo": {"tipo": "texto"}}} for r in roots},
    }


def test_c1_recusa_o_mapa_com_root_core_vindo_de_tenant() -> None:
    with pytest.raises(HTTPException) as e:
        _reject_tenant_context_map("masking", "context_map", TENANT, _mapa("core"))
    assert e.value.status_code == 422
    # A recusa tem de NOMEAR o que fazer. Um 422 mudo manda o autor adivinhar, e o
    # caminho certo (`session.*`, `journey.*`, outro root) não é óbvio de fora.
    assert TENANT in str(e.value.detail)
    # A recusa tem de dizer POR QUE, senão o autor tenta de novo com outro root.
    assert "tenant-vence-global" in str(e.value.detail)


def test_c2_a_recusa_e_TOTAL_nesta_chave_mesmo_com_root_proprio() -> None:
    """⚠️ Este caso INVERTEU na CNS-08, e a inversão é a decisão.

    Sob a CNS-05 ele afirmava o oposto — que o tenant seguia escrevendo o que era dele.
    Medindo o mecanismo por inteiro, o dano não dependia do root: a resolução de config
    é `LIMIT 1` (tenant vence o global POR INTEIRO), então QUALQUER override substitui
    as 94 folhas da plataforma. Escrever `session.card` aqui não acrescenta ao mapa —
    apaga o resto dele para aquele tenant.

    A testemunha de que o portão não recusa tudo mudou de casa: é o C-4 (outras chaves
    do mesmo namespace passam) somado ao C-3 (a plataforma escreve no global)."""
    with pytest.raises(HTTPException) as e:
        _reject_tenant_context_map("masking", "context_map", TENANT, _mapa("session", "journey", "card"))
    assert e.value.status_code == 422
    assert "override por tenant" in str(e.value.detail)


def test_c3_a_plataforma_escreve_core_no_global() -> None:
    """O seed é o dono do root. Recusá-lo aqui quebraria o provisionamento."""
    _reject_tenant_context_map("masking", "context_map", None, _mapa("core"))
    _reject_tenant_context_map("masking", "context_map", "__global__", _mapa("core"))


def test_c4_outras_chaves_do_namespace_nao_sao_assunto_deste_portao() -> None:
    _reject_tenant_context_map("masking", "types", TENANT, {"types": [{"id": "core"}]})
    _reject_tenant_context_map("masking", "context_rules", TENANT, {"rules": []})
    _reject_tenant_context_map("routing", "context_map", TENANT, _mapa("core"))


@pytest.mark.parametrize("valor", [None, "", 42, [], {"contexto": None}, {"contexto": []}, {}])
def test_c5_a_recusa_e_por_ESCOPO_e_nenhum_payload_a_transforma_em_500(valor) -> None:
    """⚠️ Este caso também mudou de proposição na CNS-08, e vale dizer o que ele mede.

    Sob a CNS-05 o portão INSPECIONAVA o valor (procurava o root `core`), e o risco era
    estourar `TypeError` num corpo ainda não normalizado — o 422 virando 500, e a recusa
    deixando de ser diagnosticável. Agora ele decide por ESCOPO e não olha o valor, então
    o risco mudou de forma: o que se afirma aqui é que **nenhum formato de payload muda o
    desfecho**, nem para 500 nem para "passou".

    O caso continua valendo justamente porque a inspeção pode VOLTAR — no dia da chave de
    tenant (CNS-16) o portão volta a olhar conteúdo, e aí este teste é a rede."""
    with pytest.raises(HTTPException) as e:
        _reject_tenant_context_map("masking", "context_map", TENANT, valor)
    assert e.value.status_code == 422
