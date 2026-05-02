"""
router.py
REST endpoints for the Pricing API.

Endpoints:
  GET  /v1/pricing/invoice/{tenant_id}                  — invoice for current or given cycle
  GET  /v1/pricing/invoice/{tenant_id}?format=xlsx      — XLSX export
  GET  /v1/pricing/resources/{tenant_id}                — list installation resources
  POST /v1/pricing/resources/{tenant_id}                — upsert resource
  DELETE /v1/pricing/resources/{tenant_id}/{resource_id} — remove resource
  POST /v1/pricing/reserve/{tenant_id}/{pool_id}/activate
  POST /v1/pricing/reserve/{tenant_id}/{pool_id}/deactivate
  GET  /v1/pricing/reserve/{tenant_id}/activity         — activation log
  GET  /health
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Annotated

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import db as pricing_db
from .calculator import PricingCalculator, invoice_to_xlsx, load_price_table
from .config import Settings, get_settings

logger = logging.getLogger("plughub.pricing.router")

router = APIRouter()


# ─── Dependency helpers ────────────────────────────────────────────────────────

def get_pool(request: Request) -> asyncpg.Pool:
    return request.app.state.pg_pool


def require_admin(
    x_admin_token: Annotated[str | None, Header(alias="X-Admin-Token")] = None,
    settings: Settings = Depends(get_settings),
) -> None:
    if settings.admin_token and x_admin_token != settings.admin_token:
        raise HTTPException(status_code=403, detail="Invalid admin token")


# ─── Invoice ──────────────────────────────────────────────────────────────────

@router.get("/v1/pricing/invoice/{tenant_id}")
async def get_invoice(
    tenant_id:       str,
    installation_id: str   = Query(default="default"),
    cycle_start:     str | None = Query(default=None, description="YYYY-MM-DD"),
    cycle_end:       str | None = Query(default=None, description="YYYY-MM-DD"),
    format:          str   = Query(default="json", pattern="^(json|xlsx)$"),
    pool:            asyncpg.Pool = Depends(get_pool),
    settings:        Settings     = Depends(get_settings),
):
    """
    Returns the invoice for the given tenant/installation and billing cycle.
    Defaults to the current calendar month.
    Pass format=xlsx to download an Excel file.
    """
    start = date.fromisoformat(cycle_start) if cycle_start else None
    end   = date.fromisoformat(cycle_end)   if cycle_end   else None

    price_table = await load_price_table(settings.config_api_url, tenant_id)
    calc        = PricingCalculator(pool, price_table)
    invoice     = await calc.calculate(tenant_id, installation_id, start, end)

    if format == "xlsx":
        xlsx_bytes = invoice_to_xlsx(invoice)
        filename   = f"invoice_{tenant_id}_{invoice.cycle_start}.xlsx"
        return Response(
            content     = xlsx_bytes,
            media_type  = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers     = {"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return JSONResponse(invoice.to_dict())


# ─── Resources ────────────────────────────────────────────────────────────────

@router.get("/v1/pricing/resources/{tenant_id}")
async def list_resources(
    tenant_id:       str,
    installation_id: str = Query(default="default"),
    pool: asyncpg.Pool   = Depends(get_pool),
):
    resources = await pricing_db.list_resources(pool, tenant_id, installation_id)
    return {"tenant_id": tenant_id, "installation_id": installation_id, "resources": resources}


class UpsertResourceBody(BaseModel):
    installation_id: str        = Field(default="default")
    resource_type:   str        = Field(..., description="ai_agent | human_agent | whatsapp_number | ...")
    quantity:        int        = Field(..., ge=0)
    pool_type:       str        = Field(default="base", pattern="^(base|reserve)$")
    reserve_pool_id: str | None = Field(default=None)
    billing_unit:    str        = Field(default="monthly", pattern="^(monthly|daily)$")
    label:           str        = Field(default="")


@router.post("/v1/pricing/resources/{tenant_id}", dependencies=[Depends(require_admin)])
async def upsert_resource(
    tenant_id: str,
    body: UpsertResourceBody,
    pool: asyncpg.Pool = Depends(get_pool),
):
    resource = await pricing_db.upsert_resource(
        pool,
        tenant_id       = tenant_id,
        installation_id = body.installation_id,
        resource_type   = body.resource_type,
        quantity        = body.quantity,
        pool_type       = body.pool_type,
        reserve_pool_id = body.reserve_pool_id,
        billing_unit    = body.billing_unit,
        label           = body.label,
    )
    return resource


@router.delete(
    "/v1/pricing/resources/{tenant_id}/{resource_id}",
    dependencies=[Depends(require_admin)],
)
async def delete_resource(
    tenant_id:   str,
    resource_id: str,
    pool: asyncpg.Pool = Depends(get_pool),
):
    deleted = await pricing_db.delete_resource(pool, resource_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Resource not found")
    return {"deleted": True}


# ─── Reserve pool activation / deactivation ───────────────────────────────────

@router.post(
    "/v1/pricing/reserve/{tenant_id}/{pool_id}/activate",
    dependencies=[Depends(require_admin)],
)
async def activate_reserve(
    tenant_id: str,
    pool_id:   str,
    activated_by: str = Query(default="operator"),
    pool: asyncpg.Pool = Depends(get_pool),
):
    """
    Activates a reserve pool:
    1. Sets active=TRUE on all resources in the pool.
    2. Logs today as an activation date (full-day billing starts today).
    """
    updated = await pricing_db.set_reserve_active(pool, tenant_id, pool_id, active=True)
    if updated == 0:
        raise HTTPException(status_code=404, detail=f"Reserve pool '{pool_id}' not found for tenant")
    log = await pricing_db.record_activation(pool, tenant_id, pool_id, activated_by)
    return {"activated": True, "pool_id": pool_id, "resources_updated": updated, "log": log}


@router.post(
    "/v1/pricing/reserve/{tenant_id}/{pool_id}/deactivate",
    dependencies=[Depends(require_admin)],
)
async def deactivate_reserve(
    tenant_id: str,
    pool_id:   str,
    pool: asyncpg.Pool = Depends(get_pool),
):
    """
    Deactivates a reserve pool:
    1. Sets active=FALSE on all resources in the pool.
    2. Closes open activation log records (deactivation_date = today).
    Today is still billable (full-day model).
    """
    updated = await pricing_db.set_reserve_active(pool, tenant_id, pool_id, active=False)
    if updated == 0:
        raise HTTPException(status_code=404, detail=f"Reserve pool '{pool_id}' not found for tenant")
    await pricing_db.record_deactivation(pool, tenant_id, pool_id)
    return {"deactivated": True, "pool_id": pool_id, "resources_updated": updated}


# ─── Activation log ───────────────────────────────────────────────────────────

@router.get("/v1/pricing/reserve/{tenant_id}/activity")
async def get_activation_log(
    tenant_id:       str,
    reserve_pool_id: str | None = Query(default=None),
    limit:           int        = Query(default=100, ge=1, le=500),
    pool: asyncpg.Pool = Depends(get_pool),
):
    logs = await pricing_db.list_activation_log(pool, tenant_id, reserve_pool_id, limit)
    return {"tenant_id": tenant_id, "logs": logs, "count": len(logs)}


# ─── Health ───────────────────────────────────────────────────────────────────

@router.get("/health")
async def health(pool: asyncpg.Pool = Depends(get_pool)):
    try:
        await pool.fetchval("SELECT 1")
        return {"status": "ok", "service": "pricing-api"}
    except Exception as exc:
        raise HTTPException(status_code=503, detail=str(exc))
