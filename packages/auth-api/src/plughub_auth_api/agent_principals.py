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
         "credential_rotated_at, active, created_by, created_at, updated_at, last_authenticated_at")


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
    for k in ("credential_rotated_at", "created_at", "updated_at", "last_authenticated_at"):
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
    url = f"{settings.agent_registry_url.rstrip('/')}/v1/pools/{pool_id}"
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            r = await c.get(url, headers={"x-tenant-id": tenant_id})
    except httpx.HTTPError as exc:
        logger.error("principal: registry inalcançável conferindo o pool %s (%s) — recusando", pool_id, exc)
        raise HTTPException(status_code=503, detail="agent-registry indisponível; concessão de pool recusada") from exc
    if r.status_code == 404:
        return "não existe"
    if r.status_code != 200:
        logger.error("principal: registry respondeu %s para o pool %s — recusando", r.status_code, pool_id)
        raise HTTPException(status_code=503, detail="agent-registry respondeu erro; concessão de pool recusada")
    p = r.json()
    if (p.get("purpose") or "contact") != "contact":
        return "não é pool de contato"
    if "a2a" not in (p.get("channel_types") or []) or not p.get("a2a"):
        return "não expõe A2A (canal a2a + contrato)"
    if "partner" not in ((p.get("a2a") or {}).get("principal_kinds") or []):
        return "o contrato A2A do pool não admite principal_kind 'partner'"
    return None


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
    plain, h, prefix = new_credential()
    row = await _pool(request).fetchrow(
        f"""UPDATE auth.agent_principals
               SET credential_hash = $2, credential_prefix = $3, credential_rotated_at = now(), updated_at = now()
             WHERE agent_principal_id = $1
         RETURNING {_COLS}""",
        atual["agent_principal_id"], h, prefix)
    await _trilha(_pool(request), claims, dict(row), "rotate_credential", ["credential"])
    return {**_out(row), "credential": plain}
