"""
router.py
REST endpoints for the Pricing API.

Endpoints:
  GET  /v1/pricing/invoice/{tenant_id}                  — invoice for current or given cycle
  GET  /v1/pricing/invoice/{tenant_id}?format=xlsx      — XLSX export
  GET  /v1/pricing/capacity/{tenant_id}                 — configured capacity by resource_type
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
import hmac

from plughub_authz import abac_can, bearer_from_header, verify_user_jwt
from pydantic import BaseModel, Field

from . import db as pricing_db
from .calculator import PricingCalculator, invoice_to_xlsx, load_price_table
from .config import Settings, get_settings
from .quota_sync import sync_tenant

logger = logging.getLogger("plughub.pricing.router")

router = APIRouter()


# ─── Dependency helpers ────────────────────────────────────────────────────────

def get_pool(request: Request) -> asyncpg.Pool:
    return request.app.state.pg_pool


def get_redis(request: Request):
    """Redis do quota sync (None quando PLUGHUB_PRICING_REDIS_URL não setada)."""
    return getattr(request.app.state, "redis", None)


# Portão — história curta. Até 2026-08-28 este arquivo tinha um verificador HS256 próprio
# (stdlib); migrou para `plughub_authz.enforce_write`, que trouxe duas decisões que FICAM:
# credencial ausente é 401 (não 403 — "não sei quem é" ≠ "sei, e não pode"), e o campo
# nomeado na recusa é o que o portão confere. Em 2026-09-29 (AUT-61) o `enforce_write` saiu
# daqui: ele desliga o portão com `admin_token` vazio e não compara o tenant do caminho, e
# as LEITURAS não tinham portão nenhum. Ver `_gate`.
def _gate(request: Request, settings: Settings, grants: tuple, *, service_ok: bool, what: str) -> None:
    """Portão de TODA rota de pricing (AUT-61, 2026-09-29).

    Medido antes, pela borda pública (5174), sem credencial: `GET /v1/pricing/invoice/{t}`
    respondia 200 com a fatura de qualquer tenant; recursos, capacidade e o log de reservas
    também. As escritas tinham o `enforce_write`, mas com DOIS furos: `admin_token` vazio
    desligava o portão, e o tenant do CAMINHO nunca era comparado ao do token.

    Portas, nesta ordem:
      1. `X-Admin-Token` (sistema: `pricing-seed`) — só se configurado; vazio FECHA a porta.
      2. `X-Service-Token` (só nas LEITURAS com `service_ok`) — a analytics-api.
      3. `Bearer` + algum dos `grants` de `billing`, e o `tenant_id` do caminho = o do token.

    O catálogo decide o mapa (`infra/modules.yaml`, módulo `billing`):
    `visualizar` = "Visualizar fatura e recursos"; `gerenciar` = "Gerenciar recursos e ativar
    reservas". A escrita deixou `config.platform`, o campo que o gate usava sem que o
    catálogo o dissesse. Medido antes de trocar: os dois portadores de `config.platform`
    (`admin@` e a fixture `probe@`) têm `billing.gerenciar` — a troca não revoga ninguém.
    """
    adm = request.headers.get("x-admin-token")
    if adm and settings.admin_token and hmac.compare_digest(adm, settings.admin_token):
        return
    svc = request.headers.get("x-service-token")
    if service_ok and svc and settings.service_token and hmac.compare_digest(svc, settings.service_token):
        return
    token = bearer_from_header(request.headers.get("authorization"))
    if not token:
        raise HTTPException(status_code=401, detail=f"{what} exige credencial")
    if not settings.jwt_secret:
        raise HTTPException(
            status_code=503,
            detail="pricing-api sem PLUGHUB_PRICING_JWT_SECRET — nao consigo verificar credencial",
        )
    claims = verify_user_jwt(token, settings.jwt_secret)
    if not claims:
        raise HTTPException(status_code=401, detail="credencial invalida ou expirada")
    if not any(abac_can(claims, "billing", campo, minimo) for campo, minimo in grants):
        pedido = " ou ".join(f"billing.{c} ({m})" for c, m in grants)
        logger.warning("pricing NEGADO: sub=%s %s — sem %s", claims.get("sub"), what, pedido)
        raise HTTPException(status_code=403, detail=f"{what} exige {pedido}")
    token_tenant = str(claims.get("tenant_id") or "")
    if not token_tenant:
        raise HTTPException(status_code=401, detail="credencial sem tenant")
    pedido_tenant = request.path_params.get("tenant_id")
    if pedido_tenant != token_tenant:
        logger.warning("pricing RECUSA: sub=%s pediu tenant=%s com credencial de %s (%s)",
                       claims.get("sub"), pedido_tenant, token_tenant, what)
        raise HTTPException(status_code=403, detail="tenant_mismatch")


VER       = (("visualizar", "read_only"), ("gerenciar", "read_write"))
GERENCIAR = (("gerenciar", "read_write"),)


def require_read(request: Request, settings: Settings = Depends(get_settings)) -> None:
    _gate(request, settings, VER, service_ok=True, what="leitura de pricing/billing")


def require_admin(request: Request, settings: Settings = Depends(get_settings)) -> None:
    """Escrita. Nome mantido: é o que as rotas e os testes já referenciam."""
    _gate(request, settings, GERENCIAR, service_ok=False, what="escrita de pricing/billing")


# ─── Invoice ──────────────────────────────────────────────────────────────────

@router.get("/v1/pricing/invoice/{tenant_id}", dependencies=[Depends(require_read)])
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

@router.get("/v1/pricing/resources/{tenant_id}", dependencies=[Depends(require_read)])
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
    redis = Depends(get_redis),
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
    await sync_tenant(redis, pool, tenant_id)   # C mudou → re-grava quota de admissão
    return resource


@router.delete(
    "/v1/pricing/resources/{tenant_id}/{resource_id}",
    dependencies=[Depends(require_admin)],
)
async def delete_resource(
    tenant_id:   str,
    resource_id: str,
    pool: asyncpg.Pool = Depends(get_pool),
    redis = Depends(get_redis),
):
    deleted = await pricing_db.delete_resource(pool, tenant_id, resource_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Resource not found")
    await sync_tenant(redis, pool, tenant_id)   # C mudou → re-grava quota de admissão
    return {"deleted": True}


# ─── Configured capacity (Fase 2 — Pools/Infra report) ───────────────────────

@router.get("/v1/pricing/capacity/{tenant_id}", dependencies=[Depends(require_read)])
async def get_capacity(
    tenant_id:       str,
    installation_id: str = Query(default="default"),
    pool: asyncpg.Pool   = Depends(get_pool),
):
    """
    Capacidade configurada (contratada) por resource_type: base + reservas ativas.
    `agent_capacity_total` (ai_agent + human_agent) é o denominador do total na
    aba Capacidade do Analytics/Pools (per-pool segue a capacidade provisionada).
    """
    return await pricing_db.get_capacity(pool, tenant_id, installation_id)


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
    redis = Depends(get_redis),
):
    """
    Activates a reserve pool:
    1. Sets active=TRUE on all resources in the pool.
    2. Logs today as an activation date (full-day billing starts today).
    3. Re-syncs the admission quota (C = base + active reserves).
    """
    updated = await pricing_db.set_reserve_active(pool, tenant_id, pool_id, active=True)
    if updated == 0:
        raise HTTPException(status_code=404, detail=f"Reserve pool '{pool_id}' not found for tenant")
    log = await pricing_db.record_activation(pool, tenant_id, pool_id, activated_by)
    await sync_tenant(redis, pool, tenant_id)
    return {"activated": True, "pool_id": pool_id, "resources_updated": updated, "log": log}


@router.post(
    "/v1/pricing/reserve/{tenant_id}/{pool_id}/deactivate",
    dependencies=[Depends(require_admin)],
)
async def deactivate_reserve(
    tenant_id: str,
    pool_id:   str,
    pool: asyncpg.Pool = Depends(get_pool),
    redis = Depends(get_redis),
):
    """
    Deactivates a reserve pool:
    1. Sets active=FALSE on all resources in the pool.
    2. Closes open activation log records (deactivation_date = today).
    Today is still billable (full-day model).
    3. Re-syncs the admission quota (C shrinks — reduction always accepted).
    """
    updated = await pricing_db.set_reserve_active(pool, tenant_id, pool_id, active=False)
    if updated == 0:
        raise HTTPException(status_code=404, detail=f"Reserve pool '{pool_id}' not found for tenant")
    await pricing_db.record_deactivation(pool, tenant_id, pool_id)
    await sync_tenant(redis, pool, tenant_id)
    return {"deactivated": True, "pool_id": pool_id, "resources_updated": updated}


# ─── Activation log ───────────────────────────────────────────────────────────

@router.get("/v1/pricing/reserve/{tenant_id}/activity", dependencies=[Depends(require_read)])
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
