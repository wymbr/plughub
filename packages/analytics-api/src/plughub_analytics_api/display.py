"""
display.py
FastAPI router for the /reports/display/* endpoints.

These endpoints feed the new-format dashboard cards (Part 4 of Dashboard #35).
Each endpoint returns a data shape compatible with one or more DisplayTools
defined in platform-ui/src/dashboard/tools/.

Routes (14 total):
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
  GET /reports/display/journey-active-count      → MetricCardData (metric_card)
  GET /reports/display/journey-resolution-rate   → BarChartData   (bar_chart)
  GET /reports/display/journey-funnel            → DonutData      (donut)
  GET /reports/display/journey-median-duration   → BarChartData   (bar_chart)

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
    fmt_agent_performance,
    fmt_evaluation_score,
    fmt_handle_time,
    fmt_journey_active_count,
    fmt_journey_funnel,
    fmt_journey_median_duration,
    fmt_journey_resolution_rate,
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


# ─── GET /reports/display/journey-active-count ───────────────────────────────

@router.get("/journey-active-count")
async def display_journey_active_count(
    request:        Request,
    tenant_id:      str           = Query(...,  description="Tenant identifier"),
    from_:          Optional[str] = Query(None, alias="from", description="Period start"),
    to:             Optional[str] = Query(None, description="Period end"),
    skill_id:       Optional[str] = Query(None, description="Filter by skill_id"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> JSONResponse:
    """Count of active journeys in period with trend — compatible with metric_card."""
    store = request.app.state.store
    data  = await fmt_journey_active_count(
        client    = store.new_client(),
        database  = store._database,
        tenant_id = tenant_id,
        from_dt   = from_,
        to_dt     = to,
        skill_id  = skill_id,
    )
    return JSONResponse(content=data)


# ─── GET /reports/display/journey-resolution-rate ────────────────────────────

@router.get("/journey-resolution-rate")
async def display_journey_resolution_rate(
    request:        Request,
    tenant_id:      str           = Query(...,  description="Tenant identifier"),
    from_:          Optional[str] = Query(None, alias="from", description="Period start"),
    to:             Optional[str] = Query(None, description="Period end"),
    skill_id:       Optional[str] = Query(None, description="Filter by skill_id"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> JSONResponse:
    """Journey resolution rate % per skill_id (terminal journeys only) — compatible with bar_chart."""
    store = request.app.state.store
    data  = await fmt_journey_resolution_rate(
        client    = store.new_client(),
        database  = store._database,
        tenant_id = tenant_id,
        from_dt   = from_,
        to_dt     = to,
        skill_id  = skill_id,
    )
    return JSONResponse(content=data)


# ─── GET /reports/display/journey-funnel ─────────────────────────────────────

@router.get("/journey-funnel")
async def display_journey_funnel(
    request:        Request,
    tenant_id:      str           = Query(...,  description="Tenant identifier"),
    from_:          Optional[str] = Query(None, alias="from", description="Period start"),
    to:             Optional[str] = Query(None, description="Period end"),
    skill_id:       Optional[str] = Query(None, description="Filter by skill_id"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> JSONResponse:
    """Journey distribution by status (active/suspended/completed/failed/cancelled) — compatible with donut."""
    store = request.app.state.store
    data  = await fmt_journey_funnel(
        client    = store.new_client(),
        database  = store._database,
        tenant_id = tenant_id,
        from_dt   = from_,
        to_dt     = to,
        skill_id  = skill_id,
    )
    return JSONResponse(content=data)


# ─── GET /reports/display/journey-median-duration ────────────────────────────

@router.get("/journey-median-duration")
async def display_journey_median_duration(
    request:        Request,
    tenant_id:      str           = Query(...,  description="Tenant identifier"),
    from_:          Optional[str] = Query(None, alias="from", description="Period start"),
    to:             Optional[str] = Query(None, description="Period end"),
    skill_id:       Optional[str] = Query(None, description="Filter by skill_id"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> JSONResponse:
    """Journey p50 duration in minutes per skill_id (terminal journeys) — compatible with bar_chart."""
    store = request.app.state.store
    data  = await fmt_journey_median_duration(
        client    = store.new_client(),
        database  = store._database,
        tenant_id = tenant_id,
        from_dt   = from_,
        to_dt     = to,
        skill_id  = skill_id,
    )
    return JSONResponse(content=data)
