"""
pool_auth.py
Optional FastAPI dependency for pool-scoped data visibility (Arc 7c).

Reads an auth-api Bearer JWT (HS256) and extracts ``accessible_pools[]``
from its claims to restrict analytics queries to the caller's allowed pools.

Behaviour summary
-----------------
NB (Segurança Fase A/E): pool-scoping é DESACOPLADO de ``analytics_open_access``. Aquele
flag é um bypass amplo de demo (audit/admin/transcript sem token) — mas o domínio de pools
deve valer sempre que houver como verificar o token. Aqui o único bypass é a AUSÊNCIA de
segredo (sem como verificar o JWT).
- No auth_jwt_secret configured
    → PoolPrincipal(accessible_pools=None) — no restriction (all pools)
- No Authorization header present
    → PoolPrincipal(accessible_pools=None) — unauthenticated callers see all pools
      (backward-compatible with existing dashboard/report consumers)
- Valid JWT, claim `unrestricted: true`
    → PoolPrincipal(accessible_pools=None) — irrestrito EXPLICITO (passo 2, 2026-08-27)
- Valid JWT, accessible_pools=[]   (LEGADO: convenção implícita "todos os pools")
    → PoolPrincipal(accessible_pools=None) — no restriction, **contado** (ver `_resolve_scope`)
- Valid JWT, accessible_pools=[…]  (restricted operator)
    → PoolPrincipal(accessible_pools=[…]) — queries filtered to those pools only
- Invalid / expired JWT
    → HTTP 401

Usage
-----
    @router.get("/reports/sessions")
    async def report_sessions(
        ...,
        pool_principal: PoolPrincipal = Depends(optional_pool_principal),
    ):
        accessible = pool_principal.accessible_pools   # None | list[str]
        data = await query_sessions_report(..., accessible_pools=accessible)
"""
from __future__ import annotations

import json
import logging

from typing import Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .config import get_settings

logger = logging.getLogger("plughub.analytics.pool_auth")

_bearer = HTTPBearer(auto_error=False)

# ADR adr-internal-work-queue-author-bound (D2) — sufixo reservado do espelho de fila
# interna. Garantido por construção: a regex de `pool_id` no registry só admite hífen
# nesta posição, então nenhum pool declarado por tenant pode colidir.
_INTERNAL_QUEUE_SUFFIX = "-int"


def _with_internal_mirrors(pools: list[str] | None) -> list[str] | None:
    """
    Acesso DERIVADO: quem alcança `p` alcança `p-int`.

    Sem isto o supervisor com acesso a `retencao_humano` NÃO enxerga o ACW de
    `retencao_humano-int` — teríamos tornado o tempo de pós-atendimento mensurável e
    o escondido justamente de quem precisa dele. É a falha mais provável desta ADR
    porque o sintoma é AUSÊNCIA (um pool a menos no relatório), não erro.

    Derivamos aqui, e não no JWT emitido pelo auth-api: mantém o token intacto,
    dispensa re-login e evita duplicar o conceito em todo consumidor de
    `accessible_pools`. `None` (irrestrito) e `[]` passam inalterados.
    """
    if not pools:
        return pools
    out = list(pools)
    seen = set(out)
    for p in pools:
        if p.endswith(_INTERNAL_QUEUE_SUFFIX):
            continue
        mirror = f"{p}{_INTERNAL_QUEUE_SUFFIX}"
        if mirror not in seen:
            out.append(mirror)
            seen.add(mirror)
    return out


class PoolPrincipal:
    """
    Lightweight identity object carrying pool-scoped and agent-group-scoped access.

    accessible_pools:
      None       → no restriction (all pools visible)
      list[str]  → caller may only see data for these pool_ids

    supervised_agent_types (Arc 9):
      None       → no restriction (all agent types visible)
      list[str]  → caller may only see sessions/segments involving these agent_type_ids
    """

    def __init__(
        self,
        accessible_pools: list[str] | None,
        tenant_id: str | None,
        sub: str,
        supervised_agent_types: list[str] | None = None,
    ) -> None:
        self.accessible_pools       = accessible_pools
        self.supervised_agent_types = supervised_agent_types
        self.tenant_id = tenant_id
        self.sub = sub

    @property
    def is_unrestricted(self) -> bool:
        """True when the caller can see all pools."""
        return self.accessible_pools is None


# ══════════════════════════════════════════════════════════════════════════════
# Tradução da convenção do auth-api → domínio interno  (passo 2, 2026-08-27)
# ══════════════════════════════════════════════════════════════════════════════
#
# Historicamente havia UMA regra implícita: `accessible_pools == []` significa "todos
# os pools". Ela é lida por SETE tradutores em serviços diferentes, e o passo 3 do
# plano inverte esse significado para "nenhum pool". Sem um jeito EXPLÍCITO de dizer
# "este usuário não tem recorte", a inversão apagaria o acesso de quem depende da
# convenção — em silêncio, que é o modo de falha que esta casa persegue.
#
# `unrestricted: true` é essa declaração. Ordem dos ramos, e por quê:
#
#   1. `accessible_pools` não-vazio → escopado. O RESTRITIVO vence, sempre.
#   2. claim `unrestricted` = true  → irrestrito EXPLÍCITO.
#   3. senão                        → irrestrito LEGADO, **contado**.
#
# A ordem não é estética. Com o claim vencendo a lista, um `unrestricted` setado por
# engano ALARGA o acesso de um operador escopado — e esse erro é invisível, porque a
# tela só mostra dado a MAIS, nunca a menos. Com a lista vencendo, o mesmo engano é
# inerte. A ambiguidade "os dois setados" fica inofensiva na LEITURA; recusá-la também
# na ESCRITA exigiria validar contra a linha do banco no update parcial, e validação
# pela metade é pior que nenhuma — registrada como follow-up, não feita aqui.
#
# O ramo 3 tem de sobreviver ao passo 2 inteiro: token vive 1h, então tokens sem o
# claim circulam por até uma hora após o deploy, e há serviços que ainda cunham o
# seu. Inverter aqui seria fazer o passo 3 cedo e sem inventário.
#
# O log distingue "claim AUSENTE" (token velho / emissor que não sabe do claim) de
# "claim presente e false com lista vazia" (usuário que realmente não tem escopo
# declarado). São populações diferentes e só a segunda é decisão de alguém — é
# essa distinção que dá ao passo 3 uma lista, em vez de uma estimativa.
_LEGACY_MARK = "LEGADO_POOLS_VAZIO"


def _resolve_scope(payload: dict, origem: str) -> list[str] | None:
    """`None` = irrestrito; lista = escopo (já com os espelhos `-int`)."""
    raw = payload.get("accessible_pools") or []
    if raw:
        return _with_internal_mirrors(list(raw))
    if payload.get("unrestricted") is True:
        return None
    logger.warning(
        "pool_auth(%s): irrestrito por %s — `accessible_pools` vazio e sem claim "
        "`unrestricted`. claim_presente=%s sub=%s. Este ramo desaparece no passo 3 "
        "do plano; enquanto existir, cada linha destas e um usuario a decidir.",
        origem, _LEGACY_MARK, "unrestricted" in payload, payload.get("sub", ""),
    )
    return None


async def optional_pool_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> PoolPrincipal:
    """
    FastAPI dependency — optionally decodes an auth-api Bearer JWT.

    Always succeeds (never raises 401 for missing token). Raises 401 only
    when a token IS present but fails verification.
    """
    settings = get_settings()

    # Pool-scoping é DESACOPLADO do `analytics_open_access` (Segurança Fase A/E): o
    # open_access é um bypass amplo de demo (audit/admin/transcript sem token), mas o
    # domínio de pools deve valer sempre que dá p/ verificar o token. Único bypass aqui:
    # nenhum segredo configurado (não há como verificar o JWT) → irrestrito. Com segredo,
    # a decisão vem do token (ausente → irrestrito no path abaixo; presente → enforça).
    if not settings.auth_jwt_secret:
        if not settings.analytics_open_access:
            logger.error(
                "pool_auth RECUSA: `auth_jwt_secret` AUSENTE e `analytics_open_access` "
                "desligado. Sem segredo nao ha como verificar o token, e 'nao sei quem "
                "e' nao pode virar 've todos os pools'. Configure o segredo, ou ligue "
                "o open_access DE PROPOSITO (ambiente de demo)."
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="auth_unavailable",
            )
        logger.warning(
            "pool_auth: irrestrito por `analytics_open_access` (sem `auth_jwt_secret`). "
            "O escopo de pool NAO esta valendo nesta requisicao."
        )
        return PoolPrincipal(accessible_pools=None, tenant_id=None, sub="open_access")

    # ── Sem header `Authorization` ───────────────────────────────────────────
    #
    # Ate 2026-08-27 este ramo devolvia IRRESTRITO, com o motivo declarado no codigo
    # ("backward-compatible with existing dashboard/report consumers"). Medido: `curl`
    # sem token devolvia as 120 linhas de todos os pools, sem 401 — enquanto um token
    # LIXO no mesmo endpoint devolvia 401. Ou seja, o mecanismo de recusa existia; o
    # 200 do anonimo era ESCOLHA.
    #
    # O defeito nomeado no TODO era "o ABAC de pool e OPT-IN DO CHAMADOR": omitir o
    # header era decisao de QUEM CHAMA. Agora o bypass e decisao declarada do
    # OPERADOR — uma env com default `False`, que e o mesmo mecanismo (e a mesma
    # grafia de ator, `open_access`) que o gate de AUDITORIA ja usa, e aquele e o
    # portao mais sensivel da casa. Producao fecha por default; o demo declara.
    if not credentials:
        if not settings.analytics_open_access:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="auth_required",
            )
        logger.warning(
            "pool_auth: requisicao SEM `Authorization` aceita como irrestrita porque "
            "`analytics_open_access` esta ligado. O escopo de pool NAO esta valendo — "
            "esta linha e a diferenca entre 'nao ha filtro' e 'o filtro nao rodou'."
        )
        return PoolPrincipal(accessible_pools=None, tenant_id=None, sub="open_access")

    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.auth_jwt_secret,
            algorithms=["HS256"],
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
        )
    except jwt.InvalidTokenError as exc:
        logger.warning("pool_auth JWT validation failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )

    sub        = payload.get("sub", "")
    tenant_id  = payload.get("tenant_id")
    accessible_pools: list[str] | None = _resolve_scope(payload, "header")

    # Arc 9 — supervised_agent_types: [] = no restriction (admin); non-empty = filter
    raw_agent_types = payload.get("supervised_agent_types", [])
    supervised_agent_types: list[str] | None = None if not raw_agent_types else list(raw_agent_types)

    return PoolPrincipal(
        accessible_pools=accessible_pools,
        tenant_id=tenant_id,
        sub=sub,
        supervised_agent_types=supervised_agent_types,
    )


def accessible_pools_from_token(token: str | None) -> list[str] | None:
    """
    Decode `accessible_pools` from a raw JWT string passed as a QUERY PARAM.

    For SSE/EventSource callers (dashboard streams): the browser's EventSource cannot
    send an Authorization header, so the auth-api Bearer travels as `?token=`.

    ⚠️ MUDOU EM 2026-08-27 — antes era "lenient by design": token ausente, invalido OU
    **expirado** devolvia `None` (irrestrito), e o comentario justificava com *"a bad
    token can't 401 a stream"*. A consequencia era que o PIOR caso — token vencido — era
    o MAIS permissivo, e um stream ao vivo de dados de contato e justamente onde isso
    menos pode valer. E a justificativa nao se sustenta: um stream **pode** 401 no
    connect; o que ele nao pode e 401 no meio.

    Agora segue a mesma regra do irmao de header: o bypass e decisao declarada do
    OPERADOR (`analytics_open_access`, default `False`), nunca consequencia de o
    chamador ter omitido ou deixado vencer o token.

    LEVANTA `HTTPException(401)` quando nao ha como autorizar e o open_access esta
    desligado — os tres call sites (`dashboard.py`) estao dentro de handlers.
    `accessible_pools=[]` no token continua significando irrestrito (a convencao do
    auth-api; inverte-la e o passo 3 do plano, com o inventario na mao).
    """
    settings = get_settings()

    def _deny(motivo: str) -> None:
        if settings.analytics_open_access:
            logger.warning(
                "pool_auth(SSE): %s — irrestrito porque `analytics_open_access` esta "
                "ligado. O escopo de pool NAO esta valendo neste stream.", motivo,
            )
            return
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="auth_required",
        )

    if not settings.auth_jwt_secret:
        _deny("`auth_jwt_secret` ausente")
        return None
    if not token:
        _deny("sem `?token=`")
        return None
    try:
        payload = jwt.decode(token, settings.auth_jwt_secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        _deny("token EXPIRADO")
        return None
    except jwt.InvalidTokenError:
        _deny("token invalido")
        return None
    # Mesma derivação do `optional_pool_principal` (D2) — os dois pontos de escopo
    # precisam concordar, senão o mesmo relatório mostra pools diferentes conforme
    # o token venha no header ou na query. Por isso é a MESMA função, não duas cópias.
    return _resolve_scope(payload, "SSE")


def accessible_pools_from_request(
    request: "Any", token: "str | None" = None,
) -> "list[str] | None":
    """
    Escopo de pool aceitando as DUAS origens do token: `?token=` OU o cabecalho
    `Authorization: Bearer`.

    ── Por que existe (2026-08-27) ──────────────────────────────────────────────
    O `?token=` foi criado para SSE: `EventSource` nao manda cabecalho. Mas o
    cabecalho e o caminho normal de todo o resto — e `accessible_pools_from_token`
    so olhava a query. Enquanto `analytics_open_access` era `true`, a ausencia do
    `?token=` apenas avisava e devolvia irrestrito: o chamador por cabecalho
    funcionava POR ACIDENTE. Ao endurecer o demo, os tres endpoints de `/dashboard/*`
    passaram a 401 para quem autentica por cabecalho — `apiFetch` do platform-ui
    incluido.

    A precedencia e query-primeiro por compatibilidade: quem ja monta a URL com
    `?token=` continua igual. O cabecalho e o fallback, nao o contrario.
    """
    if not token:
        auth = ""
        try:
            auth = request.headers.get("Authorization", "") or ""
        except Exception:  # request sem headers (teste, chamada interna)
            auth = ""
        if auth.startswith("Bearer "):
            token = auth[len("Bearer "):].strip() or None
    return accessible_pools_from_token(token)


# ══════════════════════════════════════════════════════════════════════════════
# Irmão ESTRITO — para fronteiras de ESCRITA e de dado pessoal
# ══════════════════════════════════════════════════════════════════════════════
#
# `optional_pool_principal` degrada ABERTO em dois ramos declarados (sem segredo;
# sem header). Isso é defensável para uma LEITURA de relatório e indefensável para
# uma ESCRITA em sessão viva — foi o que deixou `POST /supervisor/join` entrar em
# conferência de cliente sem token nenhum.
#
# A postura aqui é a do gate de auditoria (`audit.py`), não a do relatório:
# **sem como verificar, RECUSA**. Ausente ≠ inválido só importa para a MENSAGEM;
# os dois reprovam.

async def require_pool_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> PoolPrincipal:
    """
    Como `optional_pool_principal`, mas **401 em vez de irrestrito** nos dois ramos
    de fail-open. Use em qualquer rota que ESCREVA ou devolva conteúdo de UM contato.

    Motivos de recusa, nomeados (nunca um 401 mudo — quem depura precisa saber se
    faltou config no serviço ou token no chamador):
      - `auth_unavailable`   — `auth_jwt_secret` não configurado. É falha do SERVIÇO:
                               sem segredo não há como verificar, e "não sei quem é"
                               não pode virar "pode tudo" numa fronteira de escrita.
      - `auth_required`      — nenhum header `Authorization`.
      - `Token expired` / `Invalid token` — herdados do decode.
    """
    settings = get_settings()
    if not settings.auth_jwt_secret:
        logger.error(
            "require_pool_principal RECUSA: auth_jwt_secret AUSENTE. Esta rota exige "
            "identidade verificável; sem segredo não há verificação. (O irmão "
            "`optional_pool_principal` degrada aberto neste mesmo ramo — de propósito, "
            "e só para leitura de relatório.)"
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="auth_unavailable",
        )
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="auth_required",
        )
    return await optional_pool_principal(credentials)


# ══════════════════════════════════════════════════════════════════════════════
# Pools de uma sessão VIVA — derivados do Redis
# ══════════════════════════════════════════════════════════════════════════════
#
# ⚠️ Por que isto não é `meta["pool_id"]` e ponto. MEDIDO em 2026-08-27: o campo
# significa coisas DIFERENTES conforme o canal e o momento —
#   · `webchat.py:202` / `webrtc.py:477`  gravam o pool de ENTRADA na criação;
#   · `webhook.py:660` o OMITE de propósito, com comentário dizendo que semeá-lo
#     "gravaria o pool de ENTRADA num campo que os leitores tomam por 'pool que
#     está atendendo'";
#   · o bridge (`activate_human_agent`) sobrescreve com o pool ALOCADO — mas só
#     quando um agente HUMANO é ativado.
# Ou seja: entrada, ausente ou atendimento, dependendo do caminho. É a fatia C da
# dívida de `session:{id}:meta` (ver `docs/guias/session-meta-ownership.md`), e
# autorizar sobre um campo ambíguo seria construir a fronteira sobre o defeito.
#
# Derivamos a UNIÃO, que é o que a semântica de escopo do analytics já usa
# (`_session_scope_clause`: entrou por pool meu OU pool meu atendeu). Um supervisor
# de retenção tem de alcançar o contato que ENTROU por `sac_ia` e está sendo
# atendido por ele — negar isso seria mais restritivo que a própria lista de onde
# ele clicou.

async def resolve_live_session_pools(
    redis, tenant_id: str, session_id: str,
) -> set[str]:
    """
    Devolve os pool_ids que TOCAM esta sessao viva:
    `meta.pool_id` uniao os `pools[]` de cada instancia presente em
    `session:{sid}:ai_agents` e `session:{sid}:human_agents`.

    Conjunto VAZIO significa **"nao consegui determinar"**, nunca "nenhum pool" —
    quem chama deve RECUSAR nos dois casos, mas a distincao importa para a mensagem.
    Nunca levanta: erro de Redis vira conjunto vazio (=> recusa), com log. Falhar
    ABERTO aqui devolveria a fronteira ao estado que este arco fecha.
    """
    pools: set[str] = set()

    try:
        raw_meta = await redis.get(f"session:{session_id}:meta")
        if raw_meta:
            meta = json.loads(raw_meta)
            if isinstance(meta, dict):
                pid = (meta.get("pool_id") or "").strip()
                if pid:
                    pools.add(pid)
    except Exception as exc:
        logger.warning(
            "resolve_live_session_pools: meta de session=%s ilegivel (%s) — o pool de "
            "entrada NAO entra na uniao (a decisao de recusar e de quem chama)",
            session_id, exc,
        )

    instances: set[str] = set()
    for key in (f"session:{session_id}:ai_agents", f"session:{session_id}:human_agents"):
        try:
            for m in (await redis.smembers(key)) or []:
                instances.add(m.decode() if isinstance(m, (bytes, bytearray)) else str(m))
        except Exception as exc:
            logger.warning(
                "resolve_live_session_pools: %s ilegivel (%s) — pools de agente ficam "
                "de fora da uniao", key, exc,
            )

    for inst in instances:
        try:
            raw = await redis.get(f"{tenant_id}:instance:{inst}")
            if not raw:
                continue
            rec = json.loads(raw)
            if not isinstance(rec, dict):
                continue
            for pool in rec.get("pools") or []:
                if isinstance(pool, str) and pool.strip():
                    pools.add(pool.strip())
        except Exception as exc:
            logger.warning(
                "resolve_live_session_pools: instancia %s ilegivel (%s)", inst, exc,
            )

    return pools
