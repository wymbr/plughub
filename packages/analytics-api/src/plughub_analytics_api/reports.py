"""
reports.py
FastAPI router for the four /reports/* endpoints.

Routes:
  GET /reports/sessions   — session list with filters
  GET /reports/agents     — agent event list with filters
  GET /reports/quality    — sentiment event list with filters
  GET /reports/usage      — usage event list with filters

Common query params (all endpoints):
  tenant_id     string   required
  from_dt       ISO8601  optional, default: 7 days ago
  to_dt         ISO8601  optional, default: now
  page          int      optional, default: 1
  page_size     int      optional, default: 100; max 1000 (JSON) / 10000 (CSV)
  format        json|csv optional, default: json

Endpoint-specific filter params are documented per endpoint below.

CSV response:
  Content-Type: text/csv
  Content-Disposition: attachment; filename="{report}_{date}.csv"
  Body: RFC 4180 CSV (header row + data rows)
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, Response

from .reports_query import (
    _clamp_page_size,
    _to_csv,
    query_agents_report,
    query_quality_report,
    query_sessions_report,
    query_usage_report,
)

logger = logging.getLogger("plughub.analytics.reports")

router = APIRouter(prefix="/reports")


# ─── helpers ──────────────────────────────────────────────────────────────────

def _today_label() -> str:
    from datetime import datetime
    return datetime.utcnow().strftime("%Y-%m-%d")


def _respond(data: dict, fmt: str, filename: str) -> Response:
    if fmt == "csv":
        csv_body = _to_csv(data.get("data", []))
        return Response(
            content=csv_body,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    status_code = 503 if data.get("error") else 200
    # Include meta at top level alongside data
    return JSONResponse(content=data, status_code=status_code)


# ─── GET /reports/sessions ────────────────────────────────────────────────────

@router.get("/sessions")
async def report_sessions(
    request:      Request,
    tenant_id:    str           = Query(...,    description="Tenant identifier"),
    from_dt:      Optional[str] = Query(None,   description="ISO8601 start (default: 7d ago)"),
    to_dt:        Optional[str] = Query(None,   description="ISO8601 end (default: now)"),
    channel:      Optional[str] = Query(None,   description="Filter by channel"),
    outcome:      Optional[str] = Query(None,   description="Filter by session outcome"),
    close_reason: Optional[str] = Query(None,   description="Filter by close_reason"),
    pool_id:      Optional[str] = Query(None,   description="Filter by pool_id"),
    page:         int           = Query(1,       ge=1),
    page_size:    int           = Query(100,     ge=1),
    format:       str           = Query("json",  pattern="^(json|csv)$"),
) -> Response:
    """
    Session list for the given tenant and time window.

    Columns: session_id, tenant_id, channel, pool_id, opened_at, closed_at,
             close_reason, outcome, wait_time_ms, handle_time_ms
    """
    ps = _clamp_page_size(page_size, format == "csv")
    data = await query_sessions_report(
        client    = request.app.state.store._client,
        database  = request.app.state.store._database,
        tenant_id = tenant_id,
        from_dt   = from_dt,
        to_dt     = to_dt,
        channel      = channel,
        outcome      = outcome,
        close_reason = close_reason,
        pool_id      = pool_id,
        page      = page,
        page_size = ps,
    )
    return _respond(data, format, f"sessions_{_today_label()}.csv")


# ─── GET /reports/agents ──────────────────────────────────────────────────────

@router.get("/agents")
async def report_agents(
    request:       Request,
    tenant_id:     str           = Query(...,   description="Tenant identifier"),
    from_dt:       Optional[str] = Query(None,  description="ISO8601 start (default: 7d ago)"),
    to_dt:         Optional[str] = Query(None,  description="ISO8601 end (default: now)"),
    agent_type_id: Optional[str] = Query(None,  description="Filter by agent_type_id"),
    pool_id:       Optional[str] = Query(None,  description="Filter by pool_id"),
    event_type:    Optional[str] = Query(None,  description="Filter by event_type (routed|agent_done)"),
    outcome:       Optional[str] = Query(None,  description="Filter by outcome"),
    page:          int           = Query(1,      ge=1),
    page_size:     int           = Query(100,    ge=1),
    format:        str           = Query("json", pattern="^(json|csv)$"),
) -> Response:
    """
    Agent event list. Useful for per-agent performance analysis.

    Columns: event_id, tenant_id, session_id, agent_type_id, pool_id, instance_id,
             event_type, outcome, handoff_reason, handle_time_ms, routing_mode, timestamp
    """
    ps = _clamp_page_size(page_size, format == "csv")
    data = await query_agents_report(
        client    = request.app.state.store._client,
        database  = request.app.state.store._database,
        tenant_id = tenant_id,
        from_dt   = from_dt,
        to_dt     = to_dt,
        agent_type_id = agent_type_id,
        pool_id       = pool_id,
        event_type    = event_type,
        outcome       = outcome,
        page      = page,
        page_size = ps,
    )
    return _respond(data, format, f"agents_{_today_label()}.csv")


# ─── GET /reports/quality ─────────────────────────────────────────────────────

@router.get("/quality")
async def report_quality(
    request:   Request,
    tenant_id: str           = Query(...,   description="Tenant identifier"),
    from_dt:   Optional[str] = Query(None,  description="ISO8601 start (default: 7d ago)"),
    to_dt:     Optional[str] = Query(None,  description="ISO8601 end (default: now)"),
    pool_id:   Optional[str] = Query(None,  description="Filter by pool_id"),
    category:  Optional[str] = Query(None,  description="Filter by sentiment category"),
    page:      int           = Query(1,      ge=1),
    page_size: int           = Query(100,    ge=1),
    format:    str           = Query("json", pattern="^(json|csv)$"),
) -> Response:
    """
    Per-turn sentiment event list. Useful for CSAT quality analysis.

    category filter: satisfied | neutral | frustrated | angry

    Columns: event_id, tenant_id, session_id, pool_id, score, category, timestamp
    """
    ps = _clamp_page_size(page_size, format == "csv")
    data = await query_quality_report(
        client    = request.app.state.store._client,
        database  = request.app.state.store._database,
        tenant_id = tenant_id,
        from_dt   = from_dt,
        to_dt     = to_dt,
        pool_id   = pool_id,
        category  = category,
        page      = page,
        page_size = ps,
    )
    return _respond(data, format, f"quality_{_today_label()}.csv")


# ─── GET /reports/usage ───────────────────────────────────────────────────────

@router.get("/usage")
async def report_usage(
    request:          Request,
    tenant_id:        str           = Query(...,   description="Tenant identifier"),
    from_dt:          Optional[str] = Query(None,  description="ISO8601 start (default: 7d ago)"),
    to_dt:            Optional[str] = Query(None,  description="ISO8601 end (default: now)"),
    dimension:        Optional[str] = Query(None,  description="Filter by dimension"),
    source_component: Optional[str] = Query(None,  description="Filter by source_component"),
    page:             int           = Query(1,      ge=1),
    page_size:        int           = Query(100,    ge=1),
    format:           str           = Query("json", pattern="^(json|csv)$"),
) -> Response:
    """
    Raw usage event list. Useful for billing and metering BI exports.

    dimension filter: sessions | messages | llm_tokens_input | llm_tokens_output |
                      webchat_attachments | whatsapp_conversations | ...

    Columns: event_id, tenant_id, session_id, dimension, quantity, source_component, timestamp
    """
    ps = _clamp_page_size(page_size, format == "csv")
    data = await query_usage_report(
        client    = request.app.state.store._client,
        database  = request.app.state.store._database,
        tenant_id = tenant_id,
        from_dt   = from_dt,
        to_dt     = to_dt,
        dimension        = dimension,
        source_component = source_component,
        page      = page,
        page_size = ps,
    )
    return _respond(data, format, f"usage_{_today_label()}.csv")
