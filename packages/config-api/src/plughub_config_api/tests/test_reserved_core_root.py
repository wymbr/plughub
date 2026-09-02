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

from plughub_config_api.router import _reject_tenant_core_root, RESERVED_CONTEXT_ROOT

TENANT = "tenant_probe"


def _mapa(*roots: str) -> dict:
    return {
        "mode": "audit",
        "dynamic_prefixes": ["agent."],
        "contexto": {r: {"dom": {"campo": {"tipo": "texto"}}} for r in roots},
    }


def test_c1_recusa_root_core_vindo_de_tenant() -> None:
    with pytest.raises(HTTPException) as e:
        _reject_tenant_core_root("masking", "context_map", TENANT, _mapa("core"))
    assert e.value.status_code == 422
    # A recusa tem de NOMEAR o que fazer. Um 422 mudo manda o autor adivinhar, e o
    # caminho certo (`session.*`, `journey.*`, outro root) não é óbvio de fora.
    assert RESERVED_CONTEXT_ROOT in str(e.value.detail)
    assert "session.*" in str(e.value.detail)
    assert TENANT in str(e.value.detail)


def test_c2_testemunha_o_tenant_segue_escrevendo_o_que_e_dele() -> None:
    """Sem este caso, um portão que recusasse TUDO passaria no C-1."""
    _reject_tenant_core_root("masking", "context_map", TENANT, _mapa("session", "journey", "card"))


def test_c3_a_plataforma_escreve_core_no_global() -> None:
    """O seed é o dono do root. Recusá-lo aqui quebraria o provisionamento."""
    _reject_tenant_core_root("masking", "context_map", None, _mapa("core"))
    _reject_tenant_core_root("masking", "context_map", "__global__", _mapa("core"))


def test_c4_outras_chaves_do_namespace_nao_sao_assunto_deste_portao() -> None:
    _reject_tenant_core_root("masking", "types", TENANT, {"types": [{"id": "core"}]})
    _reject_tenant_core_root("masking", "context_rules", TENANT, {"rules": []})
    _reject_tenant_core_root("routing", "context_map", TENANT, _mapa("core"))


@pytest.mark.parametrize("valor", [None, "", 42, [], {"contexto": None}, {"contexto": []}, {}])
def test_c5_payload_estranho_nao_explode(valor) -> None:
    """O portão roda ANTES do store, sobre corpo ainda não normalizado. Se ele levantar
    `TypeError`/`AttributeError` num payload torto, o 422 vira 500 e a recusa deixa de
    ser diagnosticável — o autor lê "erro interno" onde havia uma regra."""
    _reject_tenant_core_root("masking", "context_map", TENANT, valor)
