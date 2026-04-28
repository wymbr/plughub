"""
reports_query.py
ClickHouse query helpers for the /reports/* endpoints.

Four report helpers, all following the same pattern:
  - Accept: client, database, tenant_id, from_dt, to_dt, optional filters, page, page_size
  - Return: {"data": list[dict], "meta": {page, page_size, total, from_dt, to_dt}}

Datetime strings are formatted as 'YYYY-MM-DD HH:MM:SS' for ClickHouse comparisons.
Optional filters are injected as named ClickHouse parameters ({name:Type}) to avoid
SQL injection; only strings read from user input are parameterised.
"""
from __future__ import annotations

import asyncio
import csv
import io
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("plughub.analytics.reports")

# ─── defaults ─────────────────────────────────────────────────────────────────

_MAX_PAGE_SIZE_JSON = 1_000
_MAX_PAGE_SIZE_CSV  = 10_000


def _default_from() -> str:
    return (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")


def _default_to() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _ch_fmt(iso: str | None) -> str:
    """Converts an ISO8601 string to a ClickHouse-compatible datetime string (UTC, no tz)."""
    if not iso:
        return _default_to()
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return _default_to()


def _rows_to_dicts(result: Any) -> list[dict]:
    """Converts a clickhouse_connect query result to a list of dicts."""
    cols = result.column_names
    rows = []
    for row in result.result_rows:
        d = dict(zip(cols, row))
        # Convert datetime objects to ISO strings for JSON serialisability
        for k, v in d.items():
            if isinstance(v, datetime):
                d[k] = v.isoformat()
        rows.append(d)
    return rows


def _to_csv(data: list[dict]) -> str:
    """Converts a list of dicts to a CSV string."""
    if not data:
        return ""
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(data[0].keys()), lineterminator="\n")
    writer.writeheader()
    writer.writerows(data)
    return buf.getvalue()


def _clamp_page_size(page_size: int, is_csv: bool) -> int:
    limit = _MAX_PAGE_SIZE_CSV if is_csv else _MAX_PAGE_SIZE_JSON
    return max(1, min(page_size, limit))


# ─── shared count helper ──────────────────────────────────────────────────────

def _count(client: Any, sql_count: str, params: dict) -> int:
    result = client.query(sql_count, parameters=params)
    if result.result_rows:
        return int(result.result_rows[0][0])
    return 0


def _meta(page: int, page_size: int, total: int, from_dt: str, to_dt: str) -> dict:
    return {
        "page":      page,
        "page_size": page_size,
        "total":     total,
        "from_dt":   from_dt,
        "to_dt":     to_dt,
    }


# ─── /reports/sessions ────────────────────────────────────────────────────────

async def query_sessions_report(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    channel:      str | None = None,
    outcome:      str | None = None,
    close_reason: str | None = None,
    pool_id:      str | None = None,
    page:      int = 1,
    page_size: int = 100,
) -> dict:
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt)   if to_dt   else _default_to()
    try:
        return await asyncio.to_thread(
            _fetch_sessions, client, database, tenant_id, since, until,
            channel, outcome, close_reason, pool_id, page, page_size,
        )
    except Exception as exc:
        logger.warning("query_sessions_report failed tenant=%s: %s", tenant_id, exc)
        return {"data": [], "meta": _meta(page, page_size, 0, since, until), "error": "data_unavailable"}


def _fetch_sessions(
    client: Any, db: str, tenant_id: str,
    since: str, until: str,
    channel: str | None, outcome: str | None, close_reason: str | None, pool_id: str | None,
    page: int, page_size: int,
) -> dict:
    conditions = [
        "tenant_id = {tenant_id:String}",
        f"opened_at >= '{since}'",
        f"opened_at < '{until}'",
    ]
    params: dict = {"tenant_id": tenant_id}

    if channel:
        conditions.append("channel = {channel:String}")
        params["channel"] = channel
    if outcome:
        conditions.append("outcome = {outcome:String}")
        params["outcome"] = outcome
    if close_reason:
        conditions.append("close_reason = {close_reason:String}")
        params["close_reason"] = close_reason
    if pool_id:
        conditions.append("pool_id = {pool_id:String}")
        params["pool_id"] = pool_id

    where = " AND ".join(conditions)
    offset = (page - 1) * page_size

    total = _count(client, f"SELECT count() FROM {db}.sessions FINAL WHERE {where}", params)

    result = client.query(f"""
        SELECT
            session_id, tenant_id, channel, pool_id,
            opened_at, closed_at, close_reason, outcome,
            wait_time_ms, handle_time_ms
        FROM {db}.sessions FINAL
        WHERE {where}
        ORDER BY opened_at DESC
        LIMIT {page_size} OFFSET {offset}
    """, parameters=params)

    return {"data": _rows_to_dicts(result), "meta": _meta(page, page_size, total, since, until)}


# ─── /reports/agents ─────────────────────────────────────────────────────────

async def query_agents_report(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    agent_type_id: str | None = None,
    pool_id:       str | None = None,
    event_type:    str | None = None,
    outcome:       str | None = None,
    page:      int = 1,
    page_size: int = 100,
) -> dict:
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt)   if to_dt   else _default_to()
    try:
        return await asyncio.to_thread(
            _fetch_agents, client, database, tenant_id, since, until,
            agent_type_id, pool_id, event_type, outcome, page, page_size,
        )
    except Exception as exc:
        logger.warning("query_agents_report failed tenant=%s: %s", tenant_id, exc)
        return {"data": [], "meta": _meta(page, page_size, 0, since, until), "error": "data_unavailable"}


def _fetch_agents(
    client: Any, db: str, tenant_id: str,
    since: str, until: str,
    agent_type_id: str | None, pool_id: str | None,
    event_type: str | None, outcome: str | None,
    page: int, page_size: int,
) -> dict:
    conditions = [
        "tenant_id = {tenant_id:String}",
        f"timestamp >= '{since}'",
        f"timestamp < '{until}'",
    ]
    params: dict = {"tenant_id": tenant_id}

    if agent_type_id:
        conditions.append("agent_type_id = {agent_type_id:String}")
        params["agent_type_id"] = agent_type_id
    if pool_id:
        conditions.append("pool_id = {pool_id:String}")
        params["pool_id"] = pool_id
    if event_type:
        conditions.append("event_type = {event_type:String}")
        params["event_type"] = event_type
    if outcome:
        conditions.append("outcome = {outcome:String}")
        params["outcome"] = outcome

    where = " AND ".join(conditions)
    offset = (page - 1) * page_size

    total = _count(client, f"SELECT count() FROM {db}.agent_events FINAL WHERE {where}", params)

    result = client.query(f"""
        SELECT
            event_id, tenant_id, session_id, agent_type_id, pool_id,
            instance_id, event_type, outcome, handoff_reason,
            handle_time_ms, routing_mode, timestamp
        FROM {db}.agent_events FINAL
        WHERE {where}
        ORDER BY timestamp DESC
        LIMIT {page_size} OFFSET {offset}
    """, parameters=params)

    return {"data": _rows_to_dicts(result), "meta": _meta(page, page_size, total, since, until)}


# ─── /reports/quality ────────────────────────────────────────────────────────

async def query_quality_report(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    pool_id:  str | None = None,
    category: str | None = None,
    page:      int = 1,
    page_size: int = 100,
) -> dict:
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt)   if to_dt   else _default_to()
    try:
        return await asyncio.to_thread(
            _fetch_quality, client, database, tenant_id, since, until,
            pool_id, category, page, page_size,
        )
    except Exception as exc:
        logger.warning("query_quality_report failed tenant=%s: %s", tenant_id, exc)
        return {"data": [], "meta": _meta(page, page_size, 0, since, until), "error": "data_unavailable"}


def _fetch_quality(
    client: Any, db: str, tenant_id: str,
    since: str, until: str,
    pool_id: str | None, category: str | None,
    page: int, page_size: int,
) -> dict:
    conditions = [
        "tenant_id = {tenant_id:String}",
        f"timestamp >= '{since}'",
        f"timestamp < '{until}'",
    ]
    params: dict = {"tenant_id": tenant_id}

    if pool_id:
        conditions.append("pool_id = {pool_id:String}")
        params["pool_id"] = pool_id
    if category:
        conditions.append("category = {category:String}")
        params["category"] = category

    where = " AND ".join(conditions)
    offset = (page - 1) * page_size

    total = _count(client, f"SELECT count() FROM {db}.sentiment_events FINAL WHERE {where}", params)

    result = client.query(f"""
        SELECT
            event_id, tenant_id, session_id, pool_id,
            score, category, timestamp
        FROM {db}.sentiment_events FINAL
        WHERE {where}
        ORDER BY timestamp DESC
        LIMIT {page_size} OFFSET {offset}
    """, parameters=params)

    return {"data": _rows_to_dicts(result), "meta": _meta(page, page_size, total, since, until)}


# ─── /reports/usage ──────────────────────────────────────────────────────────

async def query_usage_report(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    dimension:        str | None = None,
    source_component: str | None = None,
    page:      int = 1,
    page_size: int = 100,
) -> dict:
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt)   if to_dt   else _default_to()
    try:
        return await asyncio.to_thread(
            _fetch_usage, client, database, tenant_id, since, until,
            dimension, source_component, page, page_size,
        )
    except Exception as exc:
        logger.warning("query_usage_report failed tenant=%s: %s", tenant_id, exc)
        return {"data": [], "meta": _meta(page, page_size, 0, since, until), "error": "data_unavailable"}


def _fetch_usage(
    client: Any, db: str, tenant_id: str,
    since: str, until: str,
    dimension: str | None, source_component: str | None,
    page: int, page_size: int,
) -> dict:
    conditions = [
        "tenant_id = {tenant_id:String}",
        f"timestamp >= '{since}'",
        f"timestamp < '{until}'",
    ]
    params: dict = {"tenant_id": tenant_id}

    if dimension:
        conditions.append("dimension = {dimension:String}")
        params["dimension"] = dimension
    if source_component:
        conditions.append("source_component = {source_component:String}")
        params["source_component"] = source_component

    where = " AND ".join(conditions)
    offset = (page - 1) * page_size

    total = _count(client, f"SELECT count() FROM {db}.usage_events FINAL WHERE {where}", params)

    result = client.query(f"""
        SELECT
            event_id, tenant_id, session_id,
            dimension, quantity, source_component, timestamp
        FROM {db}.usage_events FINAL
        WHERE {where}
        ORDER BY timestamp DESC
        LIMIT {page_size} OFFSET {offset}
    """, parameters=params)

    return {"data": _rows_to_dicts(result), "meta": _meta(page, page_size, total, since, until)}


# ─── /reports/workflows ──────────────────────────────────────────────────────

async def query_workflows_report(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    flow_id:     str | None = None,
    status:      str | None = None,
    campaign_id: str | None = None,
    page:      int = 1,
    page_size: int = 100,
) -> dict:
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt)   if to_dt   else _default_to()
    try:
        return await asyncio.to_thread(
            _fetch_workflows, client, database, tenant_id, since, until,
            flow_id, status, campaign_id, page, page_size,
        )
    except Exception as exc:
        logger.warning("query_workflows_report failed tenant=%s: %s", tenant_id, exc)
        return {"data": [], "meta": _meta(page, page_size, 0, since, until), "error": "data_unavailable"}


def _fetch_workflows(
    client: Any, db: str, tenant_id: str,
    since: str, until: str,
    flow_id: str | None, status: str | None, campaign_id: str | None,
    page: int, page_size: int,
) -> dict:
    conditions = [
        "tenant_id = {tenant_id:String}",
        f"timestamp >= '{since}'",
        f"timestamp < '{until}'",
    ]
    params: dict = {"tenant_id": tenant_id}

    if flow_id:
        conditions.append("flow_id = {flow_id:String}")
        params["flow_id"] = flow_id
    if status:
        conditions.append("status = {status:String}")
        params["status"] = status
    if campaign_id:
        conditions.append("campaign_id = {campaign_id:String}")
        params["campaign_id"] = campaign_id

    where = " AND ".join(conditions)
    offset = (page - 1) * page_size

    total = _count(client, f"SELECT count() FROM {db}.workflow_events FINAL WHERE {where}", params)

    result = client.query(f"""
        SELECT
            event_id, tenant_id, instance_id, flow_id, campaign_id,
            event_type, status, current_step, suspend_reason, decision,
            outcome, duration_ms, wait_duration_ms, error, timestamp
        FROM {db}.workflow_events FINAL
        WHERE {where}
        ORDER BY timestamp DESC
        LIMIT {page_size} OFFSET {offset}
    """, parameters=params)

    return {"data": _rows_to_dicts(result), "meta": _meta(page, page_size, total, since, until)}


# ─── /reports/campaigns ──────────────────────────────────────────────────────

async def query_campaigns_report(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    campaign_id: str | None = None,
    channel:     str | None = None,
    status:      str | None = None,
    page:      int = 1,
    page_size: int = 100,
) -> dict:
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt)   if to_dt   else _default_to()
    try:
        return await asyncio.to_thread(
            _fetch_campaigns, client, database, tenant_id, since, until,
            campaign_id, channel, status, page, page_size,
        )
    except Exception as exc:
        logger.warning("query_campaigns_report failed tenant=%s: %s", tenant_id, exc)
        return {"data": [], "summary": [], "meta": _meta(page, page_size, 0, since, until), "error": "data_unavailable"}


def _fetch_campaigns(
    client: Any, db: str, tenant_id: str,
    since: str, until: str,
    campaign_id: str | None, channel: str | None, status: str | None,
    page: int, page_size: int,
) -> dict:
    conditions = [
        "tenant_id = {tenant_id:String}",
        f"timestamp >= '{since}'",
        f"timestamp < '{until}'",
    ]
    params: dict = {"tenant_id": tenant_id}

    if campaign_id:
        conditions.append("campaign_id = {campaign_id:String}")
        params["campaign_id"] = campaign_id
    if channel:
        conditions.append("channel = {channel:String}")
        params["channel"] = channel
    if status:
        conditions.append("status = {status:String}")
        params["status"] = status

    where = " AND ".join(conditions)
    offset = (page - 1) * page_size

    total = _count(client, f"SELECT count() FROM {db}.collect_events FINAL WHERE {where}", params)

    result = client.query(f"""
        SELECT
            collect_token, tenant_id, instance_id, flow_id, campaign_id,
            step_id, target_type, channel, interaction, status,
            send_at, responded_at, elapsed_ms, timestamp
        FROM {db}.collect_events FINAL
        WHERE {where}
        ORDER BY timestamp DESC
        LIMIT {page_size} OFFSET {offset}
    """, parameters=params)

    # Aggregate summary: one row per campaign_id
    agg_result = client.query(f"""
        SELECT
            campaign_id,
            count()                                                    AS total,
            countIf(status = 'responded')                              AS responded,
            countIf(status = 'timed_out')                              AS timed_out,
            countIf(status = 'sent')                                   AS sent,
            countIf(status = 'requested')                              AS requested,
            round(countIf(status = 'responded') * 100.0 / count(), 1) AS response_rate_pct,
            avg(if(status = 'responded', elapsed_ms, NULL))            AS avg_elapsed_ms
        FROM {db}.collect_events FINAL
        WHERE {where} AND campaign_id IS NOT NULL
        GROUP BY campaign_id
        ORDER BY total DESC
        LIMIT 100
    """, parameters=params)

    return {
        "data":    _rows_to_dicts(result),
        "summary": _rows_to_dicts(agg_result),
        "meta":    _meta(page, page_size, total, since, until),
    }


# ─── /reports/participation ───────────────────────────────────────────────────

async def query_participation_report(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    session_id:    str | None = None,
    pool_id:       str | None = None,
    agent_type_id: str | None = None,
    role:          str | None = None,
    page:      int = 1,
    page_size: int = 100,
) -> dict:
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt)   if to_dt   else _default_to()
    try:
        return await asyncio.to_thread(
            _fetch_participation, client, database, tenant_id, since, until,
            session_id, pool_id, agent_type_id, role, page, page_size,
        )
    except Exception as exc:
        logger.warning("query_participation_report failed tenant=%s: %s", tenant_id, exc)
        return {"data": [], "meta": _meta(page, page_size, 0, since, until), "error": "data_unavailable"}


def _fetch_participation(
    client: Any, db: str, tenant_id: str,
    since: str, until: str,
    session_id: str | None, pool_id: str | None,
    agent_type_id: str | None, role: str | None,
    page: int, page_size: int,
) -> dict:
    conditions = [
        "tenant_id = {tenant_id:String}",
        f"timestamp >= '{since}'",
        f"timestamp < '{until}'",
    ]
    params: dict = {"tenant_id": tenant_id}

    if session_id:
        conditions.append("session_id = {session_id:String}")
        params["session_id"] = session_id
    if pool_id:
        conditions.append("pool_id = {pool_id:String}")
        params["pool_id"] = pool_id
    if agent_type_id:
        conditions.append("agent_type_id = {agent_type_id:String}")
        params["agent_type_id"] = agent_type_id
    if role:
        conditions.append("role = {role:String}")
        params["role"] = role

    where = " AND ".join(conditions)
    offset = (page - 1) * page_size

    # Use FINAL so ReplacingMergeTree deduplication is applied at query time
    total = _count(
        client,
        f"SELECT count() FROM {db}.participation_intervals FINAL WHERE {where}",
        params,
    )

    result = client.query(f"""
        SELECT
            event_id, session_id, tenant_id,
            participant_id, pool_id, agent_type_id,
            role, agent_type, conference_id,
            joined_at, left_at, duration_ms,
            timestamp
        FROM {db}.participation_intervals FINAL
        WHERE {where}
        ORDER BY timestamp DESC
        LIMIT {page_size} OFFSET {offset}
    """, parameters=params)

    return {"data": _rows_to_dicts(result), "meta": _meta(page, page_size, total, since, until)}


# ─── /reports/segments (Arc 5 — ContactSegment) ──────────────────────────────

async def query_segments_report(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    session_id:    str | None = None,
    pool_id:       str | None = None,
    agent_type_id: str | None = None,
    role:          str | None = None,
    outcome:       str | None = None,
    page:      int = 1,
    page_size: int = 100,
) -> dict:
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt)   if to_dt   else _default_to()
    try:
        return await asyncio.to_thread(
            _fetch_segments, client, database, tenant_id, since, until,
            session_id, pool_id, agent_type_id, role, outcome, page, page_size,
        )
    except Exception as exc:
        logger.warning("query_segments_report failed tenant=%s: %s", tenant_id, exc)
        return {"data": [], "meta": _meta(page, page_size, 0, since, until), "error": "data_unavailable"}


def _fetch_segments(
    client: Any, db: str, tenant_id: str,
    since: str, until: str,
    session_id: str | None, pool_id: str | None,
    agent_type_id: str | None, role: str | None,
    outcome: str | None,
    page: int, page_size: int,
) -> dict:
    conditions = [
        "tenant_id = {tenant_id:String}",
        f"started_at >= '{since}'",
        f"started_at < '{until}'",
    ]
    params: dict = {"tenant_id": tenant_id}

    if session_id:
        conditions.append("session_id = {session_id:String}")
        params["session_id"] = session_id
    if pool_id:
        conditions.append("pool_id = {pool_id:String}")
        params["pool_id"] = pool_id
    if agent_type_id:
        conditions.append("agent_type_id = {agent_type_id:String}")
        params["agent_type_id"] = agent_type_id
    if role:
        conditions.append("role = {role:String}")
        params["role"] = role
    if outcome:
        conditions.append("outcome = {outcome:String}")
        params["outcome"] = outcome

    where  = " AND ".join(conditions)
    offset = (page - 1) * page_size

    # FINAL applies ReplacingMergeTree dedup so ended rows shadow joined rows
    total = _count(
        client,
        f"SELECT count() FROM {db}.segments FINAL WHERE {where}",
        params,
    )

    result = client.query(f"""
        SELECT
            segment_id, session_id, tenant_id,
            participant_id, pool_id, agent_type_id,
            instance_id, role, agent_type,
            parent_segment_id, sequence_index,
            started_at, ended_at, duration_ms,
            outcome, close_reason, handoff_reason, issue_status,
            conference_id
        FROM {db}.segments FINAL
        WHERE {where}
        ORDER BY started_at DESC
        LIMIT {page_size} OFFSET {offset}
    """, parameters=params)

    return {"data": _rows_to_dicts(result), "meta": _meta(page, page_size, total, since, until)}


# ─── /reports/agents/performance (Arc 5 — aggregate per agent) ───────────────

async def query_agent_performance_report(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    pool_id:       str | None = None,
    agent_type_id: str | None = None,
    role:          str | None = None,
) -> dict:
    """
    Aggregate performance metrics per (agent_type_id, pool_id, role).

    Reads from analytics.segments FINAL (Arc 5 ReplacingMergeTree).
    Returns one row per distinct combination — no pagination needed since
    the cardinality is bounded by the number of registered agent types × pools.

    Metrics:
      total_sessions     — count of participation windows
      avg_duration_ms    — mean handle time (null when all duration_ms are null)
      escalation_rate    — fraction with outcome = 'escalated'
      handoff_rate       — fraction with a non-empty handoff_reason
      resolved_count / escalated_count / transferred_count /
        abandoned_count / timeout_count / handoff_count — raw breakdowns
    """
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt)   if to_dt   else _default_to()
    try:
        return await asyncio.to_thread(
            _fetch_agent_performance,
            client, database, tenant_id, since, until,
            pool_id, agent_type_id, role,
        )
    except Exception as exc:
        logger.warning(
            "query_agent_performance_report failed tenant=%s: %s", tenant_id, exc
        )
        return {"data": [], "error": "data_unavailable"}


def _fetch_agent_performance(
    client:        Any,
    db:            str,
    tenant_id:     str,
    since:         str,
    until:         str,
    pool_id:       str | None,
    agent_type_id: str | None,
    role:          str | None,
) -> dict:
    conditions = [
        "tenant_id = {tenant_id:String}",
        f"started_at >= '{since}'",
        f"started_at < '{until}'",
    ]
    params: dict = {"tenant_id": tenant_id}

    if pool_id:
        conditions.append("pool_id = {pool_id:String}")
        params["pool_id"] = pool_id
    if agent_type_id:
        conditions.append("agent_type_id = {agent_type_id:String}")
        params["agent_type_id"] = agent_type_id
    if role:
        conditions.append("role = {role:String}")
        params["role"] = role

    where = " AND ".join(conditions)

    result = client.query(f"""
        SELECT
            agent_type_id,
            pool_id,
            role,
            count()                                                       AS total_sessions,
            avgOrNull(duration_ms)                                        AS avg_duration_ms,
            countIf(outcome = 'resolved')                                 AS resolved_count,
            countIf(outcome = 'escalated')                                AS escalated_count,
            countIf(outcome = 'transferred')                              AS transferred_count,
            countIf(outcome = 'abandoned')                                AS abandoned_count,
            countIf(outcome = 'timeout')                                  AS timeout_count,
            countIf(handoff_reason IS NOT NULL AND handoff_reason != '')  AS handoff_count,
            if(count() > 0,
               countIf(outcome = 'escalated') / count(),
               0.0)                                                       AS escalation_rate,
            if(count() > 0,
               countIf(handoff_reason IS NOT NULL AND handoff_reason != '') / count(),
               0.0)                                                       AS handoff_rate
        FROM {db}.segments FINAL
        WHERE {where}
        GROUP BY agent_type_id, pool_id, role
        ORDER BY agent_type_id, pool_id, role
    """, parameters=params)

    rows = _rows_to_dicts(result)
    return {
        "data": rows,
        "meta": {
            "total":   len(rows),
            "from_dt": since,
            "to_dt":   until,
        },
    }
