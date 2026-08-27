"""
plughub_authz — verificador CANÔNICO de JWT de usuário (auth-api, HS256) + portão ABAC
sobre `module_config`.

POR QUE ESTE PACOTE EXISTE
==========================
Em 2026-08-27, ao fechar as portas de escrita da `calendar-api` e da `dialog-api`,
mediu-se quantas implementações independentes de *"verificar JWT + ler module_config"*
já existiam no repositório: **seis**. E elas **já haviam divergido**, em seis pontos:

  1. BIBLIOTECA — stdlib à mão (`config-api`, `pricing-api`) × PyJWT (`analytics-api`,
     `channel-gateway`, `evaluation-api`) × `python-jose` (`auth-api`). Três.
  2. ORDEM DE ACESSO — dict `{none:0, read_only:1, write_only:1, read_write:2}` em quatro
     serviços × LISTA indexada `["none","read_only","write_only","read_write"]` na
     `analytics-api/audit.py`, onde `write_only` é ESTRITAMENTE MAIOR que `read_only`.
     O mesmo grant responde diferente em dois serviços.
  3. `module_config` VAZIO — recusa em quatro × LIBERA na `evaluation-api` quando
     `min_access is None` (ramo legado de compatibilidade, declarado).
  4. `min_access` DESCONHECIDO — `.get(min_access, 0)` em três serviços faz um valor
     digitado errado ("readwrite") virar rank 0, e então QUALQUER grant não-`none`
     passa. O `channel-gateway` usa `.get(min_access, 1)`. Fail-open por typo.
  5. CREDENCIAL AUSENTE — 401 (`config-api`) × 403 (`pricing-api`).
  6. SEGREDO AUSENTE — 503 × recusa × degrada ABERTO (`pool_auth`, declarado) × `None`.

O agravante que dá o nome à dívida: `channel-gateway/auth.py` **promete no docstring**
ser o ponto compartilhado — *"outros módulos devem reusar estas funções em vez de
reimplementar"* — e cinco serviços reimplementaram. Promessa sem mecanismo é a mesma
família do DDL de `participation_intervals`, que afirmava em prosa uma ordenação que
nenhum produtor impunha. Este pacote é o MECANISMO.

ESCOPO DECLARADO
================
Hoje só a `calendar-api` e a `dialog-api` consomem este pacote. Os seis existentes
**não** foram migrados de propósito: cada um tem postura deliberadamente diferente
(bypass de demo na analytics, dual-gate com admin-token na config/pricing, o ramo
legado da evaluation), e trocar a postura de seis serviços no mesmo commit que abre
duas portas novas é raio de ação onde regressão se esconde. A migração está registrada
em `TODO.md` COM a tabela acima — é ela que torna a dívida acionável em vez de
aspiracional. `infra/test/probe_authz_single_verifier.sh` conta as implementações e
reprova a sétima.

DECISÕES CANÔNICAS (cada uma resolve uma das divergências medidas)
==================================================================
  · UMA tabela de rank, com `read_only` e `write_only` COLAPSADOS em 1 — a maioria
    medida, e a que a UI (`permissions.ts`) usa. A lista indexada da `analytics-api` é
    o outlier; ela está registrada, não replicada.
  · `min_access` DESCONHECIDO levanta `ValueError` na chamada. É erro de programação,
    e erro de programação que vira "passa" é como o defeito 4 se paga.
  · `module_config` vazio ⇒ **nega**. Grant-first, a decisão do arco de ABAC total: a
    ausência de grants nunca é autorização.
  · Credencial AUSENTE ⇒ 401 (não sei quem é). Grant INSUFICIENTE ⇒ 403 (sei quem é, e
    não pode). Dois estados diferentes merecem dois códigos.
  · Portão DESABILITADO (sem `admin_token`) **loga em WARNING nomeando o que deixa de
    valer**. Degradação nunca é silenciosa — e "using default values" genérico foi
    exatamente a frase que ninguém leu por meses.
"""
from __future__ import annotations

import logging
from typing import Any

import jwt as pyjwt
from fastapi import HTTPException

logger = logging.getLogger("plughub.authz")

__all__ = [
    "ACCESS_RANK",
    "abac_can",
    "bearer_from_header",
    "enforce_write",
    "verify_user_jwt",
]

# `read_only` e `write_only` COLAPSAM em 1 de propósito: são graus laterais do mesmo
# nível, não uma escada. Quem precisa escrever pede `read_write`.
ACCESS_RANK: dict[str, int] = {
    "none": 0,
    "read_only": 1,
    "write_only": 1,
    "read_write": 2,
}


def bearer_from_header(authorization: str | None) -> str | None:
    """Extrai o token de `Authorization: Bearer <jwt>`. None se ausente/malformado."""
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
        return parts[1].strip()
    return None


def verify_user_jwt(token: str | None, secret: str) -> dict[str, Any] | None:
    """
    Decodifica e VERIFICA o JWT de usuário do auth-api (HS256).

    Devolve o payload, ou None quando o token é ausente/inválido/expirado. Segredo
    vazio também devolve None — e o chamador tem de tratar isso como "não sei
    verificar", nunca como "verificou". `enforce_write` levanta 503 nesse caso.
    """
    if not token or not secret:
        return None
    try:
        return pyjwt.decode(token, secret, algorithms=["HS256"])
    except pyjwt.ExpiredSignatureError:
        logger.info("authz: JWT expirado")
        return None
    except pyjwt.InvalidTokenError as exc:
        logger.info("authz: JWT inválido: %s", exc)
        return None


def abac_can(
    claims: dict[str, Any],
    module: str,
    field: str,
    min_access: str = "read_only",
) -> bool:
    """
    True se `module_config[module][field].access` ≥ `min_access`.

    `min_access` fora de `ACCESS_RANK` levanta `ValueError` — ver decisão canônica no
    cabeçalho. Um typo virando "passa" foi a divergência 4.
    """
    if min_access not in ACCESS_RANK:
        raise ValueError(
            f"min_access desconhecido: {min_access!r} — esperado um de {sorted(ACCESS_RANK)}"
        )
    mc = claims.get("module_config") or {}
    mod = mc.get(module)
    fld = (mod or {}).get(field) if isinstance(mod, dict) else None
    if not isinstance(fld, dict):
        return False
    access = fld.get("access") or "none"
    return ACCESS_RANK.get(access, 0) >= ACCESS_RANK[min_access]


def enforce_write(
    *,
    request: Any,
    admin_token: str,
    jwt_secret: str,
    module: str,
    field: str,
    min_access: str = "read_write",
    what: str = "escrita",
) -> dict[str, Any] | None:
    """
    Portão DUAL de escrita: admin-token (seed/sistema) OU Bearer + ABAC.

    Devolve os claims quando o caminho Bearer decidiu, ou None quando quem decidiu foi
    o admin-token (ou quando o portão está desabilitado). Levanta `HTTPException`:

      401  credencial ausente ou não verificável
      403  autenticado, mas sem o grant `{module}.{field}` no nível pedido
      503  portão ligado (há admin_token) mas sem `jwt_secret` para verificar Bearer

    `admin_token` vazio DESABILITA o portão — postura preservada dos serviços que já
    faziam isso (`config-api`, `pricing-api`), para que um deploy interno sem token não
    fique de pé sem conseguir escrever. Mas a passagem é LOGADA, nomeando o campo que
    deixa de valer: um portão inerte que não avisa é indistinguível de um portão.
    """
    if not admin_token:
        logger.warning(
            "authz: portão DESABILITADO (admin_token vazio) — %s liberada sem credencial; "
            "o grant %s.%s NÃO está sendo exigido neste deploy",
            what, module, field,
        )
        return None

    header_token = request.headers.get("x-admin-token") or request.headers.get("X-Admin-Token")
    if header_token == admin_token:
        return None  # caminho de sistema (seed, bootstrap)

    token = bearer_from_header(request.headers.get("authorization"))
    if not token:
        raise HTTPException(
            status_code=401,
            detail=f"credencial ausente: exige X-Admin-Token ou Bearer com {module}.{field}",
        )
    if not jwt_secret:
        # Não é 403: não se sabe se ele PODE, porque não há como verificar quem é.
        raise HTTPException(status_code=503, detail="jwt secret não configurado")
    claims = verify_user_jwt(token, jwt_secret)
    if claims is None:
        raise HTTPException(status_code=401, detail="token inválido ou expirado")
    if not abac_can(claims, module, field, min_access):
        raise HTTPException(
            status_code=403,
            detail=f"forbidden: exige {module}.{field} ({min_access})",
        )
    return claims
