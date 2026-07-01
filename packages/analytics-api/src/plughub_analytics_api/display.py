"""
display.py
FastAPI router for the /reports/display/* endpoints.

These endpoints feed the new-format dashboard cards (Part 4 of Dashboard #35).
Each endpoint returns a data shape compatible with one or more DisplayTools
defined in platform-ui/src/dashboard/tools/.

Routes (16 total):
  GET /reports/display/session-volume            → LineChartData  (bar/line)
  GET /reports/display/handle-time               → LineChartData  (line/bar)
  GET /reports/display/evaluation-score          → LineChartData  (line/bar)
  GET /reports/display/sessions-by-pool          → BarChartData   (bar)
  GET /reports/display/outcome-distribution      → DonutData      (donut)
  GET /reports/display/pool-status               → TableData      (table)
  GET /reports/display/agent-performance         → TableData      (table)
  GET /reports/display/kpi-sessions              → MetricCardData (metric_card)
  GET /reports/display/kpi-resolution            → MetricCardData (metric_card)
  GET /reports/display/kpi-score                 → MetricCardData (metric_card)
  GET /reports/display/agent-event-timeseries    → LineChartData  (line_chart)
  GET /reports/display/agent-event-summary       → BarChartData   (bar_chart)

Common query params:
  tenant_id   string   required
  from        date     optional  ISO8601 or YYYY-MM-DD (default: 7 days ago)
  to          date     optional  ISO8601 or YYYY-MM-DD (default: now)
  pool_id     string   optional  fixed pool filter

Auth: Bearer JWT via optional_pool_principal (same as /reports/* endpoints).
Pool RBAC: accessible_pools from JWT restricts visible pool data.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from .display_formatters import (
    fmt_agent_availability,
    fmt_agent_event_summary,
    fmt_agent_event_timeseries,
    fmt_agent_performance,
    fmt_pools_queue,
    fmt_volume_by_channel,
    fmt_evaluation_score,
    fmt_handle_time,
    fmt_kpi_resolution,
    fmt_kpi_score,
    fmt_kpi_sessions,
    fmt_outcome_distribution,
    fmt_pool_status,
    fmt_session_volume,
    fmt_sessions_by_pool,
)
from .pool_auth import PoolPrincipal, optional_pool_principal

logger = logging.getLogger("plughub.analytics.display")

router = APIRouter(prefix="/reports/display")


# ─── GET /reports/display/session-volume ─────────────────────────────────────

@router.get("/session-volume")
async def display_session_volume(
    request:        Request,
    tenant_id:      str           = Query(...,   description="Tenant identifier"),
    from_:          Optional[str] = Query(None,  alias="from", description="Period start (ISO8601 or YYYY-MM-DD)"),
    to:             Optional[str] = Query(None,  description="Period end (ISO8601 or YYYY-MM-DD)"),
    pool_id:        Optional[str] = Query(None,  description="Filter by pool_id"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> JSONResponse:
    """Session volume per time bucket — compatible with bar_chart and line_chart."""
    store = request.app.state.store
    data  = await fmt_session_volume(
        client           = store.new_client(),
        database         = store._database,
        tenant_id        = tenant_id,
        from_dt          = from_,
        to_dt            = to,
        pool_id          = pool_id,
        accessible_pools = pool_principal.accessible_pools,
    )
    return JSONResponse(content=data)


# ─── GET /reports/display/handle-time ────────────────────────────────────────

@router.get("/handle-time")
async def display_handle_time(
    request:        Request,
    tenant_id:      str           = Query(...,   description="Tenant identifier"),
    from_:          Optional[str] = Query(None,  alias="from", description="Period start"),
    to:             Optional[str] = Query(None,  description="Period end"),
    pool_id:        Optional[str] = Query(None,  description="Filter by pool_id"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> JSONResponse:
    """Average handle time (ms) per time bucket — compatible with line_chart and bar_chart."""
    store = request.app.state.store
    data  = await fmt_handle_time(
        client           = store.new_client(),
        database         = store._database,
        tenant_id        = tenant_id,
        from_dt          = from_,
        to_dt            = to,
        pool_id          = pool_id,
        accessible_pools = pool_principal.accessible_pools,
    )
    return JSONResponse(content=data)


# ─── GET /reports/display/evaluation-score ───────────────────────────────────

@router.get("/evaluation-score")
async def display_evaluation_score(
    request:        Request,
    tenant_id:      str           = Query(...,   description="Tenant identifier"),
    from_:          Optional[str] = Query(None,  alias="from", description="Period start"),
    to:             Optional[str] = Query(None,  description="Period end"),
    pool_id:        Optional[str] = Query(None,  description="Ignored — evaluation_results has no pool_id"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> JSONResponse:
    """Average evaluation score per time bucket — compatible with line_chart and bar_chart."""
    store = request.app.state.store
    data  = await fmt_evaluation_score(
        client           = store.new_client(),
        database         = store._database,
        tenant_id        = tenant_id,
        from_dt          = from_,
        to_dt            = to,
        pool_id          = pool_id,
        accessible_pools = pool_principal.accessible_pools,
    )
    return JSONResponse(content=data)


# ─── GET /reports/display/sessions-by-pool ───────────────────────────────────

@router.get("/sessions-by-pool")
async def display_sessions_by_pool(
    request:        Request,
    tenant_id:      str           = Query(...,   description="Tenant identifier"),
    from_:          Optional[str] = Query(None,  alias="from", description="Period start"),
    to:             Optional[str] = Query(None,  description="Period end"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> JSONResponse:
    """Session count grouped by pool — compatible with bar_chart."""
    store = request.app.state.store
    data  = await fmt_sessions_by_pool(
        client           = store.new_client(),
        database         = store._database,
        tenant_id        = tenant_id,
        from_dt          = from_,
        to_dt            = to,
        accessible_pools = pool_principal.accessible_pools,
    )
    return JSONResponse(content=data)


# ─── GET /reports/display/outcome-distribution ───────────────────────────────

@router.get("/outcome-distribution")
async def display_outcome_distribution(
    request:        Request,
    tenant_id:      str           = Query(...,   description="Tenant identifier"),
    from_:          Optional[str] = Query(None,  alias="from", description="Period start"),
    to:             Optional[str] = Query(None,  description="Period end"),
    pool_id:        Optional[str] = Query(None,  description="Filter by pool_id"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> JSONResponse:
    """Session outcome proportions — compatible with donut."""
    store = request.app.state.store
    data  = await fmt_outcome_distribution(
        client           = store.new_client(),
        database         = store._database,
        tenant_id        = tenant_id,
        from_dt          = from_,
        to_dt            = to,
        pool_id          = pool_id,
        accessible_pools = pool_principal.accessible_pools,
    )
    return JSONResponse(content=data)


# ─── GET /reports/display/pool-status ────────────────────────────────────────

@router.get("/pool-status")
async def display_pool_status(
    request:        Request,
    tenant_id:      str           = Query(...,   description="Tenant identifier"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> JSONResponse:
    """
    Live pool operational status from Redis snapshots — compatible with table.

    Returns current data at query time (snapshots TTL 120s). Not filterable
    by period since this is operational/live data, not historical.
    """
    redis = request.app.state.redis
    data  = await fmt_pool_status(redis=redis, tenant_id=tenant_id)
    return JSONResponse(content=data)


# ─── GET /reports/display/agent-performance ──────────────────────────────────

@router.get("/agent-performance")
async def display_agent_performance(
    request:        Request,
    tenant_id:      str           = Query(...,   description="Tenant identifier"),
    from_:          Optional[str] = Query(None,  alias="from", description="Period start"),
    to:             Optional[str] = Query(None,  description="Period end"),
    pool_id:        Optional[str] = Query(None,  description="Filter by pool_id"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> JSONResponse:
    """Per-agent resolution and escalation rates — compatible with table."""
    store = request.app.state.store
    data  = await fmt_agent_performance(
        client           = store.new_client(),
        database         = store._database,
        tenant_id        = tenant_id,
        from_dt          = from_,
        to_dt            = to,
        pool_id          = pool_id,
        accessible_pools = pool_principal.accessible_pools,
    )
    return JSONResponse(content=data)


# ─── GET /reports/display/agent-availability ─────────────────────────────────

@router.get("/agent-availability")
async def display_agent_availability(
    request:        Request,
    tenant_id:      str           = Query(...,   description="Tenant identifier"),
    from_:          Optional[str] = Query(None,  alias="from", description="Period start"),
    to:             Optional[str] = Query(None,  description="Period end"),
    pool_id:        Optional[str] = Query(None,  description="Filter by pool_id"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> JSONResponse:
    """Human agent pauses per instance/pool/day (Arc 8) — compatible with table."""
    store = request.app.state.store
    data  = await fmt_agent_availability(
        client           = store.new_client(),
        database         = store._database,
        tenant_id        = tenant_id,
        from_dt          = from_,
        to_dt            = to,
        pool_id          = pool_id,
        accessible_pools = pool_principal.accessible_pools,
    )
    return JSONResponse(content=data)


# ─── GET /reports/display/pools-queue ────────────────────────────────────────

@router.get("/pools-queue")
async def display_pools_queue(
    request:        Request,
    tenant_id:      str           = Query(...,   description="Tenant identifier"),
    from_:          Optional[str] = Query(None,  alias="from", description="Period start"),
    to:             Optional[str] = Query(None,  description="Period end"),
    pool_id:        Optional[str] = Query(None,  description="Filter by pool_id"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> JSONResponse:
    """Queue + SLA aggregated per pool (queue-attended model) — compatible with table."""
    store = request.app.state.store
    data  = await fmt_pools_queue(
        client           = store.new_client(),
        database         = store._database,
        tenant_id        = tenant_id,
        from_dt          = from_,
        to_dt            = to,
        pool_id          = pool_id,
        accessible_pools = pool_principal.accessible_pools,
    )
    return JSONResponse(content=data)


# ─── GET /reports/display/volume-by-channel ──────────────────────────────────

@router.get("/volume-by-channel")
async def display_volume_by_channel(
    request:        Request,
    tenant_id:      str           = Query(...,   description="Tenant identifier"),
    from_:          Optional[str] = Query(None,  alias="from", description="Period start"),
    to:             Optional[str] = Query(None,  description="Period end"),
    pool_id:        Optional[str] = Query(None,  description="Filter by pool_id"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> JSONResponse:
    """Contact volume split by channel — compatible with donut."""
    store = request.app.state.store
    data  = await fmt_volume_by_channel(
        client           = store.new_client(),
        database         = store._database,
        tenant_id        = tenant_id,
        from_dt          = from_,
        to_dt            = to,
        pool_id          = pool_id,
        accessible_pools = pool_principal.accessible_pools,
    )
    return JSONResponse(content=data)


# ─── GET /reports/display/kpi-sessions ───────────────────────────────────────

@router.get("/kpi-sessions")
async def display_kpi_sessions(
    request:        Request,
    tenant_id:      str           = Query(...,   description="Tenant identifier"),
    from_:          Optional[str] = Query(None,  alias="from", description="Period start"),
    to:             Optional[str] = Query(None,  description="Period end"),
    pool_id:        Optional[str] = Query(None,  description="Filter by pool_id"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> JSONResponse:
    """Total sessions KPI with trend vs prior period — compatible with metric_card."""
    store = request.app.state.store
    data  = await fmt_kpi_sessions(
        client           = store.new_client(),
        database         = store._database,
        tenant_id        = tenant_id,
        from_dt          = from_,
        to_dt            = to,
        pool_id          = pool_id,
        accessible_pools = pool_principal.accessible_pools,
    )
    return JSONResponse(content=data)


# ─── GET /reports/display/kpi-resolution ─────────────────────────────────────

@router.get("/kpi-resolution")
async def display_kpi_resolution(
    request:        Request,
    tenant_id:      str           = Query(...,   description="Tenant identifier"),
    from_:          Optional[str] = Query(None,  alias="from", description="Period start"),
    to:             Optional[str] = Query(None,  description="Period end"),
    pool_id:        Optional[str] = Query(None,  description="Filter by pool_id"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> JSONResponse:
    """Resolution rate KPI with trend vs prior period — compatible with metric_card."""
    store = request.app.state.store
    data  = await fmt_kpi_resolution(
        client           = store.new_client(),
        database         = store._database,
        tenant_id        = tenant_id,
        from_dt          = from_,
        to_dt            = to,
        pool_id          = pool_id,
        accessible_pools = pool_principal.accessible_pools,
    )
    return JSONResponse(content=data)


# ─── GET /reports/display/kpi-score ──────────────────────────────────────────

@router.get("/kpi-score")
async def display_kpi_score(
    request:        Request,
    tenant_id:      str           = Query(...,   description="Tenant identifier"),
    from_:          Optional[str] = Query(None,  alias="from", description="Period start"),
    to:             Optional[str] = Query(None,  description="Period end"),
    pool_id:        Optional[str] = Query(None,  description="Ignored — evaluation_results has no pool_id"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> JSONResponse:
    """Average evaluation score KPI with trend vs prior period — compatible with metric_card."""
    store = request.app.state.store
    data  = await fmt_kpi_score(
        client           = store.new_client(),
        database         = store._database,
        tenant_id        = tenant_id,
        from_dt          = from_,
        to_dt            = to,
        pool_id          = pool_id,
        accessible_pools = pool_principal.accessible_pools,
    )
    return JSONResponse(content=data)


# journey display endpoints — REMOVED (Arc 19 Fase F)
# journey-active-count, journey-resolution-rate, journey-funnel, journey-median-duration
# Journey entity superseded by Arc 19 unified session model.


# ─── GET /reports/display/agent-event-timeseries ─────────────────────────────

@router.get("/agent-event-timeseries")
async def display_agent_event_timeseries(
    request:        Request,
    tenant_id:      str           = Query(...,   description="Tenant identifier"),
    from_:          Optional[str] = Query(None,  alias="from", description="Period start"),
    to:             Optional[str] = Query(None,  description="Period end"),
    category:       Optional[str] = Query(None,  description="Category prefix filter (dot-notation, e.g. pool.skill.metric)"),
    pool_id:        Optional[str] = Query(None,  description="Filter by pool_id (category_l1)"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> JSONResponse:
    """Daily count and average value for agent business events — compatible with line_chart.

    Series returned: ['Total', 'Média'].
    Requires `category` or `pool_id` to scope the query; returns empty series otherwise.
    """
    store = request.app.state.store
    data  = await fmt_agent_event_timeseries(
        client    = store.new_client(),
        database  = store._database,
        tenant_id = tenant_id,
        from_dt   = from_,
        to_dt     = to,
        category  = category,
        pool_id   = pool_id,
    )
    return JSONResponse(content=data)


# ─── GET /reports/display/agent-event-summary ────────────────────────────────

@router.get("/agent-event-summary")
async def display_agent_event_summary(
    request:        Request,
    tenant_id:      str           = Query(...,   description="Tenant identifier"),
    from_:          Optional[str] = Query(None,  alias="from", description="Period start"),
    to:             Optional[str] = Query(None,  description="Period end"),
    category:       Optional[str] = Query(None,  description="Category prefix filter"),
    pool_id:        Optional[str] = Query(None,  description="Filter by pool_id (category_l1)"),
    group_by:       Optional[str] = Query("category", description="Grouping key: category|skill_id|pool_id|agent_type_id"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> JSONResponse:
    """Aggregated event counts grouped by a dimension — compatible with bar_chart.

    Default group_by is 'category'. Top 20 groups returned, sorted by event count desc.
    """
    store = request.app.state.store
    data  = await fmt_agent_event_summary(
        client    = store.new_client(),
        database  = store._database,
        tenant_id = tenant_id,
        from_dt   = from_,
        to_dt     = to,
        category  = category,
        pool_id   = pool_id,
        group_by  = group_by or "category",
    )
    return JSONResponse(content=data)
