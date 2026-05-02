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

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response

from .pool_auth import PoolPrincipal, optional_pool_principal
from .reports_query import (
    _clamp_page_size,
    _to_csv,
    query_agent_performance_daily,
    query_agent_performance_report,
    query_agents_report,
    query_campaigns_report,
    query_contact_insights_report,
    query_evaluations_report,
    query_evaluations_summary,
    query_participation_report,
    query_quality_report,
    query_segments_report,
    query_agent_availability,
    query_session_complexity,
    query_sessions_report,
    query_usage_report,
    query_workflows_report,
)
from .timeseries_query import (
    query_handle_time_timeseries,
    query_score_timeseries,
    query_volume_timeseries,
    timeseries_to_csv,
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
    request:          Request,
    tenant_id:        str           = Query(...,    description="Tenant identifier"),
    from_dt:          Optional[str] = Query(None,   description="ISO8601 start (default: 7d ago)"),
    to_dt:            Optional[str] = Query(None,   description="ISO8601 end (default: now)"),
    channel:          Optional[str] = Query(None,   description="Filter by channel"),
    outcome:          Optional[str] = Query(None,   description="Filter by session outcome"),
    close_reason:     Optional[str] = Query(None,   description="Filter by close_reason"),
    pool_id:          Optional[str] = Query(None,   description="Filter by pool_id"),
    session_id:       Optional[str] = Query(None,   description="Filter by exact session_id"),
    agent_id:         Optional[str] = Query(None,   description="Filter by agent participant_id (any segment)"),
    insight_category: Optional[str] = Query(None,   description="Filter: sessions with this insight category"),
    insight_tags:     Optional[str] = Query(None,   description="Comma-separated insight tags (AND logic)"),
    ani:              Optional[str] = Query(None,   description="Filter by ANI/source identifier (partial match)"),
    dnis:             Optional[str] = Query(None,   description="Filter by DNIS/destination identifier (partial match)"),
    page:             int           = Query(1,       ge=1),
    page_size:        int           = Query(100,     ge=1),
    format:           str           = Query("json",  pattern="^(json|csv)$"),
    pool_principal:   PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Session list for the given tenant and time window.

    Columns: session_id, tenant_id, channel, pool_id, customer_id,
             opened_at, closed_at, close_reason, outcome,
             wait_time_ms, handle_time_ms, ani, dnis, segment_count
    """
    ps = _clamp_page_size(page_size, format == "csv")
    tags_list = [t.strip() for t in insight_tags.split(",") if t.strip()] if insight_tags else None
    data = await query_sessions_report(
        client    = request.app.state.store.new_client(),
        database  = request.app.state.store._database,
        tenant_id = tenant_id,
        from_dt   = from_dt,
        to_dt     = to_dt,
        channel          = channel,
        outcome          = outcome,
        close_reason     = close_reason,
        pool_id          = pool_id,
        session_id       = session_id,
        agent_id         = agent_id,
        insight_category = insight_category,
        insight_tags     = tags_list,
        accessible_pools = pool_principal.accessible_pools,
        ani              = ani,
        dnis             = dnis,
        page      = page,
        page_size = ps,
    )
    return _respond(data, format, f"sessions_{_today_label()}.csv")


# ─── GET /reports/agents ──────────────────────────────────────────────────────

@router.get("/agents")
async def report_agents(
    request:        Request,
    tenant_id:      str           = Query(...,   description="Tenant identifier"),
    from_dt:        Optional[str] = Query(None,  description="ISO8601 start (default: 7d ago)"),
    to_dt:          Optional[str] = Query(None,  description="ISO8601 end (default: now)"),
    agent_type_id:  Optional[str] = Query(None,  description="Filter by agent_type_id"),
    pool_id:        Optional[str] = Query(None,  description="Filter by pool_id"),
    event_type:     Optional[str] = Query(None,  description="Filter by event_type (routed|agent_done)"),
    outcome:        Optional[str] = Query(None,  description="Filter by outcome"),
    page:           int           = Query(1,      ge=1),
    page_size:      int           = Query(100,    ge=1),
    format:         str           = Query("json", pattern="^(json|csv)$"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Agent event list. Useful for per-agent performance analysis.

    Columns: event_id, tenant_id, session_id, agent_type_id, pool_id, instance_id,
             event_type, outcome, handoff_reason, handle_time_ms, routing_mode, timestamp
    """
    ps = _clamp_page_size(page_size, format == "csv")
    data = await query_agents_report(
        client    = request.app.state.store.new_client(),
        database  = request.app.state.store._database,
        tenant_id = tenant_id,
        from_dt   = from_dt,
        to_dt     = to_dt,
        agent_type_id    = agent_type_id,
        pool_id          = pool_id,
        event_type       = event_type,
        outcome          = outcome,
        accessible_pools = pool_principal.accessible_pools,
        page      = page,
        page_size = ps,
    )
    return _respond(data, format, f"agents_{_today_label()}.csv")


# ─── GET /reports/contact-insights ───────────────────────────────────────────

@router.get("/contact-insights")
async def report_contact_insights(
    request:      Request,
    tenant_id:    str           = Query(...,    description="Tenant identifier"),
    from_dt:      Optional[str] = Query(None,   description="ISO8601 start (default: 7d ago)"),
    to_dt:        Optional[str] = Query(None,   description="ISO8601 end (default: now)"),
    session_id:   Optional[str] = Query(None,   description="Filter by session_id"),
    category:     Optional[str] = Query(None,   description="Filter by insight category"),
    tags:         Optional[str] = Query(None,   description="Comma-separated tags (AND logic)"),
    insight_type: Optional[str] = Query(None,   description="Filter by full insight_type (e.g. insight.registered)"),
    page:         int           = Query(1,       ge=1),
    page_size:    int           = Query(100,     ge=1),
    format:       str           = Query("json",  pattern="^(json|csv)$"),
) -> Response:
    """
    Business events registered via insight_register MCP tool during agent flows.

    Examples: service executed (cancelamento, portabilidade), errors (erro_consulta_saldo).

    Filter by category + tags to find all contacts where a given business event occurred.

    Columns: insight_id, tenant_id, session_id, insight_type, category, value, tags, agent_id, timestamp
    """
    ps = _clamp_page_size(page_size, format == "csv")
    tags_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
    data = await query_contact_insights_report(
        client    = request.app.state.store.new_client(),
        database  = request.app.state.store._database,
        tenant_id = tenant_id,
        from_dt   = from_dt,
        to_dt     = to_dt,
        session_id   = session_id,
        category     = category,
        tags         = tags_list,
        insight_type = insight_type,
        page      = page,
        page_size = ps,
    )
    return _respond(data, format, f"contact_insights_{_today_label()}.csv")


# ─── GET /reports/quality ─────────────────────────────────────────────────────

@router.get("/quality")
async def report_quality(
    request:        Request,
    tenant_id:      str           = Query(...,   description="Tenant identifier"),
    from_dt:        Optional[str] = Query(None,  description="ISO8601 start (default: 7d ago)"),
    to_dt:          Optional[str] = Query(None,  description="ISO8601 end (default: now)"),
    pool_id:        Optional[str] = Query(None,  description="Filter by pool_id"),
    category:       Optional[str] = Query(None,  description="Filter by sentiment category"),
    page:           int           = Query(1,      ge=1),
    page_size:      int           = Query(100,    ge=1),
    format:         str           = Query("json", pattern="^(json|csv)$"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Per-turn sentiment event list. Useful for CSAT quality analysis.

    category filter: satisfied | neutral | frustrated | angry

    Columns: event_id, tenant_id, session_id, pool_id, score, category, timestamp
    """
    ps = _clamp_page_size(page_size, format == "csv")
    data = await query_quality_report(
        client    = request.app.state.store.new_client(),
        database  = request.app.state.store._database,
        tenant_id = tenant_id,
        from_dt   = from_dt,
        to_dt     = to_dt,
        pool_id          = pool_id,
        category         = category,
        accessible_pools = pool_principal.accessible_pools,
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
        client    = request.app.state.store.new_client(),
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


# ─── GET /reports/workflows ───────────────────────────────────────────────────

@router.get("/workflows")
async def report_workflows(
    request:     Request,
    tenant_id:   str           = Query(...,    description="Tenant identifier"),
    from_dt:     Optional[str] = Query(None,   description="ISO8601 start (default: 7d ago)"),
    to_dt:       Optional[str] = Query(None,   description="ISO8601 end (default: now)"),
    flow_id:     Optional[str] = Query(None,   description="Filter by flow_id"),
    status:      Optional[str] = Query(None,   description="Filter by workflow status"),
    campaign_id: Optional[str] = Query(None,   description="Filter by campaign_id"),
    page:        int           = Query(1,       ge=1),
    page_size:   int           = Query(100,     ge=1),
    format:      str           = Query("json",  pattern="^(json|csv)$"),
) -> Response:
    """
    Workflow lifecycle event list.

    status filter: active | suspended | completed | failed | timed_out | cancelled

    Columns: event_id, tenant_id, instance_id, flow_id, campaign_id,
             event_type, status, current_step, suspend_reason, decision,
             outcome, duration_ms, wait_duration_ms, error, timestamp
    """
    ps = _clamp_page_size(page_size, format == "csv")
    data = await query_workflows_report(
        client    = request.app.state.store.new_client(),
        database  = request.app.state.store._database,
        tenant_id = tenant_id,
        from_dt   = from_dt,
        to_dt     = to_dt,
        flow_id     = flow_id,
        status      = status,
        campaign_id = campaign_id,
        page      = page,
        page_size = ps,
    )
    return _respond(data, format, f"workflows_{_today_label()}.csv")


# ─── GET /reports/campaigns ───────────────────────────────────────────────────

@router.get("/campaigns")
async def report_campaigns(
    request:     Request,
    tenant_id:   str           = Query(...,    description="Tenant identifier"),
    from_dt:     Optional[str] = Query(None,   description="ISO8601 start (default: 7d ago)"),
    to_dt:       Optional[str] = Query(None,   description="ISO8601 end (default: now)"),
    campaign_id: Optional[str] = Query(None,   description="Filter by campaign_id"),
    channel:     Optional[str] = Query(None,   description="Filter by channel"),
    status:      Optional[str] = Query(None,   description="Filter by collect status"),
    page:        int           = Query(1,       ge=1),
    page_size:   int           = Query(100,     ge=1),
    format:      str           = Query("json",  pattern="^(json|csv)$"),
) -> Response:
    """
    Campaign collect event list + per-campaign aggregate summary.

    status filter: requested | sent | responded | timed_out

    Response includes:
      data    — individual collect_event rows
      summary — per-campaign aggregate (total, responded, timed_out, response_rate_pct, avg_elapsed_ms)
      meta    — page / total / date range

    Columns: collect_token, tenant_id, instance_id, flow_id, campaign_id,
             step_id, target_type, channel, interaction, status,
             send_at, responded_at, elapsed_ms, timestamp
    """
    ps = _clamp_page_size(page_size, format == "csv")
    data = await query_campaigns_report(
        client    = request.app.state.store.new_client(),
        database  = request.app.state.store._database,
        tenant_id = tenant_id,
        from_dt   = from_dt,
        to_dt     = to_dt,
        campaign_id = campaign_id,
        channel     = channel,
        status      = status,
        page      = page,
        page_size = ps,
    )
    # For CSV export, flatten summary into data
    if format == "csv":
        return _respond({"data": data.get("data", [])}, format, f"campaigns_{_today_label()}.csv")
    return _respond(data, format, f"campaigns_{_today_label()}.csv")


# ─── GET /reports/participation ───────────────────────────────────────────────

@router.get("/participation")
async def report_participation(
    request:        Request,
    tenant_id:      str           = Query(...,    description="Tenant identifier"),
    from_dt:        Optional[str] = Query(None,   description="ISO8601 start (default: 7d ago)"),
    to_dt:          Optional[str] = Query(None,   description="ISO8601 end (default: now)"),
    session_id:     Optional[str] = Query(None,   description="Filter by session_id"),
    pool_id:        Optional[str] = Query(None,   description="Filter by pool_id"),
    agent_type_id:  Optional[str] = Query(None,   description="Filter by agent_type_id"),
    role:           Optional[str] = Query(None,   description="Filter by participant role (primary|specialist|supervisor)"),
    page:           int           = Query(1,       ge=1),
    page_size:      int           = Query(100,     ge=1),
    format:         str           = Query("json",  pattern="^(json|csv)$"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Participant interval list — who joined which session, when, and for how long.

    Uses participation_intervals (ReplacingMergeTree) — deduplicated via FINAL at query time.
    A row without left_at means the participant is still active (or the left event
    hasn't been processed yet).

    role filter: primary | specialist | supervisor | evaluator | reviewer

    Columns: event_id, session_id, tenant_id, participant_id, pool_id,
             agent_type_id, role, agent_type, conference_id,
             joined_at, left_at, duration_ms, timestamp
    """
    ps = _clamp_page_size(page_size, format == "csv")
    data = await query_participation_report(
        client    = request.app.state.store.new_client(),
        database  = request.app.state.store._database,
        tenant_id = tenant_id,
        from_dt   = from_dt,
        to_dt     = to_dt,
        session_id       = session_id,
        pool_id          = pool_id,
        agent_type_id    = agent_type_id,
        role             = role,
        accessible_pools = pool_principal.accessible_pools,
        page      = page,
        page_size = ps,
    )
    return _respond(data, format, f"participation_{_today_label()}.csv")


# ─── /reports/segments (Arc 5 — ContactSegment) ─────────────────────────────

@router.get("/segments")
async def get_segments_report(
    request:        Request,
    tenant_id:      str,
    from_dt:        str | None    = None,
    to_dt:          str | None    = None,
    session_id:     str | None    = None,
    pool_id:        str | None    = None,
    agent_type_id:  str | None    = None,
    role:           str | None    = None,
    outcome:        str | None    = None,
    page:           int           = 1,
    page_size:      int           = 100,
    format:         str | None    = None,
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Returns ContactSegment rows — one per agent participation window in a session.

    Each row represents a single agent's contiguous presence in a session:
    - sequence_index  : order among primary (sequential) segments (0, 1, 2…)
    - parent_segment_id: non-null for conference/parallel specialist segments
    - ended_at        : null if the participant is still active
    - duration_ms     : populated on participant_left event

    Filters: session_id, pool_id, agent_type_id, role, outcome
    role:    primary | specialist | supervisor | evaluator | reviewer
    outcome: resolved | escalated | transferred | abandoned | timeout

    Columns: segment_id, session_id, tenant_id, participant_id, pool_id,
             agent_type_id, instance_id, role, agent_type,
             parent_segment_id, sequence_index,
             started_at, ended_at, duration_ms,
             outcome, close_reason, handoff_reason, issue_status, conference_id
    """
    ps = _clamp_page_size(page_size, format == "csv")
    data = await query_segments_report(
        client    = request.app.state.store.new_client(),
        database  = request.app.state.store._database,
        tenant_id = tenant_id,
        from_dt   = from_dt,
        to_dt     = to_dt,
        session_id       = session_id,
        pool_id          = pool_id,
        agent_type_id    = agent_type_id,
        role             = role,
        outcome          = outcome,
        accessible_pools = pool_principal.accessible_pools,
        page      = page,
        page_size = ps,
    )
    return _respond(data, format, f"segments_{_today_label()}.csv")


# ─── /reports/agents/performance (Arc 5 — aggregate per agent) ──────────────

@router.get("/agents/performance")
async def get_agent_performance_report(
    request:        Request,
    tenant_id:      str,
    from_dt:        str | None    = None,
    to_dt:          str | None    = None,
    pool_id:        str | None    = None,
    agent_type_id:  str | None    = None,
    role:           str | None    = None,
    format:         str | None    = None,
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Returns aggregate performance metrics per (agent_type_id, pool_id, role).

    One row per distinct agent × pool × role combination observed in the
    segments table (Arc 5 ContactSegment). No pagination — the cardinality
    is bounded by the number of registered agent types × pools.

    Metrics per group:
      - total_sessions     : number of participation windows
      - avg_duration_ms    : mean handle time (null if no completed segments)
      - escalation_rate    : fraction of sessions with outcome='escalated'
      - handoff_rate       : fraction of sessions with a non-null handoff_reason
      - resolved_count / escalated_count / transferred_count /
        abandoned_count / timeout_count / handoff_count : raw breakdowns

    Filters: pool_id, agent_type_id, role, from_dt, to_dt
    role:    primary | specialist | supervisor | evaluator | reviewer
    """
    data = await query_agent_performance_report(
        client           = request.app.state.store.new_client(),
        database         = request.app.state.store._database,
        tenant_id        = tenant_id,
        from_dt          = from_dt,
        to_dt            = to_dt,
        pool_id          = pool_id,
        agent_type_id    = agent_type_id,
        role             = role,
        accessible_pools = pool_principal.accessible_pools,
    )
    return _respond(data, format, f"agent_performance_{_today_label()}.csv")


# ─── /reports/evaluations ─────────────────────────────────────────────────────

@router.get("/evaluations")
async def get_evaluations_report(
    request:      Request,
    tenant_id:    str            = Query(...),
    from_dt:      Optional[str]  = Query(None),
    to_dt:        Optional[str]  = Query(None),
    campaign_id:  Optional[str]  = Query(None),
    form_id:      Optional[str]  = Query(None),
    evaluator_id: Optional[str]  = Query(None),
    eval_status:  Optional[str]  = Query(None),
    page:         int            = Query(1, ge=1),
    page_size:    int            = Query(100, ge=1),
    format:       str            = Query("json"),
) -> Response:
    """
    Individual evaluation results per session.

    Filters: campaign_id, form_id, evaluator_id, eval_status
    eval_status: submitted | approved | rejected | contested | locked
    format: json | csv
    """
    page_size = _clamp_page_size(page_size, format == "csv")
    data = await query_evaluations_report(
        client       = request.app.state.store.new_client(),
        database     = request.app.state.store._database,
        tenant_id    = tenant_id,
        from_dt      = from_dt,
        to_dt        = to_dt,
        campaign_id  = campaign_id,
        form_id      = form_id,
        evaluator_id = evaluator_id,
        eval_status  = eval_status,
        page         = page,
        page_size    = page_size,
    )
    return _respond(data, format, f"evaluations_{_today_label()}.csv")


@router.get("/evaluations/summary")
async def get_evaluations_summary(
    request:     Request,
    tenant_id:   str            = Query(...),
    from_dt:     Optional[str]  = Query(None),
    to_dt:       Optional[str]  = Query(None),
    campaign_id: Optional[str]  = Query(None),
    form_id:     Optional[str]  = Query(None),
    group_by:    str            = Query("campaign_id"),
    format:      str            = Query("json"),
) -> Response:
    """
    Aggregated evaluation summary: avg score, score distribution, status counts.

    group_by: campaign_id | evaluator_id | form_id | date
    Includes per-group breakdowns: score_excellent (≥0.9), score_good (0.7-0.9),
    score_fair (0.5-0.7), score_poor (<0.5), with_compliance_flags count.
    format: json | csv
    """
    data = await query_evaluations_summary(
        client     = request.app.state.store.new_client(),
        database   = request.app.state.store._database,
        tenant_id  = tenant_id,
        from_dt    = from_dt,
        to_dt      = to_dt,
        campaign_id = campaign_id,
        form_id    = form_id,
        group_by   = group_by,
    )
    return _respond(data, format, f"evaluations_summary_{_today_label()}.csv")


# ─── GET /reports/timeseries/volume ──────────────────────────────────────────

@router.get("/timeseries/volume")
async def report_timeseries_volume(
    request:       Request,
    tenant_id:     str           = Query(...,   description="Tenant identifier"),
    from_dt:       Optional[str] = Query(None,  description="ISO8601 start (default: 7d ago)"),
    to_dt:         Optional[str] = Query(None,  description="ISO8601 end (default: now)"),
    interval:      int           = Query(60,    ge=1, le=1440, description="Bucket size in minutes"),
    breakdown_by:  Optional[str] = Query(None,  description="pool_id | channel"),
    pool_id:       Optional[str] = Query(None,  description="Filter by pool_id"),
    format:        str           = Query("json", pattern="^(json|csv)$"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Session volume (count) bucketed by time interval.

    buckets[].value = number of sessions opened in the bucket window.
    breakdown_by=pool_id|channel splits each bucket by that dimension.
    meta.total = total sessions across all buckets.
    """
    data = await query_volume_timeseries(
        client     = request.app.state.store.new_client(),
        database   = request.app.state.store._database,
        tenant_id  = tenant_id,
        from_dt    = from_dt,
        to_dt      = to_dt,
        interval   = interval,
        breakdown_by = breakdown_by,
        pool_id    = pool_id,
        accessible_pools = pool_principal.accessible_pools,
    )
    if format == "csv":
        return Response(
            content=timeseries_to_csv(data.get("buckets", [])),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="volume_timeseries_{_today_label()}.csv"'},
        )
    return JSONResponse(content=data, status_code=503 if data.get("error") else 200)


# ─── GET /reports/timeseries/handle_time ─────────────────────────────────────

@router.get("/timeseries/handle_time")
async def report_timeseries_handle_time(
    request:       Request,
    tenant_id:     str           = Query(...,   description="Tenant identifier"),
    from_dt:       Optional[str] = Query(None,  description="ISO8601 start (default: 7d ago)"),
    to_dt:         Optional[str] = Query(None,  description="ISO8601 end (default: now)"),
    interval:      int           = Query(60,    ge=1, le=1440, description="Bucket size in minutes"),
    breakdown_by:  Optional[str] = Query(None,  description="pool_id | channel"),
    pool_id:       Optional[str] = Query(None,  description="Filter by pool_id"),
    format:        str           = Query("json", pattern="^(json|csv)$"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Average handle time (ms) bucketed by time interval.

    buckets[].value = avg duration_ms of sessions opened in the bucket window.
    meta.total = overall avg across all buckets.
    Tip: divide by 60000 in the UI to display minutes.
    """
    data = await query_handle_time_timeseries(
        client     = request.app.state.store.new_client(),
        database   = request.app.state.store._database,
        tenant_id  = tenant_id,
        from_dt    = from_dt,
        to_dt      = to_dt,
        interval   = interval,
        breakdown_by = breakdown_by,
        pool_id    = pool_id,
        accessible_pools = pool_principal.accessible_pools,
    )
    if format == "csv":
        return Response(
            content=timeseries_to_csv(data.get("buckets", [])),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="handle_time_timeseries_{_today_label()}.csv"'},
        )
    return JSONResponse(content=data, status_code=503 if data.get("error") else 200)


# ─── GET /reports/timeseries/score ───────────────────────────────────────────

@router.get("/timeseries/score")
async def report_timeseries_score(
    request:      Request,
    tenant_id:    str           = Query(...,   description="Tenant identifier"),
    from_dt:      Optional[str] = Query(None,  description="ISO8601 start (default: 7d ago)"),
    to_dt:        Optional[str] = Query(None,  description="ISO8601 end (default: now)"),
    interval:     int           = Query(60,    ge=1, le=1440, description="Bucket size in minutes"),
    breakdown_by: Optional[str] = Query(None,  description="campaign_id | form_id"),
    campaign_id:  Optional[str] = Query(None,  description="Filter by campaign_id"),
    format:       str           = Query("json", pattern="^(json|csv)$"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Average evaluation score (0–1) bucketed by time interval.

    buckets[].value = avg overall_score of evaluations submitted in the bucket.
    breakdown_by=campaign_id|form_id splits each bucket by that dimension.
    meta.total = overall avg across all buckets.
    """
    data = await query_score_timeseries(
        client     = request.app.state.store.new_client(),
        database   = request.app.state.store._database,
        tenant_id  = tenant_id,
        from_dt    = from_dt,
        to_dt      = to_dt,
        interval   = interval,
        breakdown_by = breakdown_by,
        campaign_id  = campaign_id,
        accessible_pools = pool_principal.accessible_pools,
    )
    if format == "csv":
        return Response(
            content=timeseries_to_csv(data.get("buckets", [])),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="score_timeseries_{_today_label()}.csv"'},
        )
    return JSONResponse(content=data, status_code=503 if data.get("error") else 200)


# ─── /reports/agent-performance/daily (Arc 5 MV — v_agent_performance) ──────

@router.get("/agent-performance/daily")
async def get_agent_performance_daily(
    request:        Request,
    tenant_id:      str           = Query(...,    description="Tenant identifier"),
    from_dt:        Optional[str] = Query(None,   description="Start date (YYYY-MM-DD or ISO8601); default: 7d ago"),
    to_dt:          Optional[str] = Query(None,   description="End date (YYYY-MM-DD or ISO8601); default: today"),
    pool_id:        Optional[str] = Query(None,   description="Filter by pool_id"),
    agent_type_id:  Optional[str] = Query(None,   description="Filter by agent_type_id"),
    format:         str           = Query("json",  pattern="^(json|csv)$"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Daily pre-aggregated performance metrics from the mv_agent_performance_daily
    materialized view (AggregatingMergeTree), read via the v_agent_performance
    readable SQL view.

    One row per (agent_type_id, pool_id, period_date). Suitable for trend charts
    and time-series dashboards — much faster than querying segments FINAL.

    Columns:
      agent_type_id, pool_id, period_date,
      total_sessions, avg_duration_ms,
      resolution_rate, escalation_rate, transfer_rate, human_rate

    Rates are in [0.0, 1.0]. total_sessions reflects sessions handled in that day
    for the given agent × pool combination (MIN_SESSIONS threshold NOT applied here —
    use the routing-engine's performance_job for throttle-safe scores).
    """
    data = await query_agent_performance_daily(
        client    = request.app.state.store.new_client(),
        database  = request.app.state.store._database,
        tenant_id = tenant_id,
        from_dt   = from_dt,
        to_dt     = to_dt,
        pool_id          = pool_id,
        agent_type_id    = agent_type_id,
        accessible_pools = pool_principal.accessible_pools,
    )
    return _respond(data, format, f"agent_performance_daily_{_today_label()}.csv")


# ─── /reports/sessions/complexity (Arc 5 MV — v_segment_summary) ─────────────

@router.get("/sessions/complexity")
async def get_session_complexity(
    request:        Request,
    tenant_id:      str           = Query(...,    description="Tenant identifier"),
    from_dt:        Optional[str] = Query(None,   description="ISO8601 start (default: 7d ago)"),
    to_dt:          Optional[str] = Query(None,   description="ISO8601 end (default: now)"),
    pool_id:        Optional[str] = Query(None,   description="Filter sessions by originating pool_id"),
    min_handoffs:   int           = Query(0,       ge=0, description="Minimum handoff_count to include"),
    page:           int           = Query(1,       ge=1),
    page_size:      int           = Query(100,     ge=1),
    format:         str           = Query("json",  pattern="^(json|csv)$"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Session complexity metrics from the mv_segment_summary materialized view
    (AggregatingMergeTree), read via the v_segment_summary readable SQL view.
    Joined with the sessions table for date-range and pool_id filtering.

    One row per session. Suitable for identifying complex interactions (high
    handoffs, multi-agent conferences) and escalation pattern analysis.

    Columns:
      session_id, pool_id,
      segment_count, primary_segments, specialist_segments, human_segments,
      total_duration_ms,
      handoff_count, escalation_count, resolved_count

    Use min_handoffs=1 to find sessions that were transferred at least once.
    Use min_handoffs=2 to find sessions with multiple escalation steps.
    """
    ps = _clamp_page_size(page_size, format == "csv")
    data = await query_session_complexity(
        client    = request.app.state.store.new_client(),
        database  = request.app.state.store._database,
        tenant_id = tenant_id,
        from_dt   = from_dt,
        to_dt     = to_dt,
        pool_id          = pool_id,
        min_handoffs     = min_handoffs,
        accessible_pools = pool_principal.accessible_pools,
        page      = page,
        page_size = ps,
    )
    return _respond(data, format, f"session_complexity_{_today_label()}.csv")


# ─── /reports/agent-availability (Arc 8 — pause intervals) ───────────────────

@router.get("/agent-availability")
async def get_agent_availability(
    request:        Request,
    tenant_id:      str           = Query(...,   description="Tenant identifier"),
    from_dt:        Optional[str] = Query(None,  description="ISO8601 start (default: 7d ago)"),
    to_dt:          Optional[str] = Query(None,  description="ISO8601 end (default: now)"),
    pool_id:        Optional[str] = Query(None,  description="Filter by pool_id"),
    agent_type_id:  Optional[str] = Query(None,  description="Filter by agent_type_id"),
    page:           int           = Query(1,      ge=1),
    page_size:      int           = Query(100,    ge=1),
    format:         str           = Query("json", pattern="^(json|csv)$"),
    pool_principal: PoolPrincipal = Depends(optional_pool_principal),
) -> Response:
    """
    Agent pause/availability report from the agent_pause_intervals table (Arc 8).

    Aggregates completed pause intervals (duration_ms IS NOT NULL) per
    (agent_type_id, pool_id, period_date).

    Each row includes:
      agent_type_id, pool_id, period_date,
      total_pauses      — number of completed pause intervals,
      total_pause_ms    — sum of all durations in milliseconds,
      reason_breakdown  — list of {reason_id, reason_label, count, total_ms}

    Pool scoping (Arc 7c): if the caller JWT carries accessible_pools the
    result is restricted to those pool_ids automatically.

    Use format=csv for bulk exports (flattens reason_breakdown as JSON string).
    """
    ps = _clamp_page_size(page_size, format == "csv")
    data = await query_agent_availability(
        store             = request.app.state.store,
        tenant_id         = tenant_id,
        from_dt           = from_dt,
        to_dt             = to_dt,
        pool_id           = pool_id,
        agent_type_id     = agent_type_id,
        accessible_pools  = pool_principal.accessible_pools,
        page              = page,
        page_size         = ps,
    )
    return _respond(data, format, f"agent_availability_{_today_label()}.csv")

