"""
presets.py — o PAPEL como preset de nascimento, nao como portao.

POR QUE ISTO EXISTE
-------------------
Medido em 2026-08-27: `create_user` gravava `roles`, `accessible_pools`, `unrestricted`
e `max_concurrent_sessions` — e NAO gravava `module_config`. Todo usuario criado pela
tela nascia com config vazio, ou seja, dentro da degradacao graciosa (`passesAbacRule`
libera quando o config esta vazio). O menu "funcionava" porque o buraco o sustentava.

Inverter essa degradacao (config vazio = nao pode nada) sem isto faria cada usuario
novo NASCER CEGO — menu so com Home — e quem o criou leria isso como "a tela de Acesso
quebrou". Por isso este modulo e pre-requisito daquele passo, nao um acompanhamento.

O MODELO
--------
O papel deixa de ser um segundo mecanismo ao lado da ABAC e passa a ser um PRESET
declarado em `infra/modules.yaml` (`role_defaults` por campo), aplicado UMA VEZ, na
criacao. Duas propriedades vem junto, e sao desejadas:

  · Papel e CERTIDAO DE NASCIMENTO, nao politica viva. Editar o preset nao muda quem
    ja existe — mesma semantica de seed-if-absent do resto da casa. Mudanca de politica
    se aplica por edicao, nao por decreto.
  · Trocar o papel de alguem depois NAO reescreve os grants. Rebaixar alguem e um ato
    deliberado sobre as permissoes dele; deduzi-lo da troca de papel apagaria grants
    concedidos a mao, em silencio.

MULTIPLOS PAPEIS: o acesso resultante e o MAIOR entre os presets dos papeis
(`admin,developer` recebe o maximo de cada campo). Interseccao seria a leitura errada —
acumular papeis expressa acumular funcoes.
"""
from __future__ import annotations

import logging
from typing import Any

from plughub_authz import ACCESS_RANK as _RANK

logger = logging.getLogger("plughub.auth_api.presets")

# read_only e write_only sao incomparaveis entre si, mas ambos < read_write.
#
# ⚠️ Ate 2026-08-28 esta era a QUARTA copia da tabela de rank, e o comentario que a
# acompanhava dizia apontar "as tres casas" — apontar nao e mecanismo, e foi assim que as seis
# implementacoes do verificador divergiram em seis pontos. Hoje e a tabela canonica.
# O censo do `probe_authz_single_verifier.sh` nunca contou este arquivo (ele nao
# decodifica JWT nem le `module_config` de claims): a copia estava no eixo VIZINHO,
# como o resolvedor de escopo estava.
#
# `>` sobre esta tabela mantem o PRIMEIRO entre `read_only` e `write_only`, que
# colapsam em 1. Ambiguidade preexistente e preservada de proposito: escolher um dos
# dois aqui seria inventar uma ordem que nenhuma das outras casas tem.


def build_module_config(
    roles: list[str],
    modules: list[dict[str, Any]],
) -> dict[str, dict[str, dict[str, Any]]]:
    """Monta o `module_config` de nascimento para `roles`.

    `modules` = linhas de `auth.module_registry` (cada uma com `permission_schema`).
    Campos sem `role_defaults` para nenhum dos papeis simplesmente NAO entram — ausencia
    e negacao, e escrever `access: none` encheria o config de ruido que a tela de Acesso
    teria de filtrar.
    """
    out: dict[str, dict[str, dict[str, Any]]] = {}
    if not roles:
        return out

    for mod in modules:
        module_id = mod.get("module_id")
        schema = mod.get("permission_schema") or {}
        if not module_id or not isinstance(schema, dict):
            continue
        campos: dict[str, dict[str, Any]] = {}
        for campo, definicao in schema.items():
            if not isinstance(definicao, dict):
                continue
            defaults = definicao.get("role_defaults") or {}
            if not isinstance(defaults, dict):
                continue
            melhor = "none"
            for papel in roles:
                acesso = defaults.get(papel, "none")
                if _RANK.get(acesso, 0) > _RANK.get(melhor, 0):
                    melhor = acesso
            if melhor == "none":
                continue
            dominio = definicao.get("domain") or []
            if dominio and melhor not in dominio:
                # Declaracao invalida no catalogo. Recusar ALTO aqui e melhor do que
                # deixar o 422 estourar na criacao do usuario, longe da causa.
                logger.error(
                    "preset invalido: %s.%s = '%s' fora do domain %s — campo ignorado",
                    module_id, campo, melhor, dominio,
                )
                continue
            campos[campo] = {"access": melhor, "scope": []}
        if campos:
            out[module_id] = campos
    return out


def roles_sem_preset(modules: list[dict[str, Any]], roles_conhecidos: list[str]) -> list[str]:
    """Papeis declarados que nenhum campo do catalogo menciona.

    Um papel assim faz `build_module_config` devolver `{}` — exatamente o "nascer cego"
    que este modulo existe para impedir. Chamado no boot para LOGAR, porque descobrir
    isso na criacao do primeiro usuario daquele papel e tarde demais.
    """
    citados: set[str] = set()
    for mod in modules:
        for definicao in (mod.get("permission_schema") or {}).values():
            if isinstance(definicao, dict):
                citados.update((definicao.get("role_defaults") or {}).keys())
    return [r for r in roles_conhecidos if r not in citados]
