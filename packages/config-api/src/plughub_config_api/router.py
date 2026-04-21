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

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger("plughub.config.router")

router = APIRouter(prefix="/config")


# ─── auth dependency for mutations ───────────────────────────────────────────

async def _require_admin(
    request: Request,
    x_admin_token: Optional[str] = Header(default=None),
) -> None:
    """Simple static token guard for write operations."""
    from .config import get_settings
    settings = get_settings()
    expected = getattr(settings, "admin_token", None)
    if expected and x_admin_token != expected:
        raise HTTPException(status_code=401, detail="Invalid X-Admin-Token")


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

@router.put("/{namespace}/{key}", dependencies=[Depends(_require_admin)])
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
    """
    store = request.app.state.store
    await store.set(
        tenant_id   = body.tenant_id,
        namespace   = namespace,
        key         = key,
        value       = body.value,
        description = body.description,
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

@router.delete("/{namespace}/{key}", dependencies=[Depends(_require_admin)])
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
    deleted = await store.delete(tenant_id, namespace, key)
    if not deleted:
        raise HTTPException(
            status_code=404,
            detail=f"Config entry {namespace}.{key} not found "
                   f"(tenant={tenant_id or '__global__'})",
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
