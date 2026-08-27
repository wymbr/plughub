"""
router.py
FastAPI router for the Config API.

Routes:
  GET  /config/{namespace}/{key}?tenant_id=xxx
       Resolved value (tenant override → global default). 404 if neither exists.

  GET  /config/{namespace}?tenant_id=xxx
       All resolved keys in namespace for the given tenant.

  GET  /config?tenant_id=xxx
       All resolved config for the tenant, grouped by namespace.

  PUT  /config/{namespace}/{key}
       Body: {"tenant_id": null|"...", "value": <any>, "description": "..."}
       Upsert. tenant_id=null sets the global platform default.

  DELETE /config/{namespace}/{key}?tenant_id=xxx
       Removes an explicit entry. Returns 404 if not found.
       tenant_id=null or omitted targets the global default.

  GET  /config/{namespace}/raw?tenant_id=xxx
       Raw (non-resolved) entries explicitly set for (tenant_id, namespace).
       Useful for seeing what overrides are active.

All mutation endpoints require the X-Admin-Token header matching PLUGHUB_CONFIG_ADMIN_TOKEN.
Read endpoints are unauthenticated (internal service — network-level access control applies).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger("plughub.config.router")

router = APIRouter(prefix="/config")


# ─── auth dependency for mutations (admin-token OR Bearer+ABAC) ───────────────
# G-PROBE platform-wide: o endpoint de escrita é genérico (`/{namespace}/{key}`) e
# compartilhado por várias telas. Cada namespace mapeia a um campo ABAC `config.*`.
# Gate DUAL (transição): admin-token (telas ainda não migradas) OU Bearer + ABAC do
# campo do namespace (telas migradas: Platform→`plataforma`, Masking→`masking`).

_ACCESS_RANK = {"none": 0, "read_only": 1, "write_only": 1, "read_write": 2}

# namespaces de canais (telas ainda em admin-token) → campo `canais` (path Bearer correto,
# inativo até a migração de Channels). masking → `masking`. Default (Platform é o editor
# catch-all de config de plataforma) → `plataforma`.
_NS_FIELD_OVERRIDES = {
    "masking":      "masking",
    "audit_policy": "masking",   # MaskingPage edita masking + audit_policy → mesmo campo
    "webchat":  "channels", "webhook": "channels", "sms": "channels", "whatsapp": "channels", "voice": "channels", "webrtc": "channels",
    # Passo 2 do arco de ABAC total (2026-08-27): Dashboards deixa de cair no
    # catch-all `platform`. O MENU passa a apontar para o mesmo campo — a divergência
    # menu×backend do Channels (menu em `platform`, backend em `channels`) é o defeito
    # que este passo fecha, e repeti-lo aqui seria criá-lo de novo.
    "dashboards":   "dashboards",
}


def _ns_field(namespace: str) -> str:
    return _NS_FIELD_OVERRIDES.get(namespace, "platform")


def _b64url_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _verify_hs256(token: str, secret: str) -> dict[str, Any]:
    try:
        h, p, sig = token.split(".")
    except ValueError:
        raise HTTPException(status_code=401, detail="malformed token")
    expected = base64.urlsafe_b64encode(
        hmac.new(secret.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest()
    ).rstrip(b"=").decode()
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(status_code=401, detail="invalid token signature")
    payload = json.loads(_b64url_decode(p))
    if payload.get("exp") and int(payload["exp"]) < int(time.time()):
        raise HTTPException(status_code=401, detail="token expired")
    return payload


def _check_config_field(claims: dict[str, Any], field: str, min_access: str) -> bool:
    mc = claims.get("module_config") or {}
    fc = (mc.get("config") or {}).get(field) or {}
    access = fc.get("access", "none")
    if access == "none":
        return False
    return _ACCESS_RANK.get(access, 0) >= _ACCESS_RANK.get(min_access, 0)


async def _require_config_write(
    namespace: str,
    request: Request,
    x_admin_token: Optional[str] = Header(default=None),
    admin_token: Optional[str] = Query(default=None, alias="admin_token"),
) -> None:
    """Write guard: admin-token (back-compat) OR Bearer + ABAC `config.{ns_field}` (read_write).

    Preserva a postura original: admin_token vazio = auth desabilitada (internal-only).
    """
    from .config import get_settings
    settings = get_settings()
    expected = getattr(settings, "admin_token", None)
    if not expected:
        return  # auth desabilitada (deploy interno) — comportamento original preservado
    token = x_admin_token or admin_token
    if token == expected:
        return  # admin-token válido (telas legadas / seed / sistema)
    # Caminho Bearer+ABAC (telas migradas): exige JWT válido + campo do namespace.
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing admin token or Bearer")
    if not getattr(settings, "jwt_secret", ""):
        raise HTTPException(status_code=503, detail="jwt secret not configured")
    claims = _verify_hs256(auth[len("Bearer "):], settings.jwt_secret)
    field = _ns_field(namespace)
    if not _check_config_field(claims, field, "read_write"):
        raise HTTPException(status_code=403, detail=f"forbidden: requires config.{field} (read_write)")


# ─── request models ──────────────────────────────────────────────────────────

class PutConfigBody(BaseModel):
    value:       Any
    tenant_id:   Optional[str] = None   # None → global default
    description: str           = ""


# ─── GET /config/{namespace}/{key} ───────────────────────────────────────────

@router.get("/{namespace}/{key}")
async def get_config(
    namespace: str,
    key:       str,
    request:   Request,
    tenant_id: str = Query(..., description="Tenant to resolve config for"),
) -> JSONResponse:
    """
    Returns the resolved value for (tenant_id, namespace, key).
    Applies two-level lookup: tenant-specific → global default.
    404 if no value exists at either level.
    """
    store = request.app.state.store
    value = await store.get(tenant_id, namespace, key)
    if value is None:
        raise HTTPException(
            status_code=404,
            detail=f"No config found for {namespace}.{key} "
                   f"(tenant={tenant_id}, no global default either)",
        )
    return JSONResponse(content={
        "tenant_id": tenant_id,
        "namespace": namespace,
        "key":       key,
        "value":     value,
    })


# ─── GET /config/{namespace} ─────────────────────────────────────────────────

@router.get("/{namespace}")
async def list_namespace(
    namespace: str,
    request:   Request,
    tenant_id: str = Query(..., description="Tenant to resolve config for"),
) -> JSONResponse:
    """
    All resolved keys in a namespace for the given tenant.
    Returns {key: resolved_value}. Empty dict if namespace has no entries.
    """
    store = request.app.state.store
    data  = await store.list_namespace(tenant_id, namespace)
    return JSONResponse(content={
        "tenant_id": tenant_id,
        "namespace": namespace,
        "entries":   data,
    })


# ─── GET /config ─────────────────────────────────────────────────────────────

@router.get("")
async def list_all(
    request:   Request,
    tenant_id: str = Query(..., description="Tenant to resolve config for"),
) -> JSONResponse:
    """
    All resolved config for a tenant, grouped by namespace.
    Not cached — for admin/diagnostic use.
    """
    store = request.app.state.store
    data  = await store.list_all(tenant_id)
    return JSONResponse(content={
        "tenant_id": tenant_id,
        "config":    data,
    })


# ─── PUT /config/{namespace}/{key} ───────────────────────────────────────────

@router.put("/{namespace}/{key}", dependencies=[Depends(_require_config_write)])
async def put_config(
    namespace: str,
    key:       str,
    body:      PutConfigBody,
    request:   Request,
) -> JSONResponse:
    """
    Upsert a config value.
    body.tenant_id = null  → sets global platform default.
    body.tenant_id = "xyz" → sets tenant-specific override.
    Publishes config.changed to Kafka after a successful write (fire-and-forget).
    """
    store   = request.app.state.store
    emitter = request.app.state.emitter
    await store.set(
        tenant_id   = body.tenant_id,
        namespace   = namespace,
        key         = key,
        value       = body.value,
        description = body.description,
    )
    # Fire-and-forget — never let Kafka failure affect the HTTP response
    await emitter.emit_config_changed(
        tenant_id = body.tenant_id,
        namespace = namespace,
        key       = key,
        operation = "set",
    )
    effective_tenant = body.tenant_id or "__global__"
    return JSONResponse(
        status_code=200,
        content={
            "ok":        True,
            "tenant_id": effective_tenant,
            "namespace": namespace,
            "key":       key,
        },
    )


# ─── DELETE /config/{namespace}/{key} ────────────────────────────────────────

@router.delete("/{namespace}/{key}", dependencies=[Depends(_require_config_write)])
async def delete_config(
    namespace: str,
    key:       str,
    request:   Request,
    tenant_id: Optional[str] = Query(
        default=None,
        description="Tenant whose override to remove. Omit to delete the global default.",
    ),
) -> JSONResponse:
    """
    Removes an explicit config entry.
    - Deleting a tenant override restores the global default for that tenant.
    - Deleting the global entry leaves all tenants without a fallback (returns null).
    """
    store   = request.app.state.store
    emitter = request.app.state.emitter
    deleted = await store.delete(tenant_id, namespace, key)
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail=f"Config entry {namespace}.{key} not found "
                   f"(tenant={tenant_id or '__global__'})",
        )
    await emitter.emit_config_changed(
        tenant_id = tenant_id,
        namespace = namespace,
        key       = key,
        operation = "delete",
    )
    return JSONResponse(content={"ok": True, "deleted": True})


# ─── GET /config/{namespace}/raw ─────────────────────────────────────────────

@router.get("/{namespace}/raw")
async def list_namespace_raw(
    namespace: str,
    request:   Request,
    tenant_id: str = Query(..., description="Which tenant's explicit entries to list"),
) -> JSONResponse:
    """
    Raw (non-resolved) entries explicitly set for (tenant_id, namespace).
    Shows what is overriding the global default for a specific tenant.
    Pass tenant_id='__global__' to see the global defaults themselves.
    """
    store   = request.app.state.store
    entries = await store.list_namespace_raw(tenant_id, namespace)
    return JSONResponse(content={
        "tenant_id": tenant_id,
        "namespace": namespace,
        "entries":   entries,
    })
