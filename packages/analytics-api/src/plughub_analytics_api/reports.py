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
    query_agent_performance_report,
    query_agents_report,
    query_campaigns_report,
    query_participation_report,
    query_quality_report,
    query_segments_report,
    query_sessions_report,
    query_usage_report,
    query_workflows_report,
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
        client    = request.app.state.store._client,
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
        client    = request.app.state.store._client,
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
    request:       Request,
    tenant_id:     str           = Query(...,    description="Tenant identifier"),
    from_dt:       Optional[str] = Query(None,   description="ISO8601 start (default: 7d ago)"),
    to_dt:         Optional[str] = Query(None,   description="ISO8601 end (default: now)"),
    session_id:    Optional[str] = Query(None,   description="Filter by session_id"),
    pool_id:       Optional[str] = Query(None,   description="Filter by pool_id"),
    agent_type_id: Optional[str] = Query(None,   description="Filter by agent_type_id"),
    role:          Optional[str] = Query(None,   description="Filter by participant role (primary|specialist|supervisor)"),
    page:          int           = Query(1,       ge=1),
    page_size:     int           = Query(100,     ge=1),
    format:        str           = Query("json",  pattern="^(json|csv)$"),
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
        client    = request.app.state.store._client,
        database  = request.app.state.store._database,
        tenant_id = tenant_id,
        from_dt   = from_dt,
        to_dt     = to_dt,
        session_id    = session_id,
        pool_id       = pool_id,
        agent_type_id = agent_type_id,
        role          = role,
        page      = page,
        page_size = ps,
    )
    return _respond(data, format, f"participation_{_today_label()}.csv")


# ─── /reports/segments (Arc 5 — ContactSegment) ─────────────────────────────

@router.get("/segments")
async def get_segments_report(
    request:       Request,
    tenant_id:     str,
    from_dt:       str | None = None,
    to_dt:         str | None = None,
    session_id:    str | None = None,
    pool_id:       str | None = None,
    agent_type_id: str | None = None,
    role:          str | None = None,
    outcome:       str | None = None,
    page:          int = 1,
    page_size:     int = 100,
    format:        str | None = None,
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
        client    = request.app.state.store._client,
        database  = request.app.state.store._database,
        tenant_id = tenant_id,
        from_dt   = from_dt,
        to_dt     = to_dt,
        session_id    = session_id,
        pool_id       = pool_id,
        agent_type_id = agent_type_id,
        role          = role,
        outcome       = outcome,
        page      = page,
        page_size = ps,
    )
    return _respond(data, format, f"segments_{_today_label()}.csv")


# ─── /reports/agents/performance (Arc 5 — aggregate per agent) ──────────────

@router.get("/agents/performance")
async def get_agent_performance_report(
    request:       Request,
    tenant_id:     str,
    from_dt:       str | None = None,
    to_dt:         str | None = None,
    pool_id:       str | None = None,
    agent_type_id: str | None = None,
    role:          str | None = None,
    format:        str | None = None,
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
        client        = request.app.state.store._client,
        database      = request.app.state.store._database,
        tenant_id     = tenant_id,
        from_dt       = from_dt,
        to_dt         = to_dt,
        pool_id       = pool_id,
        agent_type_id = agent_type_id,
        role          = role,
    )
    return _respond(data, format, f"agent_performance_{_today_label()}.csv")
