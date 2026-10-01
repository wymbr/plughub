"""tenant_scope.py — AUT-71 (2026-10-01): o tenant de toda gestão é o do TOKEN.

Medido ao vivo antes desta casa existir: o admin do `tenant_demo` criou um usuário no tenant
`probe_aas08_outro` (`POST /auth/users`, 201), listou os usuários dele
(`GET /auth/users?tenant_id=…`), leu a ficha e o `module_config` por id, e listou templates e
grupos de lá. Nenhuma rota de gestão comparava o tenant do pedido com o do token — o ABAC
`config.users`/`config.permissions` respondia *"pode administrar pessoas"*, e ninguém
perguntava *"de qual tenant"*. O `_assert_pode_administrar` deixa o admin passar por definição
(`_irrestrito_para_pessoas`), e era essa a porta: irrestrito DENTRO do tenant virou irrestrito
na instalação.

Três respostas, e a escolha de cada uma é deliberada:

  · `tenant_id` no CORPO ou na QUERY diferente do token → **403 `tenant_mismatch`**, com log
    nomeando quem pediu o quê. O chamador declarou o outro tenant; dizer que é proibido não
    revela nada que ele não tenha escrito (mesma resposta da AUT-69 e do TNT-01).
  · LINHA buscada por id que é de outro tenant → **404**, igual a inexistente. Responder 403
    confirmaria que o id existe noutro tenant (mesma regra da AUT-63 no calendar-api).
  · token SEM `tenant_id` → **403 `tenant_claim_missing`**: não há em nome de quem gravar.
    Nunca *"sem tenant = todos"*.

O `tenant_id` do corpo fica OBRIGATÓRIO nos modelos (não virou opcional com default no token):
os chamadores reais já o mandam igual ao do token (tela de Acesso, Grupos, `seed_auth.py`), e
conferir o que veio é mais barato de provar que reescrever o que não veio.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException

logger = logging.getLogger("plughub.auth_api.tenant_scope")


def claims_tenant(claims: dict[str, Any]) -> str:
    """O tenant do token, ou 403 — nunca vazio seguindo adiante."""
    tenant = str(claims.get("tenant_id") or "")
    if not tenant:
        logger.warning("gestao RECUSADA: token de %s sem tenant_id", claims.get("sub"))
        raise HTTPException(status_code=403, detail="tenant_claim_missing")
    return tenant


def same_tenant(claims: dict[str, Any], pedido: str | None, onde: str) -> str:
    """`tenant_id` declarado pelo chamador (corpo ou query) tem de ser o do token.

    Ausente vale o do token — é o caso das listagens chamadas sem `?tenant_id=`, que antes
    caíam num default fixo (`"tenant_demo"`) e liam o tenant do BUILD, não o do chamador.
    """
    tenant = claims_tenant(claims)
    if pedido and pedido != tenant:
        logger.warning("%s RECUSADO: %s (tenant %s) pediu o tenant %s",
                       onde, claims.get("email") or claims.get("sub"), tenant, pedido)
        raise HTTPException(status_code=403, detail="tenant_mismatch")
    return tenant


def own_row(claims: dict[str, Any], row: dict[str, Any] | None, oque: str) -> dict[str, Any]:
    """Linha buscada por id: de outro tenant é 404, como inexistente."""
    tenant = claims_tenant(claims)
    if not row:
        raise HTTPException(status_code=404, detail=f"{oque} not found")
    if str(row.get("tenant_id") or "") != tenant:
        logger.warning("%s de outro tenant RECUSADO (404): %s (tenant %s) pediu id do tenant %s",
                       oque, claims.get("email") or claims.get("sub"), tenant, row.get("tenant_id"))
        raise HTTPException(status_code=404, detail=f"{oque} not found")
    return row
