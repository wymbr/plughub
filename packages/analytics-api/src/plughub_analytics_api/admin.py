"""
admin.py
FastAPI router for the admin consolidated endpoint.

Routes:
  GET /admin/consolidated
      Aggregated cross-tenant (admin) or single-tenant (operator) view.
      Auth: Bearer JWT (HS256, secret from settings.admin_jwt_secret)

      Query params:
        from_dt   ISO8601, optional (default: 24h ago)
        to_dt     ISO8601, optional (default: now)

      Response:
        {
          "scope":   "all_tenants" | "<tenant_id>",
          "period":  {"from": "...", "to": "..."},
          "by_channel": [
            {
              "tenant_id": "...",
              "channel": "webchat",
              "sessions": 150,
              "avg_handle_ms": 42000,
              "by_outcome": {"resolved": 100, "transferred": 50}
            }, ...
          ],
          "by_pool": [
            {
              "tenant_id": "...",
              "pool_id": "retencao_humano",
              "sessions": 120,
              "avg_handle_ms": 38000,
              "avg_sentiment": 0.35,
              "sentiment_sample_count": 450
            }, ...
          ]
        }

RBAC:
  admin role    → sees all tenants; tenant_id is not required
  operator role → sees only their own tenant (tenant_id from JWT, not query param)
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from .admin_query import query_consolidated
from .auth import Principal, require_principal

logger = logging.getLogger("plughub.analytics.admin")

router = APIRouter(prefix="/admin")


@router.get("/consolidated")
async def admin_consolidated(
    request:   Request,
    from_dt:   Optional[str] = Query(None, description="ISO8601 start (default: 24h ago)"),
    to_dt:     Optional[str] = Query(None, description="ISO8601 end (default: now)"),
    principal: Principal     = Depends(require_principal),
) -> JSONResponse:
    """
    Cross-tenant aggregated view (admin) or single-tenant view (operator).

    Admins receive data across all tenants.
    Operators receive only their own tenant's data — the `tenant_id` is taken
    from the JWT and cannot be overridden by the caller.
    """
    store = request.app.state.store

    # operator's effective tenant comes from the JWT; admin has no restriction
    effective_tenant = principal.effective_tenant(None)

    data = await query_consolidated(
        client    = store._client,
        database  = store._database,
        tenant_id = effective_tenant,
        from_dt   = from_dt,
        to_dt     = to_dt,
    )
    status_code = 503 if data.get("error") else 200
    return JSONResponse(content=data, status_code=status_code)
