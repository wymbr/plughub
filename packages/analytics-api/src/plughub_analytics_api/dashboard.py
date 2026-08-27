"""
dashboard.py
FastAPI router for the three dashboard endpoints.

Routes:
  GET /dashboard/operational
      SSE stream of Redis pool snapshots, refreshed every 5 seconds.
      Query param: tenant_id (required)
      Format: text/event-stream
        event: pools
        data: [{"pool_id":…, "available":N, "queue_length":N, …}]
        id: <unix_timestamp>
        retry: 3000

  GET /dashboard/metrics
      Last 24 hours aggregated metrics from ClickHouse.
      Query param: tenant_id (required)
      Returns: sessions, agent_events, usage, sentiment aggregates.

  GET /dashboard/sentiment
      Current per-pool sentiment aggregate from Redis.
      Query param: tenant_id (required)
      Returns: list of {pool_id, avg_score, count, distribution, updated_at}.

All endpoints require a valid Bearer JWT (same RBAC as /admin/*).
Operators are restricted to their own tenant_id; admins may query any tenant.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, StreamingResponse

from .auth import Principal, require_principal
from .pool_auth import accessible_pools_from_request
from .query import (
    get_metrics_24h, get_pool_sla_1h,
    get_pool_snapshots, get_sentiment_live,
)


def _filter_by_pool(rows: list, accessible: list[str] | None) -> list:
    """Segurança Fase D — restringe uma lista de dicts com `pool_id` ao domínio.
    `accessible=None` (irrestrito/sem token) → passa tudo."""
    if accessible is None:
        return rows
    allowed = set(accessible)
    return [r for r in rows if isinstance(r, dict) and r.get("pool_id") in allowed]

logger = logging.getLogger("plughub.analytics.dashboard")

router = APIRouter(prefix="/dashboard")

_SSE_INTERVAL_S = 5   # seconds between SSE pushes
_SSE_RETRY_MS   = 3000


# ─── GET /dashboard/operational  (SSE) ───────────────────────────────────────

@router.get("/operational")
async def dashboard_operational(
    request:   Request,
    tenant_id: str = Query(..., description="Tenant identifier"),
    token:     str | None = Query(None, description="auth-api Bearer (SSE query param — EventSource não manda header) p/ pool-scoping"),
    principal: Principal = Depends(require_principal),
) -> StreamingResponse:
    """
    Server-Sent Events stream of live pool operational snapshots.

    Each pool snapshot contains:
      pool_id, tenant_id, available, queue_length,
      sla_target_ms, channel_types, updated_at.

    The stream pushes every 5s or when the client disconnects.
    Snapshots older than 120s are naturally absent (Redis TTL).
    """
    effective = principal.effective_tenant(tenant_id)
    if effective != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied for requested tenant",
        )
    redis = request.app.state.redis
    # Segurança Fase D — domínio de pools do chamador (Bearer via query param, pois
    # EventSource não envia header). None = irrestrito (sem token/segredo/inválido).
    accessible = accessible_pools_from_request(request, token)

    async def event_generator():
        yield f"retry: {_SSE_RETRY_MS}\n\n"
        try:
            while True:
                if await request.is_disconnected():
                    break
                snapshots = _filter_by_pool(await get_pool_snapshots(redis, tenant_id), accessible)
                event_id  = int(time.time())
                yield (
                    f"event: pools\n"
                    f"id: {event_id}\n"
                    f"data: {json.dumps(snapshots)}\n\n"
                )
                await asyncio.sleep(_SSE_INTERVAL_S)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning("SSE error tenant=%s: %s", tenant_id, exc)
            yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",   # disable nginx buffering
        },
    )


# ─── GET /dashboard/metrics ───────────────────────────────────────────────────

@router.get("/metrics")
async def dashboard_metrics(
    request:   Request,
    tenant_id: str = Query(..., description="Tenant identifier"),
    principal: Principal = Depends(require_principal),
) -> JSONResponse:
    """
    Aggregated metrics for the last 24 hours (ClickHouse).

    Response structure:
    {
      "period":    "last_24h",
      "tenant_id": "...",
      "sessions": {
        "total": N,
        "avg_handle_ms": N | null,
        "by_channel": {"webchat": N, ...},
        "by_outcome": {"resolved": N, ...},
        "by_close_reason": {"flow_complete": N, ...}
      },
      "agent_events": {
        "total_routed": N,
        "total_done": N,
        "by_outcome": {"resolved": N, ...}
      },
      "usage": { "by_dimension": {"llm_tokens_input": N, ...} },
      "sentiment": {
        "avg_score": F | null,
        "sample_count": N,
        "by_category": {"satisfied": N, ...}
      }
    }
    """
    effective = principal.effective_tenant(tenant_id)
    if effective != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied for requested tenant",
        )
    store = request.app.state.store
    data  = await get_metrics_24h(
        client   = store.new_client(),
        database = store._database,
        tenant_id = tenant_id,
    )
    status_code = 503 if data.get("error") else 200
    return JSONResponse(content=data, status_code=status_code)


# ─── GET /dashboard/sentiment ─────────────────────────────────────────────────

@router.get("/sentiment")
async def dashboard_sentiment(
    request:   Request,
    tenant_id: str = Query(..., description="Tenant identifier"),
    token:     str | None = Query(None, description="auth-api Bearer p/ pool-scoping"),
    principal: Principal = Depends(require_principal),
) -> JSONResponse:
    """
    Current per-pool sentiment aggregate (Redis, TTL 300s).

    Response: list of pool sentiment entries:
    [
      {
        "pool_id": "retencao_humano",
        "tenant_id": "tenant_telco",
        "avg_score": 0.42,
        "count": 37,
        "distribution": {
          "satisfied": 20, "neutral": 10, "frustrated": 5, "angry": 2
        },
        "last_session_id": "sess-...",
        "updated_at": "2026-..."
      },
      ...
    ]
    Returns empty list when no live data is available.
    """
    effective = principal.effective_tenant(tenant_id)
    if effective != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied for requested tenant",
        )
    redis = request.app.state.redis
    data  = _filter_by_pool(await get_sentiment_live(redis, tenant_id),
                            accessible_pools_from_request(request, token))
    return JSONResponse(content=data)


# ─── GET /dashboard/pool-sla ──────────────────────────────────────────────────

@router.get("/pool-sla")
async def dashboard_pool_sla(
    request:   Request,
    tenant_id: str = Query(..., description="Tenant identifier"),
    token:     str | None = Query(None, description="auth-api Bearer p/ pool-scoping"),
    principal: Principal = Depends(require_principal),
) -> JSONResponse:
    """
    Per-pool SLA performance for the last 1 hour (ClickHouse).

    Response: list of pool SLA entries:
    [
      {
        "pool_id":              "demo_ia",
        "avg_wait_ms":          4200.0,
        "p90_wait_ms":          9800.0,
        "sla_compliance_pct":   91.3,   // % of sessions served within sla_target_ms
        "sessions_count":       47
      },
      ...
    ]
    Returns empty list when no sessions in the last hour or ClickHouse is unavailable.
    sla_compliance_pct is null when sessions have no sla_target_ms set.
    """
    effective = principal.effective_tenant(tenant_id)
    if effective != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied for requested tenant",
        )
    store = request.app.state.store
    data  = await get_pool_sla_1h(
        client    = store.new_client(),
        database  = store._database,
        tenant_id = tenant_id,
    )
    data  = _filter_by_pool(data, accessible_pools_from_request(request, token))
    return JSONResponse(content=data)


# GET /dashboard/pool-journeys — REMOVED (Arc 19 Fase F)
# Journey entity superseded by Arc 19 unified session model.
