"""
agent_principals.py — principal de máquina EXTERNO, tipo `partner` (AAS-04; adr-a2a-server-binding D6, D7).

Um `partner` é o software de um parceiro ou do próprio tenant (ERP, orquestrador) que chama
pools do tenant pelo canal `a2a`. O admin o cadastra e recebe a credencial UMA vez.

    GET    /auth/v1/agent-principals                      config.agents read_only
    GET    /auth/v1/agent-principals/{id}                 config.agents read_only
    POST   /auth/v1/agent-principals                      config.agents read_write  → 201 + credencial
    PUT    /auth/v1/agent-principals/{id}                 config.agents read_write
    POST   /auth/v1/agent-principals/{id}/credential      config.agents read_write  → rotaciona
    POST   /auth/v1/agent-principals/introspect           X-Service-Token (só serviço)

    AAS-09 — `customer_agent`, o token que o PRÓPRIO cliente gera para o assistente dele:
    POST   /auth/v1/agent-principals/customer-grants          X-Service-Token  → código de retirada
    POST   /auth/v1/agent-principals/customer-grants/redeem   X-Service-Token  → credencial, UMA vez
    POST   /auth/v1/agent-principals/customer/revoke          X-Service-Token  → desativa os do titular

Regras, e de onde vem cada uma:

- **O tenant é o do TOKEN** de quem administra (TNT-01), e o da credencial na introspecção
  (D7: no canal `a2a` o tenant vem *exclusivamente* da credencial). Nenhuma rota lê tenant de
  query ou corpo.
- **`allowed_pools` é CONCESSÃO**, e passa pela mesma régua de quem concede pool a pessoa
  (`grants.violacoes`): ninguém dá a um terceiro pool que não alcança; o master passa. E cada
  pool tem de EXPOR A2A para `partner` (contato, canal, contrato com `partner` em
  `principal_kinds`) — conferido no registry; registry fora recusa (503), nunca aprova por
  ausência.
- **A credencial é opaca e só existe como hash.** Sai em claro no 201 da criação e da rotação e
  em lugar nenhum mais; rotacionar invalida a anterior na hora. Desativar (`active=false`) faz a
  introspecção responder inativa — a borda guarda o veredicto por até 30 s.
- **Introspecção no formato do RFC 7662** (`{"active": false}` quando não serve), e a recusa
  não diz POR QUE (desconhecida × desativada) a quem só tem a credencial; o motivo vai ao log.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
import secrets
from datetime import datetime, timezone
from typing import Any

import asyncpg
import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import db as db_mod
from . import grants
from .config import Settings, get_settings
from .router import _require_config

logger = logging.getLogger("plughub.auth_api.agent_principals")

agent_principals_router = APIRouter(prefix="/auth/v1/agent-principals", tags=["agent-principals"])

_AGENTS_READ  = _require_config("agents", write=False)
_AGENTS_WRITE = _require_config("agents", write=True)

CREDENTIAL_PREFIX = "pha_"
_POOL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_COLS = ("agent_principal_id, tenant_id, kind, origin, display_name, allowed_pools, credential_prefix, "
         "credential_rotated_at, active, created_by, created_at, updated_at, last_authenticated_at, "
         "customer_id, proof_mechanism, proof_verified_at, proof_session_id, mandate, expires_at, "
         "max_active_tasks, max_tasks_per_day")

# AAS-09 — a retirada vale minutos e uma vez: o link passa pela conversa (fica no transcrito), e o
# que protege a credencial é ela não existir antes de a pessoa abrir o link.
PICKUP_TTL_S = 600
PICKUP_PREFIX = "pkc_"
# Mecanismos de prova que sustentam a emissão — os que satisfazem `otp` em `SATISFIED_BY`
# (@plughub/schemas/identity-evidence.ts). Quem julga a prova é o emissor (mcp-server), que tem a
# journey; aqui só se recusa um nome que não é prova.
PROOF_MECHANISMS = frozenset({"otp", "whatsapp"})


def new_credential() -> tuple[str, str, str]:
    """(texto em claro, sha256, prefixo de identificação)."""
    plain = CREDENTIAL_PREFIX + secrets.token_urlsafe(32)
    return plain, hash_credential(plain), plain[:12]


def hash_credential(plain: str) -> str:
    return hashlib.sha256(plain.encode("utf-8")).hexdigest()


def _pool(request: Request) -> asyncpg.Pool:
    return request.app.state.pool


def _out(row: Any) -> dict[str, Any]:
    d = dict(row)
    d["agent_principal_id"] = str(d["agent_principal_id"])
    d["allowed_pools"] = list(d.get("allowed_pools") or [])
    if d.get("mandate") is not None:
        d["mandate"] = list(d["mandate"])
    for k in ("credential_rotated_at", "created_at", "updated_at", "last_authenticated_at",
              "proof_verified_at", "expires_at"):
        if d.get(k) is not None and hasattr(d[k], "isoformat"):
            d[k] = d[k].isoformat()
    return d


def _tenant(claims: dict[str, Any]) -> str:
    t = str(claims.get("tenant_id") or "")
    if not t:
        raise HTTPException(status_code=403, detail="token sem tenant_id — principal não administrável")
    return t


# ── a concessão de pools ──────────────────────────────────────────────────────

async def _pool_exposes_a2a_to_partner(settings: Settings, tenant_id: str, pool_id: str) -> str | None:
    """None = pode ser concedido; senão, o motivo. Registry fora levanta 503."""
    motivo, _ = await _pool_exposes_a2a(settings, tenant_id, pool_id, "partner")
    return motivo


async def _pool_exposes_a2a(
    settings: Settings, tenant_id: str, pool_id: str, kind: str,
) -> tuple[str | None, dict[str, Any]]:
    """(motivo, contrato A2A do pool). `motivo` None = expõe A2A a este tipo. Registry fora → 503."""
    url = f"{settings.agent_registry_url.rstrip('/')}/v1/pools/{pool_id}"
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            r = await c.get(url, headers={"x-tenant-id": tenant_id})
    except httpx.HTTPError as exc:
        logger.error("principal: registry inalcançável conferindo o pool %s (%s) — recusando", pool_id, exc)
        raise HTTPException(status_code=503, detail="agent-registry indisponível; concessão de pool recusada") from exc
    if r.status_code == 404:
        return "não existe", {}
    if r.status_code != 200:
        logger.error("principal: registry respondeu %s para o pool %s — recusando", r.status_code, pool_id)
        raise HTTPException(status_code=503, detail="agent-registry respondeu erro; concessão de pool recusada")
    p = r.json()
    if (p.get("purpose") or "contact") != "contact":
        return "não é pool de contato", {}
    if "a2a" not in (p.get("channel_types") or []) or not p.get("a2a"):
        return "não expõe A2A (canal a2a + contrato)", {}
    contrato = p.get("a2a") or {}
    if kind not in (contrato.get("principal_kinds") or []):
        return f"o contrato A2A do pool não admite principal_kind '{kind}'", contrato
    return None, contrato


async def _assert_pools(claims: dict[str, Any], settings: Settings, pools: list[str]) -> None:
    fora = grants.violacoes(claims, accessible_pools=pools)
    if fora:
        raise HTTPException(status_code=403, detail="forbidden: você não pode conceder o que não detém. " + " · ".join(fora))
    tenant_id = _tenant(claims)
    ruins = []
    for p in pools:
        motivo = await _pool_exposes_a2a_to_partner(settings, tenant_id, p)
        if motivo:
            ruins.append(f"{p}: {motivo}")
    if ruins:
        raise HTTPException(status_code=422, detail={"reason": "pool_not_a2a_partner", "pools": ruins})


async def _trilha(pool: asyncpg.Pool, claims: dict[str, Any], row: dict[str, Any], acao: str, campos: list[str]) -> None:
    logger.info("administração de principal: %s -> %s (%s: %s)",
                claims.get("email") or claims.get("sub"), row["agent_principal_id"], acao, ",".join(campos) or "-")
    # A trilha de administração é a mesma das pessoas (AUT-39): `target_id` é o id do principal,
    # e a ação nomeia o tipo do alvo.
    await db_mod.registrar_admin_de_usuario(
        pool, tenant_id=str(claims.get("tenant_id") or ""), actor_id=str(claims.get("sub") or ""),
        actor_email=str(claims.get("email") or ""), target_id=str(row["agent_principal_id"]),
        target_email=f"principal:{row['display_name']}", acao=f"agent_principal.{acao}", campos=campos,
    )


# ── modelos ───────────────────────────────────────────────────────────────────

class CreatePrincipal(BaseModel):
    model_config = {"extra": "forbid"}
    display_name:  str       = Field(min_length=1, max_length=120)
    allowed_pools: list[str] = Field(min_length=1)


class UpdatePrincipal(BaseModel):
    model_config = {"extra": "forbid"}
    display_name:  str | None       = Field(default=None, min_length=1, max_length=120)
    allowed_pools: list[str] | None = Field(default=None, min_length=1)
    active:        bool | None      = None


class Introspect(BaseModel):
    credential: str = Field(min_length=1, max_length=512)


def _norm_pools(pools: list[str]) -> list[str]:
    out = sorted({p.strip() for p in pools})
    bad = [p for p in out if not _POOL_ID_RE.match(p)]
    if bad:
        raise HTTPException(status_code=422, detail={"reason": "pool_id_invalid", "pools": bad})
    return out


# ── rotas de administração ────────────────────────────────────────────────────

@agent_principals_router.get("")
async def list_principals(request: Request, claims: dict[str, Any] = Depends(_AGENTS_READ)) -> list[dict[str, Any]]:
    rows = await _pool(request).fetch(
        f"SELECT {_COLS} FROM auth.agent_principals WHERE tenant_id = $1 ORDER BY display_name",
        _tenant(claims))
    return [_out(r) for r in rows]


async def _service_caller(request: Request, settings: Settings = Depends(get_settings)) -> None:
    """Porta de SERVIÇO. É dependência, e não código no handler, porque dependência resolve ANTES
    do corpo: no handler, um anônimo sem corpo recebia 422 — a validação respondendo antes da
    credencial (medido pela varredura anônima da AUT-58)."""
    expected = settings.service_token
    if not expected:
        logger.error("introspecção de principal: PLUGHUB_AUTH_SERVICE_TOKEN vazio — nenhuma credencial é conferida")
        raise HTTPException(status_code=503, detail="introspecção não configurada")
    if not hmac.compare_digest(request.headers.get("x-service-token", ""), expected):
        raise HTTPException(status_code=401, detail="credencial de serviço ausente ou inválida")


@agent_principals_router.post("/introspect", dependencies=[Depends(_service_caller)])
async def introspect(body: Introspect, request: Request) -> dict[str, Any]:
    row = await _pool(request).fetchrow(
        f"SELECT {_COLS} FROM auth.agent_principals WHERE credential_hash = $1", hash_credential(body.credential))
    if row is None:
        logger.info("introspecção: credencial desconhecida (prefixo %s)", body.credential[:12])
        return {"active": False}
    if not row["active"]:
        logger.warning("introspecção: credencial de principal DESATIVADO %s (%s)", row["agent_principal_id"], row["display_name"])
        return {"active": False}
    if row.get("expires_at") is not None and row["expires_at"] <= datetime.now(timezone.utc):
        logger.info("introspecção: credencial VENCIDA do principal %s (%s), venceu em %s",
                    row["agent_principal_id"], row["display_name"], row["expires_at"].isoformat())
        return {"active": False}
    await _pool(request).execute(
        "UPDATE auth.agent_principals SET last_authenticated_at = now() WHERE agent_principal_id = $1",
        row["agent_principal_id"])
    d = _out(row)
    return {
        "active":             True,
        "sub":                d["agent_principal_id"],
        "subject_type":       "agent",
        "tenant_id":          d["tenant_id"],
        "kind":               d["kind"],
        "origin":             d["origin"],
        "display_name":       d["display_name"],
        "allowed_pools":      d["allowed_pools"],
        # AAS-09 — só no `customer_agent`; ausentes no `partner`
        **({
            "customer_id":       d["customer_id"],
            "proof_mechanism":   d["proof_mechanism"],
            "proof_verified_at": d["proof_verified_at"],
            "mandate":           d.get("mandate") or [],
            "exp":               int(row["expires_at"].timestamp()),
            "max_active_tasks":  d["max_active_tasks"],
            "max_tasks_per_day": d["max_tasks_per_day"],
        } if d["kind"] == "customer_agent" else {}),
    }


@agent_principals_router.get("/{principal_id}")
async def get_principal(principal_id: str, request: Request, claims: dict[str, Any] = Depends(_AGENTS_READ)) -> dict[str, Any]:
    row = await _fetch(request, claims, principal_id)
    return _out(row)


async def _fetch(request: Request, claims: dict[str, Any], principal_id: str) -> Any:
    try:
        row = await _pool(request).fetchrow(
            f"SELECT {_COLS} FROM auth.agent_principals WHERE agent_principal_id = $1::uuid AND tenant_id = $2",
            principal_id, _tenant(claims))
    except (asyncpg.DataError, ValueError):
        row = None
    if row is None:
        raise HTTPException(status_code=404, detail="principal não encontrado")
    return row


@agent_principals_router.post("", status_code=201)
async def create_principal(
    body: CreatePrincipal, request: Request,
    claims: dict[str, Any] = Depends(_AGENTS_WRITE), settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    pools = _norm_pools(body.allowed_pools)
    await _assert_pools(claims, settings, pools)
    plain, h, prefix = new_credential()
    row = await _pool(request).fetchrow(
        f"""INSERT INTO auth.agent_principals
              (tenant_id, kind, origin, display_name, allowed_pools, credential_hash, credential_prefix,
               credential_rotated_at, created_by)
            VALUES ($1, 'partner', 'external', $2, $3, $4, $5, now(), $6)
            RETURNING {_COLS}""",
        _tenant(claims), body.display_name.strip(), pools, h, prefix,
        str(claims.get("email") or claims.get("sub") or ""))
    await _trilha(_pool(request), claims, dict(row), "create", ["display_name", "allowed_pools", "credential"])
    return {**_out(row), "credential": plain}


@agent_principals_router.put("/{principal_id}")
async def update_principal(
    principal_id: str, body: UpdatePrincipal, request: Request,
    claims: dict[str, Any] = Depends(_AGENTS_WRITE), settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    atual = await _fetch(request, claims, principal_id)
    campos = sorted(body.model_fields_set)
    if not campos:
        raise HTTPException(status_code=400, detail="nenhum campo para atualizar")
    if atual["kind"] == "customer_agent" and set(campos) - {"active"}:
        # AAS-09 — o token do cliente nasceu da PROVA dele, com os pools e o mandato que ele
        # declarou. O admin do tenant pode DESLIGAR (revogar), nunca alargar nem renomear.
        raise HTTPException(status_code=409, detail="customer_agent: o admin só ativa ou desativa")
    pools = list(atual["allowed_pools"])
    if body.allowed_pools is not None:
        pools = _norm_pools(body.allowed_pools)
        # só o que ENTRA é concessão; tirar pool nunca exige deter o pool
        novos = sorted(set(pools) - set(atual["allowed_pools"]))
        if novos:
            await _assert_pools(claims, settings, novos)
    row = await _pool(request).fetchrow(
        f"""UPDATE auth.agent_principals
               SET display_name = COALESCE($2, display_name), allowed_pools = $3,
                   active = COALESCE($4, active), updated_at = now()
             WHERE agent_principal_id = $1
         RETURNING {_COLS}""",
        atual["agent_principal_id"], body.display_name.strip() if body.display_name else None, pools, body.active)
    await _trilha(_pool(request), claims, dict(row), "update", campos)
    return _out(row)


@agent_principals_router.post("/{principal_id}/credential")
async def rotate_credential(principal_id: str, request: Request, claims: dict[str, Any] = Depends(_AGENTS_WRITE)) -> dict[str, Any]:
    atual = await _fetch(request, claims, principal_id)
    if atual["kind"] == "customer_agent":
        # AAS-09 — rotacionar entregaria ao ADMIN uma credencial que fala em nome do cliente.
        raise HTTPException(status_code=409, detail="customer_agent: a credencial só nasce da prova do cliente")
    plain, h, prefix = new_credential()
    row = await _pool(request).fetchrow(
        f"""UPDATE auth.agent_principals
               SET credential_hash = $2, credential_prefix = $3, credential_rotated_at = now(), updated_at = now()
             WHERE agent_principal_id = $1
         RETURNING {_COLS}""",
        atual["agent_principal_id"], h, prefix)
    await _trilha(_pool(request), claims, dict(row), "rotate_credential", ["credential"])
    return {**_out(row), "credential": plain}


# ── AAS-09 — `customer_agent`: emissão, retirada e revogação (só serviço) ─────────────────────
#
# Quem chama é o mcp-server, pela tool que um fluxo do tenant invoca DEPOIS de a pessoa provar
# a posse NAQUELA sessão. O mcp-server julga a prova (tem a journey) e manda o titular; aqui não
# há usuário — o tenant vem no corpo, como em toda porta de serviço (TNT-01: serviço escolhe o
# tenant), e a régua dos pools é a do contrato A2A de cada pool, não a de quem concede.

class _ProofIn(BaseModel):
    model_config = {"extra": "forbid"}
    mechanism:   str = Field(min_length=1, max_length=40)
    verified_at: datetime
    session_id:  str = Field(min_length=1, max_length=200)


class CustomerGrantIn(BaseModel):
    model_config = {"extra": "forbid"}
    tenant_id:     str       = Field(min_length=1, max_length=120)
    customer_id:   str       = Field(min_length=1, max_length=200)
    proof:         _ProofIn
    allowed_pools: list[str] = Field(min_length=1, max_length=10)
    mandate:       list[str] = Field(default_factory=list, max_length=20)
    display_name:  str       = Field(default="assistente do cliente", min_length=1, max_length=120)


class RedeemIn(BaseModel):
    model_config = {"extra": "forbid"}
    tenant_id:   str = Field(min_length=1, max_length=120)
    pickup_code: str = Field(min_length=1, max_length=200)


class RevokeIn(BaseModel):
    model_config = {"extra": "forbid"}
    tenant_id:    str        = Field(min_length=1, max_length=120)
    customer_id:  str        = Field(min_length=1, max_length=200)
    principal_id: str | None = None


_MANDATE_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


async def _trilha_cliente(pool: asyncpg.Pool, tenant_id: str, customer_id: str, alvo: str, nome: str,
                          acao: str, campos: list[str]) -> None:
    """A trilha das pessoas (AUT-39), com o TITULAR como ator: é ele quem gera e revoga."""
    logger.info("customer_agent: %s por customer:%s -> %s (%s)", acao, customer_id, alvo, ",".join(campos) or "-")
    await db_mod.registrar_admin_de_usuario(
        pool, tenant_id=tenant_id, actor_id=f"customer:{customer_id}", actor_email="",
        target_id=alvo, target_email=f"principal:{nome}", acao=f"agent_principal.{acao}", campos=campos,
    )


@agent_principals_router.post("/customer-grants", status_code=201, dependencies=[Depends(_service_caller)])
async def create_customer_grant(
    body: CustomerGrantIn, request: Request, settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    if body.proof.mechanism not in PROOF_MECHANISMS:
        raise HTTPException(status_code=422, detail={"reason": "proof_mechanism_unknown", "mechanism": body.proof.mechanism})
    mandato = sorted({m.strip() for m in body.mandate if m.strip()})
    ruins = [m for m in mandato if not _MANDATE_RE.match(m)]
    if ruins:
        raise HTTPException(status_code=422, detail={"reason": "mandate_invalid", "items": ruins})
    pools = _norm_pools(body.allowed_pools)
    politicas: list[dict[str, Any]] = []
    recusas: list[str] = []
    for p in pools:
        motivo, contrato = await _pool_exposes_a2a(settings, body.tenant_id, p, "customer_agent")
        if motivo:
            recusas.append(f"{p}: {motivo}")
            continue
        pol = contrato.get("customer_agent")
        if not isinstance(pol, dict):
            # Sem política o pool não emite: validade e cota de token de consumidor são decisão
            # do tenant, e um default aqui seria inventá-las.
            recusas.append(f"{p}: o contrato A2A não declara a política customer_agent (validade e cota)")
            continue
        politicas.append(pol)
    if recusas:
        logger.warning("customer_agent: emissão RECUSADA para customer:%s — %s", body.customer_id, " · ".join(recusas))
        raise HTTPException(status_code=422, detail={"reason": "pool_not_customer_agent", "pools": recusas})
    # Vários pools num token só: vale a política MAIS RESTRITA de cada eixo.
    validade = min(int(x["validity_days"]) for x in politicas)
    ativas   = min(int(x["max_active_tasks"]) for x in politicas)
    por_dia  = min(int(x["max_tasks_per_day"]) for x in politicas)

    codigo = PICKUP_PREFIX + secrets.token_urlsafe(24)
    row = await _pool(request).fetchrow(
        """INSERT INTO auth.customer_agent_grants
              (tenant_id, pickup_hash, customer_id, proof_mechanism, proof_verified_at, proof_session_id,
               allowed_pools, mandate, validity_days, max_active_tasks, max_tasks_per_day, display_name,
               pickup_expires_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, now() + make_interval(secs => $13))
            RETURNING grant_id, pickup_expires_at""",
        body.tenant_id, hash_credential(codigo), body.customer_id, body.proof.mechanism,
        body.proof.verified_at, body.proof.session_id, pools, mandato, validade, ativas, por_dia,
        body.display_name.strip(), float(PICKUP_TTL_S))
    await _trilha_cliente(_pool(request), body.tenant_id, body.customer_id, str(row["grant_id"]),
                          body.display_name, "customer_grant", ["allowed_pools", "mandate"])
    return {
        "grant_id":          str(row["grant_id"]),
        "pickup_code":       codigo,
        "pickup_expires_at": row["pickup_expires_at"].isoformat(),
        "validity_days":     validade,
        "max_active_tasks":  ativas,
        "max_tasks_per_day": por_dia,
        "allowed_pools":     pools,
        "mandate":           mandato,
    }


@agent_principals_router.post("/customer-grants/redeem", dependencies=[Depends(_service_caller)])
async def redeem_customer_grant(body: RedeemIn, request: Request) -> dict[str, Any]:
    pool = _pool(request)
    async with pool.acquire() as conn:
        async with conn.transaction():
            # UPDATE condicional: duas retiradas simultâneas — uma só leva.
            g = await conn.fetchrow(
                """UPDATE auth.customer_agent_grants SET redeemed_at = now()
                    WHERE pickup_hash = $1 AND tenant_id = $2 AND redeemed_at IS NULL
                      AND pickup_expires_at > now()
                RETURNING *""",
                hash_credential(body.pickup_code), body.tenant_id)
            if g is None:
                # Inexistente, vencido e já retirado têm a MESMA resposta (quem tem só o código
                # não aprende qual); o motivo vai ao log.
                motivo = await conn.fetchrow(
                    "SELECT redeemed_at FROM auth.customer_agent_grants WHERE pickup_hash = $1",
                    hash_credential(body.pickup_code))
                logger.warning("customer_agent: retirada RECUSADA (%s)",
                               "desconhecido" if motivo is None else
                               ("já retirado" if motivo["redeemed_at"] else "vencido"))
                raise HTTPException(status_code=410, detail="pickup_unavailable")
            plain, h, prefix = new_credential()
            row = await conn.fetchrow(
                f"""INSERT INTO auth.agent_principals
                      (tenant_id, kind, origin, display_name, allowed_pools, credential_hash, credential_prefix,
                       credential_rotated_at, created_by, customer_id, proof_mechanism, proof_verified_at,
                       proof_session_id, mandate, expires_at, max_active_tasks, max_tasks_per_day)
                    VALUES ($1, 'customer_agent', 'external', $2, $3, $4, $5, now(), $6, $7, $8, $9, $10, $11,
                            now() + make_interval(days => $12), $13, $14)
                    RETURNING {_COLS}""",
                g["tenant_id"], g["display_name"], list(g["allowed_pools"]), h, prefix,
                f"customer:{g['customer_id']}", g["customer_id"], g["proof_mechanism"], g["proof_verified_at"],
                g["proof_session_id"], list(g["mandate"] or []), g["validity_days"],
                g["max_active_tasks"], g["max_tasks_per_day"])
            await conn.execute("UPDATE auth.customer_agent_grants SET principal_id = $2 WHERE grant_id = $1",
                               g["grant_id"], row["agent_principal_id"])
    d = _out(row)
    await _trilha_cliente(pool, d["tenant_id"], d["customer_id"], d["agent_principal_id"], d["display_name"],
                          "customer_redeem", ["credential"])
    return {**d, "credential": plain}


@agent_principals_router.post("/customer/revoke", dependencies=[Depends(_service_caller)])
async def revoke_customer_principals(body: RevokeIn, request: Request) -> dict[str, Any]:
    """Desativa os `customer_agent` ATIVOS deste titular (um, se `principal_id` vier). Só do titular:
    o `principal_id` de outro cliente não casa, e a resposta é a mesma de "nenhum"."""
    args: list[Any] = [body.tenant_id, body.customer_id]
    filtro = ""
    if body.principal_id:
        filtro = " AND agent_principal_id::text = $3"
        args.append(body.principal_id)
    rows = await _pool(request).fetch(
        f"""UPDATE auth.agent_principals SET active = false, updated_at = now()
             WHERE tenant_id = $1 AND customer_id = $2 AND kind = 'customer_agent' AND active{filtro}
         RETURNING agent_principal_id, display_name""", *args)
    for r in rows:
        await _trilha_cliente(_pool(request), body.tenant_id, body.customer_id, str(r["agent_principal_id"]),
                              r["display_name"], "customer_revoke", ["active"])
    return {"revoked": [str(r["agent_principal_id"]) for r in rows]}
