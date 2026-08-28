"""
auth.py
Camada FINA de adaptação do channel-gateway sobre o verificador canônico.

⚠️ ESTE MÓDULO NÃO É O PONTO COMPARTILHADO — `packages/py-authz` é.

A versão original deste docstring dizia *"outros módulos devem reusar estas funções em
vez de reimplementar o decode/ABAC"*, e **cinco serviços reimplementaram assim mesmo**.
Promessa sem mecanismo é a mesma família do DDL de `participation_intervals`, que
afirmava em prosa uma ordenação que nenhum produtor impunha. O mecanismo é
`infra/test/probe_authz_single_verifier.sh`.

MIGRAÇÃO CONCLUÍDA (passo 3 do arco, 2026-08-28)
================================================
Saíram daqui, para `plughub_authz`:

  · `verify_user_jwt`    — o `pyjwt.decode` que fazia deste arquivo uma das SEIS
                           implementações contadas pelo C1 do probe;
  · `abac_can`           — junto com o `_ACCESS_ORDER` local, que era cópia por valor
                           da tabela de rank;
  · `bearer_from_header` — parser de header, idêntico ao canônico.

**A divergência 4 fechou com a migração.** O `abac_can` local fazia
`_ACCESS_ORDER.get(min_access, 1)`: um `min_access` digitado errado (`"readwrite"`)
virava rank 1 e, com ele, qualquer grant `read_only` para cima passava. O canônico
levanta `ValueError`, porque erro de programação que vira *"passa"* é como um portão
se paga sozinho. Era **inerte hoje** — o único call site (`main.py`
`_resolve_approver_principal`) passa o literal `"write_only"` —, e é justamente por
isso que valia fechar antes de alguém escrever o segundo call site.

O QUE FICA AQUI, E POR QUÊ
==========================
Só o que é fato do channel-gateway, não do verificador:

  · `pool_in_scope`      — wrapper que carimba a ORIGEM no log do resolvedor de escopo.
                           Não é alias: o passo 3 do plano de `accessible_pools` precisa
                           de uma LISTA de usuários a decidir, e *"veio do
                           channel-gateway"* é metade dessa linha.
  · `accessible_pools`   — helper de LOG, não de decisão (ver docstring).

Segredo: `PLUGHUB_AUTH_JWT_SECRET` (`settings.auth_jwt_secret`), o mesmo que a
analytics-api consome.

Uso (A5 — resume de aprovação interna):
    from plughub_authz import abac_can, verify_user_jwt
    payload = verify_user_jwt(token, settings.auth_jwt_secret)
    if payload and abac_can(payload, "approvals", "decide", "write_only"):
        ...  # possessed-grade; `sub` = decided_by
"""
from __future__ import annotations

import logging

from plughub_authz import pool_in_scope as _canonical_pool_in_scope

logger = logging.getLogger("plughub.channel-gateway.auth")


def accessible_pools(payload: dict) -> list[str]:
    """Lista CRUA de pools do JWT (Arc 7), para LOG.

    ⚠️ Não é função de decisão — quem decide domínio é `pool_in_scope`. A distinção
    importa porque a lista vazia é ambígua por construção (hoje "todos", depois do
    passo 3 "nenhum"), e essa ambiguidade só é tolerável numa linha de log.
    """
    ap = payload.get("accessible_pools")
    return ap if isinstance(ap, list) else []


# `pool_in_scope` era uma das TRÊS cópias do resolvedor de escopo (as outras em
# `analytics-api/pool_auth.py` e `evaluation-api/router.py`), todas com o mesmo
# marcador `LEGADO_POOLS_VAZIO` e nenhuma coberta pelo
# `probe_authz_single_verifier.sh` — ele conta quem DECODIFICA JWT, e estas só
# consomem claims já decodificados.
#
# Consolidadas em `plughub_authz` em 2026-08-28, antes do passo 3 do plano de
# `accessible_pools`: aquele passo inverte o significado de `[]`, e uma inversão
# aplicada a duas das três cópias é vazamento de escopo que degrada MUDO.
def pool_in_scope(payload: dict, pool_id: str) -> bool:
    """True se `pool_id` está no domínio do chamador. Ver `plughub_authz.resolve_scope`."""
    return _canonical_pool_in_scope(payload, pool_id, "channel-gateway")
