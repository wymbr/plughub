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
    "LEGACY_EMPTY_MEANS_UNRESTRICTED",
    "LEGACY_UNRESTRICTED_MARK",
    "abac_can",
    "bearer_from_header",
    "enforce_write",
    "pool_in_scope",
    "resolve_scope",
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
    scope_id: str | None = None,
) -> bool:
    """
    True se `module_config[module][field].access` ≥ `min_access`, respeitando o `scope`
    do grant quando um `scope_id` é nomeado.

    `min_access` fora de `ACCESS_RANK` levanta `ValueError` — ver decisão canônica no
    cabeçalho. Um typo virando "passa" foi a divergência 4.

    ── `scope_id` (D2, entrou no passo 6, 2026-08-28) ────────────────────────────
    O grant carrega uma lista `scope` ao lado do `access`, e ela recorta a CAPACIDADE a
    um conjunto de pools. É eixo diferente de `resolve_scope`/`accessible_pools`, que
    recorta LINHAS de relatório: aqui a pergunta é *"posso exercer esta função NESTE
    pool?"*.

    Três ramos, e o terceiro é herdado, não decidido:

      1. `scope` vazio/ausente → grant GLOBAL → passa (independe de `scope_id`).
      2. `scope` não-vazio + `scope_id` nomeado → teste de pertencimento.
      3. `scope` não-vazio + `scope_id is None` → **passa**.

    O ramo 3 vem da `evaluation-api`, onde o `pool_id` sai de `campaign.pool_id` e ser
    `None` significa *"a campanha não é escopada a pool"* — não *"esqueci de passar"*.
    Sob essa leitura, passar é correto: não há pool do qual estar fora. Mas a leitura
    oposta é defensável (um usuário escopado só deveria tocar recurso escopado), e
    decidir isso DENTRO de uma migração tornaria uma eventual regressão inatribuível.
    Portado literalmente, e registrado no `TODO.md` junto do passo 3 de
    `accessible_pools`, que é o mesmo tipo de pergunta.

    ── o ALIAS `pool:x` × `x` ────────────────────────────────────────────────────
    A UI grava `"pool:retencao_humano"`; parte dos grants tem só `"retencao_humano"`.
    As duas formas são aceitas AQUI, numa casa só — era o que a `evaluation-api` já
    fazia, e duplicar a normalização é como um alias vira divergência.
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
    if ACCESS_RANK.get(access, 0) < ACCESS_RANK[min_access]:
        return False

    escopo = fld.get("scope")
    if not isinstance(escopo, list) or not escopo:
        return True          # ramo 1 — grant global
    if scope_id is None:
        return True          # ramo 3 — o recurso não nomeia escopo (ver docstring)
    return scope_id in escopo or f"pool:{scope_id}" in escopo   # ramo 2


# ══════════════════════════════════════════════════════════════════════════════
# ESCOPO DE POOL — o SEGUNDO verificador
# ══════════════════════════════════════════════════════════════════════════════
#
# `abac_can` responde *"quais FUNÇÕES eu posso exercer?"*. Esta seção responde a outra
# pergunta, de eixo independente: *"quais LINHAS/POOLS eu alcanço?"*. As duas foram
# confundidas uma vez (o claim `unrestricted` liberando o menu, corrigido no mesmo dia
# em que foi introduzido) e a separação é decisão registrada em CLAUDE.md.
#
# POR QUE ELE ENTROU AQUI, e por que isto era o item URGENTE da migração
# =====================================================================
# Havia **três** implementações, medidas em 2026-08-28, todas carregando o mesmo
# marcador `LEGADO_POOLS_VAZIO`:
#
#   · `analytics-api/pool_auth.py:153`      `_resolve_scope`
#   · `channel-gateway/auth.py:67`          `pool_in_scope`
#   · `evaluation-api/router.py:437`        `_scope_from_claims`
#
# O `probe_authz_single_verifier.sh` NÃO as contava — ele conta quem decodifica JWT e
# lê `module_config`, e estas três só consomem claims já decodificados. Ou seja: sobre
# este eixo não havia mecanismo nenhum.
#
# E o prazo não era estético. O **passo 3** do plano de `accessible_pools` inverte o
# significado de `[] `: hoje é *"todos os pools"* (convenção implícita), depois será
# *"nenhum pool"*. Uma inversão aplicada a duas das três cópias é vazamento de escopo
# que **degrada mudo** — ninguém recebe erro, o relatório só mostra linhas a mais.
# Consolidar ANTES transforma o passo 3 em edição de uma linha, num arquivo.
#
# ⚠️ O QUE O PASSO 3 AINDA TERÁ DE OLHAR (não está resolvido aqui)
# ---------------------------------------------------------------
# Depois da inversão, `resolve_scope` passa a devolver `[]` para o usuário sem recorte
# declarado, e `[]` **não é** `None`. Todo consumidor precisa tratar lista VAZIA como
# domínio vazio, nunca como "sem filtro" — se algum deles fizer `if not pools: <sem
# filtro>`, a inversão vira liberação geral no lugar de restrição geral, que é o pior
# desfecho possível. Isso é auditoria de call site, e continua pendente de propósito:
# fazê-la aqui seria fazer o passo 3 cedo e sem inventário.

LEGACY_UNRESTRICTED_MARK = "LEGADO_POOLS_VAZIO"

# ⚠️ ESTE É O INTERRUPTOR DO PASSO 3, e ele existe para ser UM.
#
# True  (hoje)  — `accessible_pools == []` e sem claim `unrestricted` ⇒ irrestrito,
#                 com WARNING contado. É a convenção legada, e ela tem de sobreviver
#                 enquanto tokens antigos circulam (TTL de 1h) e enquanto houver
#                 emissor que não conheça o claim.
# False (passo 3) — a mesma entrada passa a significar **nenhum pool**.
#
# Virar isto é ato deliberado: os testes de `test_scope.py` cobrem os DOIS estados, de
# modo que a inversão já tem tabela-verdade escrita e não precisa ser descoberta no dia.
LEGACY_EMPTY_MEANS_UNRESTRICTED: bool = False


def resolve_scope(claims: dict[str, Any] | None, origem: str) -> list[str] | None:
    """
    Domínio de pools do chamador. **`None` = irrestrito**; lista = recorte.

    Ordem dos ramos — idêntica nas três origens, de propósito: dois serviços que
    respondem diferente a *"este pool está no meu domínio?"* é como se paga um
    vazamento.

      1. lista não-vazia → decide a lista.
      2. senão → depende de `LEGACY_EMPTY_MEANS_UNRESTRICTED` (ver acima), e o ramo
         legado é **CONTADO**, nunca omitido.

    ⚠️ **O claim `unrestricted` foi REMOVIDO em 2026-08-31 (decisão do dono).** Ele era a
    porta larga por CLAIM: um usuário podia carregar "vejo o tenant inteiro". Sob ABAC
    total isso não se sustenta — escopo de pool é sempre enumerado, porque pools são do
    TENANT (criados pelo usuário) e não da plataforma. Escopo de usuário passa a ser
    **sempre uma lista**; `None` (irrestrito) sobrevive apenas para principal de SISTEMA,
    construído explicitamente (ex.: `pool_auth.py`, principal de serviço), nunca vindo de
    um token de usuário. É "remover a alternativa", não "marcar cada caso".

    `origem` nomeia o call site no log (`header`, `SSE`, `results`, `transcript`, …).
    Sem ela o WARNING diria que existe um usuário a decidir, sem dizer onde ele
    apareceu — e o passo 3 precisa de uma lista, não de uma estimativa.

    O log distingue **claim AUSENTE** (token velho / emissor que não o cunha) de
    **claim presente e `false`** (usuário que realmente não tem escopo declarado). São
    populações diferentes e só a segunda é decisão de alguém.
    """
    raw = (claims.get("accessible_pools") if claims else None) or []
    if raw:
        return list(raw)
    if LEGACY_EMPTY_MEANS_UNRESTRICTED:
        logger.warning(
            "authz scope(%s): irrestrito por %s — `accessible_pools` vazio. "
            "claims_presentes=%s sub=%s. Este ramo desaparece no passo 3; "
            "enquanto existir, cada linha destas e um usuario a decidir.",
            origem,
            LEGACY_UNRESTRICTED_MARK,
            bool(claims),
            (claims or {}).get("sub", ""),
        )
        return None
    logger.info(
        "authz scope(%s): dominio VAZIO — `accessible_pools` vazio e o ramo legado esta "
        "DESLIGADO (AUT-03, 2026-08-31), entao lista vazia significa NENHUM pool. "
        "claims_presentes=%s sub=%s. Isto e config valida, nao defeito: quem precisa "
        "operar recebe escopo em Acesso. Fica em INFO, e nao em WARNING, justamente "
        "porque virou desfecho normal — mas nao pode ficar MUDO: 'nao vejo nada' e o "
        "sintoma que chega, e sem esta linha ele e indistinguivel de tela quebrada.",
        origem,
        bool(claims),
        (claims or {}).get("sub", ""),
    )
    return []


def pool_in_scope(claims: dict[str, Any] | None, pool_id: str, origem: str = "pool_in_scope") -> bool:
    """True se `pool_id` está no domínio do chamador. Derivado de `resolve_scope`."""
    dominio = resolve_scope(claims, origem)
    if dominio is None:
        return True
    return pool_id in dominio


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
