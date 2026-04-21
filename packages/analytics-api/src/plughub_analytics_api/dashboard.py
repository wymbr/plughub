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

All endpoints require `tenant_id` query param.
No auth in Phase 1 — caller must supply tenant_id.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .query import get_metrics_24h, get_pool_snapshots, get_sentiment_live

logger = logging.getLogger("plughub.analytics.dashboard")

router = APIRouter(prefix="/dashboard")

_SSE_INTERVAL_S = 5   # seconds between SSE pushes
_SSE_RETRY_MS   = 3000


# ─── GET /dashboard/operational  (SSE) ───────────────────────────────────────

@router.get("/operational")
async def dashboard_operational(
    request:   Request,
    tenant_id: str = Query(..., description="Tenant identifier"),
) -> StreamingResponse:
    """
    Server-Sent Events stream of live pool operational snapshots.

    Each pool snapshot contains:
      pool_id, tenant_id, available, queue_length,
      sla_target_ms, channel_types, updated_at.

    The stream pushes every 5s or when the client disconnects.
    Snapshots older than 120s are naturally absent (Redis TTL).
    """
    redis = request.app.state.redis

    async def event_generator():
        yield f"retry: {_SSE_RETRY_MS}\n\n"
        try:
            while True:
                if await request.is_disconnected():
                    break
                snapshots = await get_pool_snapshots(redis, tenant_id)
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
    store = request.app.state.store
    data  = await get_metrics_24h(
        client   = store._client,
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
    redis = request.app.state.redis
    data  = await get_sentiment_live(redis, tenant_id)
    return JSONResponse(content=data)
