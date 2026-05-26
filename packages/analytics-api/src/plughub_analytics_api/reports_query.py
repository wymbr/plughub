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
    """Converts an ISO8601 string (or relative '-Nd' offset) to ClickHouse UTC datetime."""
    if not iso:
        return _default_to()
    stripped = iso.strip()
    # Relative offset: -Nd (e.g. '-7d' = 7 days ago)
    if stripped.startswith("-") and stripped.endswith("d"):
        try:
            days = int(stripped[1:-1])
            return (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return _default_from()
    try:
        dt = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return _default_to()


def _rows_to_dicts(result: Any) -> list[dict]:
    """Converts a clickhouse_connect query result to a list of dicts."""
    cols = result.column_names
    rows = []
    for row in result.result_rows:
        d = dict(zip(cols, row))
        # Convert datetime objects to ISO strings for JSON serialisability.
        # ClickHouse returns naive datetimes (no tzinfo) but stores them as UTC.
        # We must append timezone info so JavaScript interprets them correctly
        # (without it, JS treats "2026-05-02T11:48:45" as local time, not UTC).
        for k, v in d.items():
            if isinstance(v, datetime):
                if v.tzinfo is None:
                    v = v.replace(tzinfo=timezone.utc)
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


def _apply_agent_scope(
    conditions: list[str],
    supervised_agent_types: "list[str] | None",
) -> bool:
    """
    Arc 9 — Mutates *conditions* in-place to add an agent_type_id IN (...) filter.

    supervised_agent_types=None  → no-op (all agent types visible)
    supervised_agent_types=[…]   → append AND agent_type_id IN ('a','b',…)
    supervised_agent_types=[]    → caller has no agent type access → caller must return empty
    """
    if supervised_agent_types is None:
        return True
    if not supervised_agent_types:
        return False
    type_list = ", ".join(f"'{t}'" for t in supervised_agent_types)
    conditions.append(f"agent_type_id IN ({type_list})")
    return True


def _agent_scope_session_join(
    db: str,
    tenant_id: str,
    supervised_agent_types: "list[str] | None",
) -> tuple[str, str]:
    """
    Arc 9 — Returns (join_sql, extra_where) for sessions queries.

    Sessions don't have agent_type_id directly — scope is applied via a
    LEFT JOIN on segments FINAL to find sessions that had at least one
    segment from a supervised agent type.

    Returns ("", "") when no filter is needed.
    Returns (join_sql, "AND _scope.session_id IS NOT NULL") when filtering.
    Returns ("", "AND 1=0") when supervised_agent_types=[] (no access).
    """
    if supervised_agent_types is None:
        return "", ""
    if not supervised_agent_types:
        return "", "AND 1=0"
    type_list = ", ".join(f"'{t}'" for t in supervised_agent_types)
    join_sql = f"""
        LEFT JOIN (
            SELECT DISTINCT session_id
            FROM {db}.segments FINAL
            WHERE tenant_id = {{tenant_id:String}}
              AND agent_type_id IN ({type_list})
        ) AS _scope ON _scope.session_id = s.session_id"""
    return join_sql, "AND _scope.session_id IS NOT NULL"


def _apply_pool_scope(
    conditions: list[str],
    accessible_pools: "list[str] | None",
) -> bool:
    """
    Mutates *conditions* in-place to add a pool_id IN (...) filter when needed.

    Returns False if the caller has NO access to any pool (empty whitelist),
    which means the caller should short-circuit and return an empty result
    without hitting ClickHouse.

    accessible_pools=None  → no-op (all pools visible, typical for open-access)
    accessible_pools=[…]   → append AND pool_id IN ('a','b',…)
    accessible_pools=[]    → caller has no pool access → caller must return empty
    """
    if accessible_pools is None:
        return True   # unrestricted
    if not accessible_pools:
        return False  # no pools allowed
    # pool_ids come from a verified JWT — safe to inline as string literals
    pool_list = ", ".join(f"'{p}'" for p in accessible_pools)
    conditions.append(f"pool_id IN ({pool_list})")
    return True


# ─── /reports/sessions ────────────────────────────────────────────────────────

async def query_sessions_report(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    channel:                str | None       = None,
    outcome:                str | None       = None,
    close_reason:           str | None       = None,
    pool_id:                str | None       = None,
    session_id:             str | None       = None,
    agent_id:               str | None       = None,
    insight_category:       str | None       = None,
    insight_tags:           list[str] | None = None,
    accessible_pools:       list[str] | None = None,
    supervised_agent_types: list[str] | None = None,
    ani:                    str | None       = None,
    dnis:                   str | None       = None,
    page:      int = 1,
    page_size: int = 100,
) -> dict:
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt)   if to_dt   else _default_to()
    if accessible_pools is not None and not accessible_pools:
        return {"data": [], "meta": _meta(page, page_size, 0, since, until)}
    if supervised_agent_types is not None and not supervised_agent_types:
        return {"data": [], "meta": _meta(page, page_size, 0, since, until)}
    try:
        return await asyncio.to_thread(
            _fetch_sessions, client, database, tenant_id, since, until,
            channel, outcome, close_reason, pool_id, session_id,
            agent_id, insight_category, insight_tags, accessible_pools,
            supervised_agent_types, page, page_size,
            ani, dnis,
        )
    except Exception as exc:
        logger.warning("query_sessions_report failed tenant=%s: %s", tenant_id, exc)
        return {"data": [], "meta": _meta(page, page_size, 0, since, until), "error": "data_unavailable"}


def _fetch_sessions(
    client: Any, db: str, tenant_id: str,
    since: str, until: str,
    channel: str | None, outcome: str | None, close_reason: str | None, pool_id: str | None,
    session_id: str | None, agent_id: str | None,
    insight_category: str | None, insight_tags: list[str] | None,
    accessible_pools: list[str] | None,
    supervised_agent_types: list[str] | None,
    page: int, page_size: int,
    ani: str | None = None, dnis: str | None = None,
) -> dict:
    conditions = [
        "s.tenant_id = {tenant_id:String}",
        f"s.opened_at >= '{since}'",
        f"s.opened_at < '{until}'",
        # Exclude completed hook-agent sessions (wrapup_ia, nps_ia, etc.) that have no
        # physical channel — these are synthetic conferences created by orchestrator-bridge.
        # We CANNOT use `s.channel != ''` alone because parse_routed writes channel=''
        # which overwrites the parse_inbound row in ReplacingMergeTree, causing active
        # sessions to vanish right after routing. Instead:
        #   - Active sessions (closed_at IS NULL) are always shown — routing may have
        #     zeroed the channel column but the session is genuinely in progress.
        #   - Closed sessions must have channel != '' — hook sessions close with channel=''
        #     (their synthetic inbound event carries no channel) so they are excluded here.
        "(s.channel != '' OR s.closed_at IS NULL)",
    ]
    params: dict = {"tenant_id": tenant_id}

    if session_id:
        conditions.append("s.session_id = {session_id:String}")
        params["session_id"] = session_id
    if channel:
        # For active sessions parse_routed may have set channel='' — include them by also
        # checking non-FINAL rows. For closed sessions s.channel is authoritative.
        conditions.append(
            f"(s.channel = {{channel:String}} OR"
            f" (s.closed_at IS NULL AND EXISTS ("
            f"  SELECT 1 FROM {db}.sessions"
            f"  WHERE tenant_id = s.tenant_id AND session_id = s.session_id"
            f"  AND channel = {{channel:String}}"
            f" )))"
        )
        params["channel"] = channel
    if outcome:
        conditions.append("s.outcome = {outcome:String}")
        params["outcome"] = outcome
    if close_reason:
        conditions.append("s.close_reason = {close_reason:String}")
        params["close_reason"] = close_reason
    if pool_id:
        # pool_id changes per segment (routing + specialists + conference).
        # Query via segments to find any session where ANY segment belonged to this pool.
        conditions.append(
            f"s.session_id IN (SELECT session_id FROM {db}.segments FINAL"
            " WHERE tenant_id = {tenant_id:String} AND pool_id = {pool_id:String})"
        )
        params["pool_id"] = pool_id

    # Pool-scope access filter (Arc 7c) — inline pool_id list (safe, values from JWT).
    # Active sessions that have not yet been routed carry pool_id='' (parse_inbound sets it
    # empty; parse_routed fills it in later). We must include pool_id='' so supervisors
    # see contacts from the moment they arrive, before routing assigns a pool.
    if accessible_pools:
        pool_list = ", ".join(f"'{p}'" for p in accessible_pools)
        conditions.append(f"(s.pool_id IN ({pool_list}) OR s.pool_id = '')")
    # accessible_pools=None → no restriction; accessible_pools=[] → short-circuit in async wrapper

    # agent_id filter — requires subquery against segments table
    if agent_id:
        conditions.append(
            f"s.session_id IN (SELECT session_id FROM {db}.segments FINAL"
            " WHERE tenant_id = {{tenant_id:String}} AND participant_id = {{agent_id:String}})"
        )
        params["agent_id"] = agent_id

    # insight_category filter — requires subquery against contact_insights table
    if insight_category:
        conditions.append(
            f"s.session_id IN (SELECT session_id FROM {db}.contact_insights FINAL"
            " WHERE tenant_id = {{tenant_id:String}} AND category = {{insight_category:String}})"
        )
        params["insight_category"] = insight_category

    # insight_tags filter — each tag must be present (AND semantics)
    if insight_tags:
        for i, tag in enumerate(insight_tags):
            tag_key = f"insight_tag_{i}"
            conditions.append(
                f"s.session_id IN (SELECT session_id FROM {db}.contact_insights FINAL"
                f" WHERE tenant_id = {{tenant_id:String}} AND has(tags, {{{tag_key}:String}}))"
            )
            params[tag_key] = tag

    # ANI/DNIS filters — partial match (LIKE) for usability
    if ani:
        conditions.append("s.ani LIKE {ani_like:String}")
        params["ani_like"] = f"%{ani}%"
    if dnis:
        conditions.append("s.dnis LIKE {dnis_like:String}")
        params["dnis_like"] = f"%{dnis}%"

    where = " AND ".join(conditions)

    # Arc 9 — agent scope: sessions that had at least one segment from a supervised agent type
    _agent_join, _agent_where = _agent_scope_session_join(db, tenant_id, supervised_agent_types)
    if _agent_where:
        where = f"{where} {_agent_where}"

    offset = (page - 1) * page_size

    total = _count(
        client,
        f"SELECT count() FROM {db}.sessions AS s FINAL {_agent_join} WHERE {where}",
        params,
    )

    # ClickHouse 23.8 does NOT support correlated subqueries with outer-query aliases
    # (e.g. "WHERE tenant_id = s.tenant_id") in the SELECT clause — it raises:
    #   Code 47: Missing columns 's.session_id' 's.tenant_id'
    #
    # Fix: use pre-aggregated LEFT JOINs instead of correlated subqueries.
    # Each JOIN is scoped to {tenant_id:String} so it remains efficient.
    # handle_time_ms is a plain COALESCE over current-row columns — no subquery needed.
    #
    # Fallback strategy:
    #   Tier 1: full JOINs + s.ani / s.dnis
    #   Tier 2: full JOINs + NULL ani/dnis  (ANI/DNIS columns not yet migrated)
    #   Tier 3: bare sessions query          (segments/agent_events tables absent)

    # Arc 9: prepend agent scope JOIN (empty string when no restriction)
    _joins = f"""{_agent_join}
        -- channel recovery: any non-empty channel row for this session
        LEFT JOIN (
            SELECT session_id, anyIf(channel, channel != '') AS channel
            FROM {db}.sessions
            WHERE tenant_id = {{tenant_id:String}} AND channel != ''
            GROUP BY session_id
        ) AS _ch ON _ch.session_id = s.session_id
        -- pool_id recovery: earliest primary-segment pool for this session
        LEFT JOIN (
            SELECT session_id, argMin(pool_id, started_at) AS pool_id
            FROM {db}.segments FINAL
            WHERE tenant_id = {{tenant_id:String}}
              AND (parent_segment_id IS NULL OR parent_segment_id = '')
              AND pool_id != ''
            GROUP BY session_id
        ) AS _pool ON _pool.session_id = s.session_id
        -- outcome recovery: most-recent closed segment outcome
        LEFT JOIN (
            SELECT session_id, argMax(outcome, ended_at) AS outcome
            FROM {db}.segments FINAL
            WHERE tenant_id = {{tenant_id:String}}
              AND outcome IS NOT NULL AND outcome != ''
            GROUP BY session_id
        ) AS _seg_out ON _seg_out.session_id = s.session_id
        -- outcome fallback: most-recent agent_done outcome
        LEFT JOIN (
            SELECT session_id, argMax(outcome, timestamp) AS outcome
            FROM {db}.agent_events FINAL
            WHERE tenant_id = {{tenant_id:String}}
              AND event_type = 'agent_done'
              AND outcome IS NOT NULL AND outcome != ''
            GROUP BY session_id
        ) AS _ae_out ON _ae_out.session_id = s.session_id
        -- segment count per session
        LEFT JOIN (
            SELECT session_id, count() AS cnt
            FROM {db}.segments FINAL
            WHERE tenant_id = {{tenant_id:String}}
            GROUP BY session_id
        ) AS _sc ON _sc.session_id = s.session_id"""

    # Use __ANI_DNIS__ placeholder instead of str.format() to avoid conflicts
    # with ClickHouse's own {param:Type} syntax inside _joins.
    _rich_sql = f"""
        SELECT
            s.session_id,
            s.tenant_id,
            COALESCE(NULLIF(s.channel,  ''), _ch.channel)     AS channel,
            COALESCE(NULLIF(s.pool_id,  ''), _pool.pool_id)   AS pool_id,
            s.customer_id,
            s.opened_at,
            s.closed_at,
            s.close_reason,
            COALESCE(NULLIF(s.outcome, ''), _seg_out.outcome, _ae_out.outcome) AS outcome,
            s.wait_time_ms,
            COALESCE(
                s.handle_time_ms,
                if(s.closed_at IS NOT NULL AND s.opened_at IS NOT NULL,
                   toInt64(dateDiff('millisecond', s.opened_at, s.closed_at)), NULL)
            ) AS handle_time_ms,
            __ANI_DNIS__,
            COALESCE(_sc.cnt, 0) AS segment_count
        FROM {db}.sessions AS s FINAL
        {_joins}
        WHERE {where}
        ORDER BY s.opened_at DESC
        LIMIT {page_size} OFFSET {offset}"""

    try:
        # Tier 1: ANI/DNIS columns present
        result = client.query(
            _rich_sql.replace("__ANI_DNIS__", "s.ani, s.dnis"),
            parameters=params,
        )
    except Exception:
        try:
            # Tier 2: ANI/DNIS columns not yet migrated
            result = client.query(
                _rich_sql.replace("__ANI_DNIS__", "NULL AS ani, NULL AS dnis"),
                parameters=params,
            )
        except Exception:
            # Tier 3: segments / agent_events tables absent — bare minimum
            result = client.query(f"""
                SELECT
                    s.session_id, s.tenant_id, s.channel, s.pool_id, s.customer_id,
                    s.opened_at, s.closed_at, s.close_reason, s.outcome,
                    s.wait_time_ms, s.handle_time_ms,
                    NULL AS ani, NULL AS dnis, 0 AS segment_count
                FROM {db}.sessions AS s FINAL
                {_agent_join}
                WHERE {where}
                ORDER BY s.opened_at DESC
                LIMIT {page_size} OFFSET {offset}
            """, parameters=params)

    return {"data": _rows_to_dicts(result), "meta": _meta(page, page_size, total, since, until)}


# ─── /reports/contact-insights ────────────────────────────────────────────────

async def query_contact_insights_report(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    session_id:  str | None       = None,
    category:    str | None       = None,
    tags:        list[str] | None = None,
    insight_type: str | None      = None,
    page:      int = 1,
    page_size: int = 100,
) -> dict:
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt)   if to_dt   else _default_to()
    try:
        return await asyncio.to_thread(
            _fetch_contact_insights, client, database, tenant_id, since, until,
            session_id, category, tags, insight_type, page, page_size,
        )
    except Exception as exc:
        logger.warning("query_contact_insights_report failed tenant=%s: %s", tenant_id, exc)
        return {"data": [], "meta": _meta(page, page_size, 0, since, until), "error": "data_unavailable"}


def _fetch_contact_insights(
    client: Any, db: str, tenant_id: str,
    since: str, until: str,
    session_id: str | None, category: str | None,
    tags: list[str] | None, insight_type: str | None,
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
    if category:
        conditions.append("category = {category:String}")
        params["category"] = category
    if insight_type:
        conditions.append("insight_type = {insight_type:String}")
        params["insight_type"] = insight_type
    if tags:
        for i, tag in enumerate(tags):
            tag_key = f"tag_{i}"
            conditions.append(f"has(tags, {{{tag_key}:String}})")
            params[tag_key] = tag

    where = " AND ".join(conditions)
    offset = (page - 1) * page_size

    total = _count(client, f"SELECT count() FROM {db}.contact_insights FINAL WHERE {where}", params)

    result = client.query(f"""
        SELECT
            insight_id, tenant_id, session_id,
            insight_type, category, value, tags,
            agent_id, timestamp
        FROM {db}.contact_insights FINAL
        WHERE {where}
        ORDER BY timestamp DESC
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
    agent_type_id:    str | None       = None,
    pool_id:          str | None       = None,
    event_type:       str | None       = None,
    outcome:          str | None       = None,
    accessible_pools: list[str] | None = None,
    page:      int = 1,
    page_size: int = 100,
) -> dict:
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt)   if to_dt   else _default_to()
    if accessible_pools is not None and not accessible_pools:
        return {"data": [], "meta": _meta(page, page_size, 0, since, until)}
    try:
        return await asyncio.to_thread(
            _fetch_agents, client, database, tenant_id, since, until,
            agent_type_id, pool_id, event_type, outcome, accessible_pools, page, page_size,
        )
    except Exception as exc:
        logger.warning("query_agents_report failed tenant=%s: %s", tenant_id, exc)
        return {"data": [], "meta": _meta(page, page_size, 0, since, until), "error": "data_unavailable"}


def _fetch_agents(
    client: Any, db: str, tenant_id: str,
    since: str, until: str,
    agent_type_id: str | None, pool_id: str | None,
    event_type: str | None, outcome: str | None,
    accessible_pools: list[str] | None,
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
    _apply_pool_scope(conditions, accessible_pools)

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
    pool_id:          str | None       = None,
    category:         str | None       = None,
    accessible_pools: list[str] | None = None,
    page:      int = 1,
    page_size: int = 100,
) -> dict:
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt)   if to_dt   else _default_to()
    if accessible_pools is not None and not accessible_pools:
        return {"data": [], "meta": _meta(page, page_size, 0, since, until)}
    try:
        return await asyncio.to_thread(
            _fetch_quality, client, database, tenant_id, since, until,
            pool_id, category, accessible_pools, page, page_size,
        )
    except Exception as exc:
        logger.warning("query_quality_report failed tenant=%s: %s", tenant_id, exc)
        return {"data": [], "meta": _meta(page, page_size, 0, since, until), "error": "data_unavailable"}


def _fetch_quality(
    client: Any, db: str, tenant_id: str,
    since: str, until: str,
    pool_id: str | None, category: str | None,
    accessible_pools: list[str] | None,
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
    _apply_pool_scope(conditions, accessible_pools)

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


# ─── /reports/workflow-summary — aggregated workflow analytics ────────────────
#
# Aggregates workflow_events per flow_id or campaign_id.
# Uses countDistinctIf to count unique instances per lifecycle stage.
# duration_ms is only populated on completed/failed events (workflow-api sets it).

async def query_workflow_summary(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    group_by:    str = "flow_id",       # "flow_id" | "campaign_id"
    flow_id:     str | None = None,
    campaign_id: str | None = None,
) -> dict:
    """
    Summarised workflow metrics grouped by flow_id or campaign_id.

    Returns one row per group with:
      group_key, total_triggered, total_completed, total_failed,
      total_timeout, total_cancelled, total_suspended,
      completion_rate, failure_rate, avg_duration_ms
    """
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt)   if to_dt   else _default_to()
    try:
        return await asyncio.to_thread(
            _fetch_workflow_summary, client, database, tenant_id, since, until,
            group_by, flow_id, campaign_id,
        )
    except Exception as exc:
        logger.warning("query_workflow_summary failed tenant=%s: %s", tenant_id, exc)
        return {"data": [], "meta": {"from_dt": since, "to_dt": until}, "error": "data_unavailable"}


def _fetch_workflow_summary(
    client: Any, db: str, tenant_id: str,
    since: str, until: str,
    group_by: str, flow_id: str | None, campaign_id: str | None,
) -> dict:
    # Validate group_by — only allow known values
    if group_by not in ("flow_id", "campaign_id"):
        group_by = "flow_id"

    conditions = [
        "tenant_id = {tenant_id:String}",
        "timestamp >= {since:String}",
        "timestamp <= {until:String}",
    ]
    params: dict = {"tenant_id": tenant_id, "since": since, "until": until}
    if flow_id:
        conditions.append("flow_id = {flow_id:String}")
        params["flow_id"] = flow_id
    if campaign_id:
        conditions.append("campaign_id = {campaign_id:String}")
        params["campaign_id"] = campaign_id

    where = " AND ".join(conditions)

    # group_key: for campaign_id, NULL/empty maps to '(sem campanha)'
    if group_by == "campaign_id":
        group_expr = "if(campaign_id IS NOT NULL AND campaign_id != '', campaign_id, '(sem campanha)')"
    else:
        group_expr = "flow_id"

    result = client.query(f"""
        SELECT
            {group_expr}                                                        AS group_key,
            countDistinctIf(instance_id, event_type = 'triggered')             AS total_triggered,
            countDistinctIf(instance_id, event_type = 'completed')             AS total_completed,
            countDistinctIf(instance_id, event_type = 'failed')                AS total_failed,
            countDistinctIf(instance_id, event_type = 'timeout')               AS total_timeout,
            countDistinctIf(instance_id, event_type = 'cancelled')             AS total_cancelled,
            countDistinctIf(instance_id, event_type = 'suspended')             AS total_suspended,
            avgIf(duration_ms, event_type IN ('completed','failed')
                  AND duration_ms IS NOT NULL AND duration_ms > 0)             AS avg_duration_ms
        FROM {db}.workflow_events FINAL
        WHERE {where}
        GROUP BY group_key
        ORDER BY total_triggered DESC
        LIMIT 500
    """, parameters=params)

    rows = _rows_to_dicts(result)

    # Compute derived rates client-side (avoid division-by-zero in SQL)
    for row in rows:
        triggered = row.get("total_triggered") or 0
        completed = row.get("total_completed") or 0
        failed    = row.get("total_failed")    or 0
        row["completion_rate"] = round(completed / triggered, 4) if triggered else 0.0
        row["failure_rate"]    = round(failed    / triggered, 4) if triggered else 0.0
        # avg_duration_ms may be None if no completed/failed events in range
        if row.get("avg_duration_ms") is None:
            row["avg_duration_ms"] = None

    return {
        "data":     rows,
        "group_by": group_by,
        "meta":     {"total": len(rows), "from_dt": since, "to_dt": until},
    }


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
    session_id:       str | None       = None,
    pool_id:          str | None       = None,
    agent_type_id:    str | None       = None,
    role:             str | None       = None,
    accessible_pools: list[str] | None = None,
    page:      int = 1,
    page_size: int = 100,
) -> dict:
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt)   if to_dt   else _default_to()
    if accessible_pools is not None and not accessible_pools:
        return {"data": [], "meta": _meta(page, page_size, 0, since, until)}
    try:
        return await asyncio.to_thread(
            _fetch_participation, client, database, tenant_id, since, until,
            session_id, pool_id, agent_type_id, role, accessible_pools, page, page_size,
        )
    except Exception as exc:
        logger.warning("query_participation_report failed tenant=%s: %s", tenant_id, exc)
        return {"data": [], "meta": _meta(page, page_size, 0, since, until), "error": "data_unavailable"}


def _fetch_participation(
    client: Any, db: str, tenant_id: str,
    since: str, until: str,
    session_id: str | None, pool_id: str | None,
    agent_type_id: str | None, role: str | None,
    accessible_pools: list[str] | None,
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
    _apply_pool_scope(conditions, accessible_pools)

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
    session_id:             str | None       = None,
    pool_id:                str | None       = None,
    agent_type_id:          str | None       = None,
    role:                   str | None       = None,
    outcome:                str | None       = None,
    accessible_pools:       list[str] | None = None,
    supervised_agent_types: list[str] | None = None,
    page:      int = 1,
    page_size: int = 100,
) -> dict:
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt)   if to_dt   else _default_to()
    if accessible_pools is not None and not accessible_pools:
        return {"data": [], "meta": _meta(page, page_size, 0, since, until)}
    if supervised_agent_types is not None and not supervised_agent_types:
        return {"data": [], "meta": _meta(page, page_size, 0, since, until)}
    try:
        return await asyncio.to_thread(
            _fetch_segments, client, database, tenant_id, since, until,
            session_id, pool_id, agent_type_id, role, outcome,
            accessible_pools, supervised_agent_types, page, page_size,
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
    accessible_pools: list[str] | None,
    supervised_agent_types: list[str] | None,
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
    _apply_pool_scope(conditions, accessible_pools)
    _apply_agent_scope(conditions, supervised_agent_types)

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
    pool_id:                str | None       = None,
    agent_type_id:          str | None       = None,
    role:                   str | None       = None,
    accessible_pools:       list[str] | None = None,
    supervised_agent_types: list[str] | None = None,
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
    if accessible_pools is not None and not accessible_pools:
        return {"data": [], "meta": {"total": 0, "from_dt": since, "to_dt": until}}
    if supervised_agent_types is not None and not supervised_agent_types:
        return {"data": [], "meta": {"total": 0, "from_dt": since, "to_dt": until}}
    try:
        return await asyncio.to_thread(
            _fetch_agent_performance,
            client, database, tenant_id, since, until,
            pool_id, agent_type_id, role, accessible_pools, supervised_agent_types,
        )
    except Exception as exc:
        logger.warning(
            "query_agent_performance_report failed tenant=%s: %s", tenant_id, exc
        )
        return {"data": [], "error": "data_unavailable"}


def _fetch_agent_performance(
    client:          Any,
    db:              str,
    tenant_id:       str,
    since:           str,
    until:           str,
    pool_id:         str | None,
    agent_type_id:   str | None,
    role:            str | None,
    accessible_pools:       list[str] | None = None,
    supervised_agent_types: list[str] | None = None,
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
    _apply_pool_scope(conditions, accessible_pools)
    _apply_agent_scope(conditions, supervised_agent_types)

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


# ─── /reports/evaluations ────────────────────────────────────────────────────

async def query_evaluations_report(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    campaign_id:  str | None = None,
    form_id:      str | None = None,
    evaluator_id: str | None = None,
    eval_status:  str | None = None,
    page:      int = 1,
    page_size: int = 100,
) -> dict:
    """
    Returns individual evaluation results (one row per evaluated session).
    Filters: campaign_id, form_id, evaluator_id, eval_status.
    """
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt)   if to_dt   else _default_to()
    try:
        return await asyncio.to_thread(
            _fetch_evaluations, client, database, tenant_id, since, until,
            campaign_id, form_id, evaluator_id, eval_status, page, page_size,
        )
    except Exception as exc:
        logger.warning("query_evaluations_report failed tenant=%s: %s", tenant_id, exc)
        return {"data": [], "meta": _meta(page, page_size, 0, since, until), "error": "data_unavailable"}


def _fetch_evaluations(
    client: Any, db: str, tenant_id: str,
    since: str, until: str,
    campaign_id: str | None, form_id: str | None,
    evaluator_id: str | None, eval_status: str | None,
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
    if form_id:
        conditions.append("form_id = {form_id:String}")
        params["form_id"] = form_id
    if evaluator_id:
        conditions.append("evaluator_id = {evaluator_id:String}")
        params["evaluator_id"] = evaluator_id
    if eval_status:
        conditions.append("eval_status = {eval_status:String}")
        params["eval_status"] = eval_status

    where = " AND ".join(conditions)
    offset = (page - 1) * page_size

    total = _count(
        client,
        f"SELECT count() FROM {db}.evaluation_results FINAL WHERE {where}",
        params,
    )

    result = client.query(f"""
        SELECT
            result_id, instance_id, session_id, tenant_id,
            evaluator_id, form_id, campaign_id,
            overall_score, eval_status, locked,
            compliance_flags, timestamp
        FROM {db}.evaluation_results FINAL
        WHERE {where}
        ORDER BY timestamp DESC
        LIMIT {page_size} OFFSET {offset}
    """, parameters=params)

    return {"data": _rows_to_dicts(result), "meta": _meta(page, page_size, total, since, until)}


# ─── /reports/evaluations/summary ─────────────────────────────────────────────

async def query_evaluations_summary(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    campaign_id: str | None = None,
    form_id:     str | None = None,
    group_by:    str = "campaign_id",   # campaign_id | evaluator_id | form_id | date
) -> dict:
    """
    Aggregated evaluation summary: avg score, score distribution, count by status.
    group_by controls the breakdown dimension.
    """
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt)   if to_dt   else _default_to()
    # Whitelist grouping dimensions
    allowed_groups = {"campaign_id", "evaluator_id", "form_id", "date"}
    if group_by not in allowed_groups:
        group_by = "campaign_id"
    try:
        return await asyncio.to_thread(
            _fetch_evaluations_summary, client, database, tenant_id, since, until,
            campaign_id, form_id, group_by,
        )
    except Exception as exc:
        logger.warning("query_evaluations_summary failed tenant=%s: %s", tenant_id, exc)
        return {"data": [], "meta": {"from_dt": since, "to_dt": until}, "error": "data_unavailable"}


def _fetch_evaluations_summary(
    client: Any, db: str, tenant_id: str,
    since: str, until: str,
    campaign_id: str | None, form_id: str | None,
    group_by: str,
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
    if form_id:
        conditions.append("form_id = {form_id:String}")
        params["form_id"] = form_id

    where = " AND ".join(conditions)

    # Resolve the GROUP BY expression
    group_col = "toDate(timestamp)" if group_by == "date" else group_by

    result = client.query(f"""
        SELECT
            {group_col}                                  AS group_key,
            count()                                      AS total_evaluated,
            countIf(eval_status = 'submitted')           AS count_submitted,
            countIf(eval_status = 'approved')            AS count_approved,
            countIf(eval_status = 'rejected')            AS count_rejected,
            countIf(eval_status = 'contested')           AS count_contested,
            countIf(eval_status = 'locked')              AS count_locked,
            countIf(locked = 1)                          AS count_locked_flag,
            round(avg(overall_score), 4)                 AS avg_score,
            round(min(overall_score), 4)                 AS min_score,
            round(max(overall_score), 4)                 AS max_score,
            countIf(overall_score >= 0.9)                AS score_excellent,
            countIf(overall_score >= 0.7 AND overall_score < 0.9) AS score_good,
            countIf(overall_score >= 0.5 AND overall_score < 0.7) AS score_fair,
            countIf(overall_score < 0.5)                 AS score_poor,
            countIf(length(compliance_flags) > 0)        AS with_compliance_flags
        FROM {db}.evaluation_results FINAL
        WHERE {where}
        GROUP BY {group_col}
        ORDER BY {group_col} ASC
    """, parameters=params)

    rows = _rows_to_dicts(result)
    return {
        "data":     rows,
        "group_by": group_by,
        "meta":     {"total": len(rows), "from_dt": since, "to_dt": until},
    }


# ─── /reports/agent-performance/daily (Arc 5 MV — v_agent_performance) ──────

async def query_agent_performance_daily(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    pool_id:                str | None       = None,
    agent_type_id:          str | None       = None,
    accessible_pools:       list[str] | None = None,
    supervised_agent_types: list[str] | None = None,
) -> dict:
    """
    Returns daily pre-aggregated performance metrics from the mv_agent_performance_daily
    AggregatingMergeTree, exposed via the v_agent_performance readable view.

    One row per (agent_type_id, pool_id, period_date) — no pagination needed since
    the cardinality is bounded by (agent_types × pools × days).

    Metrics per row:
      total_sessions     — total participation windows in that day
      avg_duration_ms    — mean handle time
      resolution_rate    — fraction with outcome = 'resolved'
      escalation_rate    — fraction with outcome = 'escalated'
      transfer_rate      — fraction with outcome = 'transferred'
      human_rate         — fraction of human-agent sessions

    More efficient than querying segments FINAL because the MV is pre-aggregated
    incrementally; ideal for dashboard trend charts and the Arc 7d performance job.
    """
    since_date = _ch_fmt(from_dt)[:10] if from_dt else _default_from()[:10]
    until_date = _ch_fmt(to_dt)[:10]   if to_dt   else _default_to()[:10]

    if accessible_pools is not None and not accessible_pools:
        return {"data": [], "meta": {"total": 0, "from_date": since_date, "to_date": until_date}}
    if supervised_agent_types is not None and not supervised_agent_types:
        return {"data": [], "meta": {"total": 0, "from_date": since_date, "to_date": until_date}}
    try:
        return await asyncio.to_thread(
            _fetch_agent_performance_daily,
            client, database, tenant_id, since_date, until_date,
            pool_id, agent_type_id, accessible_pools, supervised_agent_types,
        )
    except Exception as exc:
        logger.warning("query_agent_performance_daily failed tenant=%s: %s", tenant_id, exc)
        return {"data": [], "error": "data_unavailable"}


def _fetch_agent_performance_daily(
    client:          Any,
    db:              str,
    tenant_id:       str,
    since_date:      str,
    until_date:      str,
    pool_id:         str | None,
    agent_type_id:   str | None,
    accessible_pools:       list[str] | None,
    supervised_agent_types: list[str] | None = None,
) -> dict:
    conditions = [
        "tenant_id = {tenant_id:String}",
        f"period_date >= toDate('{since_date}')",
        f"period_date <= toDate('{until_date}')",
    ]
    params: dict = {"tenant_id": tenant_id}

    if pool_id:
        conditions.append("pool_id = {pool_id:String}")
        params["pool_id"] = pool_id
    if agent_type_id:
        conditions.append("agent_type_id = {agent_type_id:String}")
        params["agent_type_id"] = agent_type_id
    _apply_pool_scope(conditions, accessible_pools)
    _apply_agent_scope(conditions, supervised_agent_types)

    where = " AND ".join(conditions)

    result = client.query(f"""
        SELECT
            agent_type_id,
            pool_id,
            period_date,
            total_sessions,
            avg_duration_ms,
            round(resolution_rate, 4) AS resolution_rate,
            round(escalation_rate, 4) AS escalation_rate,
            round(transfer_rate,   4) AS transfer_rate,
            round(human_rate,      4) AS human_rate
        FROM {db}.v_agent_performance
        WHERE {where}
        ORDER BY period_date DESC, agent_type_id, pool_id
    """, parameters=params)

    rows = _rows_to_dicts(result)
    return {
        "data": rows,
        "meta": {"total": len(rows), "from_date": since_date, "to_date": until_date},
    }


# ─── /reports/sessions/complexity (Arc 5 MV — v_segment_summary) ─────────────

async def query_session_complexity(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    pool_id:          str | None       = None,
    min_handoffs:     int              = 0,
    accessible_pools: list[str] | None = None,
    page:      int = 1,
    page_size: int = 100,
) -> dict:
    """
    Returns session complexity metrics from the mv_segment_summary AggregatingMergeTree,
    exposed via the v_segment_summary readable view joined with the sessions table
    for date-range and pool filtering.

    Ordered by handoff_count DESC so the most complex sessions surface first.

    Metrics per session:
      segment_count      — total participation windows (primary + specialist + supervisor)
      primary_segments   — primary-agent segments
      specialist_segments— specialist segments (conferences)
      human_segments     — human-agent segments
      total_duration_ms  — sum of all segment durations
      handoff_count      — max sequence_index (0 = no handoffs, 1 = one handoff, …)
      escalation_count   — segments with outcome = 'escalated'
      resolved_count     — segments with outcome = 'resolved'

    Use min_handoffs=1 to filter only sessions that had at least one agent transfer.
    """
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt)   if to_dt   else _default_to()

    if accessible_pools is not None and not accessible_pools:
        return {"data": [], "meta": _meta(page, page_size, 0, since, until)}
    try:
        return await asyncio.to_thread(
            _fetch_session_complexity,
            client, database, tenant_id, since, until,
            pool_id, min_handoffs, accessible_pools, page, page_size,
        )
    except Exception as exc:
        logger.warning("query_session_complexity failed tenant=%s: %s", tenant_id, exc)
        return {"data": [], "error": "data_unavailable"}


def _fetch_session_complexity(
    client:          Any,
    db:              str,
    tenant_id:       str,
    since:           str,
    until:           str,
    pool_id:         str | None,
    min_handoffs:    int,
    accessible_pools: list[str] | None,
    page:            int,
    page_size:       int,
) -> dict:
    offset = (page - 1) * page_size

    # Conditions on the sessions table (for date and pool filtering)
    sess_conditions = [
        "s.tenant_id = {tenant_id:String}",
        f"s.opened_at >= '{since}'",
        f"s.opened_at < '{until}'",
    ]
    params: dict = {"tenant_id": tenant_id}

    if pool_id:
        sess_conditions.append("s.pool_id = {pool_id:String}")
        params["pool_id"] = pool_id
    if accessible_pools:
        pool_list = ", ".join(f"'{p}'" for p in accessible_pools)
        sess_conditions.append(f"s.pool_id IN ({pool_list})")

    sess_where = " AND ".join(sess_conditions)

    # Count query
    count_result = client.query(f"""
        SELECT count()
        FROM {db}.v_segment_summary vs
        INNER JOIN (
            SELECT DISTINCT session_id, pool_id
            FROM {db}.sessions FINAL
            WHERE {sess_where}
        ) s ON vs.session_id = s.session_id AND vs.tenant_id = {'{tenant_id:String}'}
        WHERE vs.handoff_count >= {min_handoffs}
    """, parameters=params)
    total = count_result.result_rows[0][0] if count_result.result_rows else 0

    # Data query
    result = client.query(f"""
        SELECT
            vs.session_id,
            s.pool_id,
            vs.segment_count,
            vs.primary_segments,
            vs.specialist_segments,
            vs.human_segments,
            vs.total_duration_ms,
            vs.handoff_count,
            vs.escalation_count,
            vs.resolved_count
        FROM {db}.v_segment_summary vs
        INNER JOIN (
            SELECT DISTINCT session_id, pool_id
            FROM {db}.sessions FINAL
            WHERE {sess_where}
        ) s ON vs.session_id = s.session_id AND vs.tenant_id = {'{tenant_id:String}'}
        WHERE vs.handoff_count >= {min_handoffs}
        ORDER BY vs.handoff_count DESC, vs.session_id
        LIMIT {page_size}
        OFFSET {offset}
    """, parameters=params)

    rows = _rows_to_dicts(result)
    return {"data": rows, "meta": _meta(page, page_size, total, since, until)}


# ─── Arc 8: agent pause availability ──────────────────────────────────────────

async def query_agent_availability(
    client:                 Any,
    database:               str,
    tenant_id:              str,
    from_dt:                str | None = None,
    to_dt:                  str | None = None,
    pool_id:                str | None = None,
    agent_type_id:          str | None = None,
    accessible_pools:       list[str] | None = None,
    supervised_agent_types: list[str] | None = None,
    page:                   int = 1,
    page_size:              int = 100,
) -> dict:
    """
    Aggregate pause intervals per (agent_type_id, pool_id, date).

    Returns:
      data: [{agent_type_id, pool_id, period_date, total_pauses,
              total_pause_ms, reason_breakdown: [{reason_id, reason_label,
              count, total_ms}]}]
      meta: pagination info
    """
    page_size = min(page_size, _MAX_PAGE_SIZE_JSON)
    since = from_dt or _default_from()
    until = to_dt   or _default_to()
    try:
        if accessible_pools is not None and len(accessible_pools) == 0:
            return {"data": [], "meta": _meta(page, page_size, 0, since, until)}
        if supervised_agent_types is not None and len(supervised_agent_types) == 0:
            return {"data": [], "meta": _meta(page, page_size, 0, since, until)}
        return await asyncio.to_thread(
            _fetch_agent_availability,
            client, database, tenant_id,
            since, until, pool_id, agent_type_id,
            accessible_pools, supervised_agent_types,
            page, page_size,
        )
    except Exception as exc:
        logger.warning("query_agent_availability failed tenant=%s: %s", tenant_id, exc)
        return {"data": [], "error": "data_unavailable"}


def _fetch_agent_availability(
    client:                 Any,
    db:                     str,
    tenant_id:              str,
    since:                  str,
    until:                  str,
    pool_id:                str | None,
    agent_type_id:          str | None,
    accessible_pools:       list[str] | None,
    supervised_agent_types: list[str] | None,
    page:                   int,
    page_size:              int,
) -> dict:
    offset = (page - 1) * page_size
    conditions = [
        "tenant_id = {tenant_id:String}",
        f"paused_at >= '{since}'",
        f"paused_at <  '{until}'",
    ]
    params: dict = {"tenant_id": tenant_id}

    if pool_id:
        conditions.append("pool_id = {pool_id:String}")
        params["pool_id"] = pool_id
    if agent_type_id:
        conditions.append("agent_type_id = {agent_type_id:String}")
        params["agent_type_id"] = agent_type_id
    _apply_pool_scope(conditions, accessible_pools)
    _apply_agent_scope(conditions, supervised_agent_types)

    where = " AND ".join(conditions)

    # distinct (agent_type_id, pool_id, date) combos — for count query
    count_result = client.query(f"""
        SELECT countDistinct((agent_type_id, pool_id, toDate(paused_at)))
        FROM {db}.agent_pause_intervals FINAL
        WHERE {where}
    """, parameters=params)
    total = count_result.result_rows[0][0] if count_result.result_rows else 0

    # Aggregate per (agent_type_id, pool_id, date)
    agg_result = client.query(f"""
        SELECT
            agent_type_id,
            pool_id,
            toDate(paused_at)                   AS period_date,
            count()                              AS total_pauses,
            sum(duration_ms)                     AS total_pause_ms
        FROM {db}.agent_pause_intervals FINAL
        WHERE {where} AND duration_ms IS NOT NULL
        GROUP BY agent_type_id, pool_id, period_date
        ORDER BY period_date DESC, agent_type_id, pool_id
        LIMIT {page_size}
        OFFSET {offset}
    """, parameters=params)

    agg_rows = _rows_to_dicts(agg_result)

    # Fetch reason breakdown for the same filters
    reason_result = client.query(f"""
        SELECT
            agent_type_id,
            pool_id,
            toDate(paused_at)   AS period_date,
            reason_id,
            reason_label,
            count()              AS cnt,
            sum(duration_ms)     AS total_ms
        FROM {db}.agent_pause_intervals FINAL
        WHERE {where} AND duration_ms IS NOT NULL
        GROUP BY agent_type_id, pool_id, period_date, reason_id, reason_label
    """, parameters=params)

    # Index reason breakdown by (agent_type_id, pool_id, period_date)
    breakdown: dict = {}
    for r in _rows_to_dicts(reason_result):
        key = (r["agent_type_id"], r["pool_id"], r["period_date"])
        breakdown.setdefault(key, []).append({
            "reason_id":    r["reason_id"],
            "reason_label": r["reason_label"],
            "count":        r["cnt"],
            "total_ms":     r["total_ms"],
        })

    for row in agg_rows:
        key = (row["agent_type_id"], row["pool_id"], row["period_date"])
        row["reason_breakdown"] = breakdown.get(key, [])
        # convert date to string if needed
        if hasattr(row.get("period_date"), "isoformat"):
            row["period_date"] = row["period_date"].isoformat()

    return {"data": agg_rows, "meta": _meta(page, page_size, total, since, until)}


# ─── /reports/events — unified event stream ───────────────────────────────────
#
# UNION ALL across five source tables:
#   sessions          → session_opened / session_closed
#   messages          → message_sent
#   agent_events      → agent_done / routed
#   agent_pause_intervals → agent_pause / agent_ready
#   workflow_events   → workflow_{event_type}
#
# Each branch normalises to the common shape:
#   event_id, session_id, tenant_id, type, timestamp,
#   channel, pool_id, author_id, author_role, content
#
# The outer WHERE applies user-level filters (event_type, pool_id, channel,
# session_id) so ClickHouse can prune cheaply after the UNION.

_NULL = "CAST(NULL AS Nullable(String))"   # reused in every branch

# Which tables serve which event types (for query pruning)
_SESSION_TYPES  = {"session_opened", "session_closed"}
_MESSAGE_TYPES  = {"message_sent"}
_AGENT_TYPES    = {"agent_done", "routed"}
_PAUSE_TYPES    = {"agent_pause"}
_READY_TYPES    = {"agent_ready"}


def _events_sql_branches(
    db:               str,
    tenant_id:        str,
    since:            str,
    until:            str,
    event_type:       str | None,
    session_id:       str | None,
    accessible_pools: list[str] | None,
) -> tuple[list[str], dict]:
    """
    Builds the list of UNION ALL branch SQL strings and a shared parameters dict.

    All user-controlled values are passed as ClickHouse named parameters
    ({ev_tid:String} etc.) — never interpolated into the SQL string.

    Optimisation: when event_type is specified, only branches that can
    produce that type are included.  When event_type is None, all branches
    are included.

    Returns: (branches, params) where params must be passed to every query
    that uses the assembled UNION ALL SQL.
    """
    include_session  = (not event_type) or event_type in _SESSION_TYPES
    include_messages = (not event_type) or event_type in _MESSAGE_TYPES
    include_agent    = (not event_type) or event_type in _AGENT_TYPES
    include_pause    = (not event_type) or event_type in _PAUSE_TYPES
    include_ready    = (not event_type) or event_type in _READY_TYPES
    include_workflow = (not event_type) or (event_type and event_type.startswith("workflow_"))

    # Shared named params for all branches
    params: dict = {
        "ev_tid":   tenant_id,
        "ev_since": since,
        "ev_until": until,
    }
    if session_id:
        params["ev_sid"] = session_id

    # accessible_pools — server-controlled (from JWT), not user input.
    # Still use an IN-list but the values come from verified JWT claims.
    pool_scope = ""
    if accessible_pools:
        pool_list = ", ".join(f"'{p}'" for p in accessible_pools)
        pool_scope = f" AND pool_id IN ({pool_list})"

    # session_id filter inside subqueries — parameterised
    sid_filter_sess  = " AND session_id = {ev_sid:String}"    if session_id else ""
    sid_filter_msg   = " AND m.session_id = {ev_sid:String}"  if session_id else ""
    sid_filter_agent = " AND ae.session_id = {ev_sid:String}" if session_id else ""

    branches: list[str] = []

    if include_session:
        # session_opened
        branches.append(f"""
    SELECT
        session_id                        AS event_id,
        session_id,
        tenant_id,
        'session_opened'                  AS type,
        opened_at                         AS timestamp,
        channel,
        pool_id,
        {_NULL}                           AS author_id,
        'system'                          AS author_role,
        {_NULL}                           AS content
    FROM {db}.sessions FINAL
    WHERE tenant_id = {{ev_tid:String}}
      AND opened_at >= {{ev_since:String}} AND opened_at <= {{ev_until:String}}
      {pool_scope}{sid_filter_sess}""")

        # session_closed
        branches.append(f"""
    SELECT
        concat(session_id, ':closed')     AS event_id,
        session_id,
        tenant_id,
        'session_closed'                  AS type,
        assumeNotNull(closed_at)          AS timestamp,
        channel,
        pool_id,
        {_NULL}                           AS author_id,
        'system'                          AS author_role,
        if(close_reason IS NOT NULL, close_reason, {_NULL}) AS content
    FROM {db}.sessions FINAL
    WHERE tenant_id = {{ev_tid:String}}
      AND closed_at IS NOT NULL
      AND closed_at >= {{ev_since:String}} AND closed_at <= {{ev_until:String}}
      {pool_scope}{sid_filter_sess}""")

    if include_messages:
        pool_join_filter = ""
        if accessible_pools:
            pool_list = ", ".join(f"'{p}'" for p in accessible_pools)
            pool_join_filter = f" AND s.pool_id IN ({pool_list})"
        branches.append(f"""
    SELECT
        m.message_id                      AS event_id,
        m.session_id,
        m.tenant_id,
        'message_sent'                    AS type,
        m.timestamp,
        m.channel,
        s.pool_id,
        if(m.author_id IS NOT NULL, m.author_id, {_NULL}) AS author_id,
        m.author_role,
        if(m.content IS NOT NULL, m.content, {_NULL}) AS content
    FROM {db}.messages FINAL m
    LEFT JOIN (
        SELECT session_id, pool_id
        FROM {db}.sessions FINAL
        WHERE tenant_id = {{ev_tid:String}}{pool_join_filter}
    ) s ON m.session_id = s.session_id
    WHERE m.tenant_id = {{ev_tid:String}}
      AND m.timestamp >= {{ev_since:String}} AND m.timestamp <= {{ev_until:String}}
      AND m.visibility = 'all'{sid_filter_msg}""")

    if include_agent:
        branches.append(f"""
    SELECT
        ae.event_id,
        ae.session_id,
        ae.tenant_id,
        ae.event_type                     AS type,
        ae.timestamp,
        s.channel,
        ae.pool_id,
        if(ae.instance_id != '', ae.instance_id, {_NULL}) AS author_id,
        ae.agent_type_id                  AS author_role,
        if(ae.outcome IS NOT NULL, ae.outcome, {_NULL}) AS content
    FROM {db}.agent_events FINAL ae
    LEFT JOIN (
        SELECT session_id, channel
        FROM {db}.sessions FINAL
        WHERE tenant_id = {{ev_tid:String}}
    ) s ON ae.session_id = s.session_id
    WHERE ae.tenant_id = {{ev_tid:String}}
      AND ae.timestamp >= {{ev_since:String}} AND ae.timestamp <= {{ev_until:String}}
      {pool_scope.replace('pool_id', 'ae.pool_id')}{sid_filter_agent}""")

    if include_pause:
        branches.append(f"""
    SELECT
        interval_id                       AS event_id,
        {_NULL}                           AS session_id,
        tenant_id,
        'agent_pause'                     AS type,
        paused_at                         AS timestamp,
        {_NULL}                           AS channel,
        pool_id,
        instance_id                       AS author_id,
        agent_type_id                     AS author_role,
        reason_label                      AS content
    FROM {db}.agent_pause_intervals FINAL
    WHERE tenant_id = {{ev_tid:String}}
      AND paused_at >= {{ev_since:String}} AND paused_at <= {{ev_until:String}}
      {pool_scope}""")

    if include_ready:
        branches.append(f"""
    SELECT
        concat(interval_id, ':r')         AS event_id,
        {_NULL}                           AS session_id,
        tenant_id,
        'agent_ready'                     AS type,
        assumeNotNull(resumed_at)         AS timestamp,
        {_NULL}                           AS channel,
        pool_id,
        instance_id                       AS author_id,
        agent_type_id                     AS author_role,
        reason_label                      AS content
    FROM {db}.agent_pause_intervals FINAL
    WHERE tenant_id = {{ev_tid:String}}
      AND resumed_at IS NOT NULL
      AND resumed_at >= {{ev_since:String}} AND resumed_at <= {{ev_until:String}}
      {pool_scope}""")

    if include_workflow:
        # event_type in workflow_events is e.g. 'triggered', 'completed', etc.
        # We prefix with 'workflow_' to match front-end expectations.
        # If a specific workflow type is requested, strip the prefix for the inner filter.
        wf_type_filter = ""
        if event_type and event_type.startswith("workflow_"):
            params["ev_wf_type"] = event_type[len("workflow_"):]
            wf_type_filter = " AND event_type = {ev_wf_type:String}"
        branches.append(f"""
    SELECT
        we.event_id,
        {_NULL}                           AS session_id,
        we.tenant_id,
        concat('workflow_', we.event_type) AS type,
        we.timestamp,
        {_NULL}                           AS channel,
        {_NULL}                           AS pool_id,
        we.instance_id                    AS author_id,
        'workflow'                        AS author_role,
        if(we.status IS NOT NULL, we.status, {_NULL}) AS content
    FROM {db}.workflow_events FINAL we
    WHERE we.tenant_id = {{ev_tid:String}}
      AND we.timestamp >= {{ev_since:String}} AND we.timestamp <= {{ev_until:String}}
      {wf_type_filter}""")

    return branches, params


async def query_events(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    session_id:       str | None       = None,
    pool_id:          str | None       = None,
    channel:          str | None       = None,
    event_type:       str | None       = None,
    accessible_pools: list[str] | None = None,
    page:      int = 1,
    page_size: int = 100,
) -> dict:
    """
    Unified event stream from sessions, messages, agent_events,
    agent_pause_intervals and workflow_events.

    Returns: { data: [EventRow], meta: { total, page, page_size, from_dt, to_dt } }
    """
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt)   if to_dt   else _default_to()
    if accessible_pools is not None and not accessible_pools:
        return {"data": [], "meta": _meta(page, page_size, 0, since, until)}
    try:
        return await asyncio.to_thread(
            _fetch_events, client, database, tenant_id, since, until,
            session_id, pool_id, channel, event_type, accessible_pools, page, page_size,
        )
    except Exception as exc:
        logger.warning("query_events failed tenant=%s: %s", tenant_id, exc)
        return {"data": [], "meta": _meta(page, page_size, 0, since, until), "error": "data_unavailable"}


def _fetch_events(
    client: Any, db: str, tenant_id: str,
    since: str, until: str,
    session_id: str | None, pool_id: str | None,
    channel: str | None, event_type: str | None,
    accessible_pools: list[str] | None,
    page: int, page_size: int,
) -> dict:
    branches, params = _events_sql_branches(
        db, tenant_id, since, until, event_type, session_id, accessible_pools,
    )
    if not branches:
        return {"data": [], "meta": _meta(page, page_size, 0, since, until)}

    union_sql = "\n    UNION ALL\n".join(branches)

    # Outer filters applied after UNION — all values are ClickHouse named params.
    outer_conditions: list[str] = []
    if event_type:
        outer_conditions.append("type = {out_event_type:String}")
        params["out_event_type"] = event_type
    if pool_id:
        outer_conditions.append("pool_id = {out_pool_id:String}")
        params["out_pool_id"] = pool_id
    if channel:
        outer_conditions.append("channel = {out_channel:String}")
        params["out_channel"] = channel
    if session_id:
        outer_conditions.append("session_id = {out_sid:String}")
        params["out_sid"] = session_id

    outer_where = ("WHERE " + " AND ".join(outer_conditions)) if outer_conditions else ""
    offset = (page - 1) * page_size

    count_sql = f"""
        SELECT count()
        FROM ({union_sql}) AS events
        {outer_where}
    """
    total = _count(client, count_sql, params)

    data_sql = f"""
        SELECT event_id, session_id, tenant_id, type, timestamp,
               channel, pool_id, author_id, author_role, content
        FROM ({union_sql}) AS events
        {outer_where}
        ORDER BY timestamp DESC
        LIMIT {page_size} OFFSET {offset}
    """
    result = client.query(data_sql, parameters=params)
    return {"data": _rows_to_dicts(result), "meta": _meta(page, page_size, total, since, until)}


# ─── /reports/journeys (Arc 10) ───────────────────────────────────────────────

async def query_journeys_report(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    skill_id:    str | None = None,
    status:      str | None = None,
    customer_id: str | None = None,
    page:      int = 1,
    page_size: int = 100,
) -> dict:
    """
    Journey list and KPI summary from journey_events ClickHouse table.

    Returns:
      data: list of journey summaries (one per journey_id)
      kpis: per-skill_id aggregations (resolution_rate, median_duration_ms, avg_session_count)
      meta: pagination info
    """
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt)   if to_dt   else _default_to()
    try:
        return await asyncio.to_thread(
            _fetch_journeys, client, database, tenant_id, since, until,
            skill_id, status, customer_id, page, page_size,
        )
    except Exception as exc:
        logger.warning("query_journeys_report failed tenant=%s: %s", tenant_id, exc)
        return {"data": [], "kpis": [], "meta": _meta(page, page_size, 0, since, until), "error": "data_unavailable"}


def _fetch_journeys(
    client:      Any,
    db:          str,
    tenant_id:   str,
    since:       str,
    until:       str,
    skill_id:    str | None,
    status:      str | None,
    customer_id: str | None,
    page:        int,
    page_size:   int,
) -> dict:
    """
    Reconstructs journey state from journey_events using argMax aggregations.
    Each event carries the current journey status — we pick the latest.
    session_count = 1 (origin) + count(distinct linked sessions).
    """
    base_conditions = [
        "tenant_id = {tenant_id:String}",
        f"timestamp >= '{since}'",
        f"timestamp <= '{until}'",
    ]
    params: dict = {"tenant_id": tenant_id}

    if skill_id:
        base_conditions.append("skill_id = {skill_id:String}")
        params["skill_id"] = skill_id
    if customer_id:
        base_conditions.append("customer_id = {customer_id:String}")
        params["customer_id"] = customer_id

    base_where = " AND ".join(base_conditions)

    # ── Journey summary (one row per journey_id) ──────────────────────────────
    # We aggregate all events to reconstruct journey state.
    # status comes from argMax on timestamp (last event wins).
    # session_count = 1 (origin) + countDistinct of session_ids from link events.
    summary_sql = f"""
        SELECT
            journey_id,
            argMax(skill_id,           timestamp)  AS skill_id,
            argMax(status,             timestamp)  AS status,
            argMax(customer_id,        timestamp)  AS customer_id,
            argMax(origin_session_id,  timestamp)  AS origin_session_id,
            argMax(workflow_instance_id, timestamp) AS workflow_instance_id,
            min(timestamp)                         AS created_at,
            max(timestamp)                         AS last_event_at,
            countDistinctIf(
                session_id,
                event_type = 'journey_session_linked' AND session_id IS NOT NULL
            ) + 1                                  AS session_count
        FROM {db}.journey_events FINAL
        WHERE {base_where}
        GROUP BY journey_id
        HAVING 1=1
    """

    # Apply status filter at HAVING level (status is aggregated)
    having_extra = ""
    if status:
        having_extra = f" AND argMax(status, timestamp) = '{status}'"

    count_sql = f"SELECT count() FROM ({summary_sql}{having_extra}) AS j"
    total = _count(client, count_sql, params)

    offset = (page - 1) * page_size
    result = client.query(f"""
        SELECT *
        FROM ({summary_sql}{having_extra}) AS j
        ORDER BY created_at DESC
        LIMIT {page_size} OFFSET {offset}
    """, parameters=params)

    rows = _rows_to_dicts(result)

    # ── KPI summary per skill_id ──────────────────────────────────────────────
    kpi_result = client.query(f"""
        SELECT
            skill_id,
            count()                                             AS total_journeys,
            countIf(status = 'completed')                       AS completed_count,
            countIf(status = 'failed')                          AS failed_count,
            countIf(status = 'active')                          AS active_count,
            round(countIf(status = 'completed') / count(), 4)   AS resolution_rate,
            avg(session_count)                                  AS avg_session_count,
            median(duration_ms)                                 AS median_duration_ms
        FROM (
            SELECT
                argMax(skill_id,  timestamp)                          AS skill_id,
                argMax(status,    timestamp)                          AS status,
                min(timestamp)                                        AS created_at,
                max(timestamp)                                        AS last_event_at,
                dateDiff('millisecond', min(timestamp), max(timestamp)) AS duration_ms,
                countDistinctIf(
                    session_id,
                    event_type = 'journey_session_linked' AND session_id IS NOT NULL
                ) + 1                                                 AS session_count
            FROM {db}.journey_events FINAL
            WHERE {base_where}
            GROUP BY journey_id
        ) AS j
        GROUP BY skill_id
        ORDER BY total_journeys DESC
    """, parameters=params)

    kpi_rows = _rows_to_dicts(kpi_result)

    return {
        "data": rows,
        "kpis": kpi_rows,
        "meta": _meta(page, page_size, total, since, until),
    }


# ─── Arc 12: Agent Business Events ────────────────────────────────────────────

async def query_agent_events_series(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    category:    str | None = None,
    pool_id:     str | None = None,
    skill_id:    str | None = None,
    granularity: str = "day",  # day | week | hour
) -> dict:
    """
    Time-series of agent business events aggregated by (period, category).

    granularity:
      hour  — toStartOfHour(emitted_at)
      day   — toDate(emitted_at)           [default]
      week  — toMonday(emitted_at)

    Returns:
      data: list of {period, category, category_l1..l4, count, total_value, avg_value, min_value, max_value}
      meta: {from_dt, to_dt, granularity, category, pool_id, skill_id}
    """
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt)   if to_dt   else _default_to()
    try:
        return await asyncio.to_thread(
            _fetch_agent_events_series,
            client, database, tenant_id, since, until,
            category, pool_id, skill_id, granularity,
        )
    except Exception as exc:
        logger.warning("query_agent_events_series failed tenant=%s: %s", tenant_id, exc)
        return {
            "data": [],
            "meta": {
                "from_dt": since, "to_dt": until,
                "granularity": granularity, "category": category,
                "pool_id": pool_id, "skill_id": skill_id,
            },
            "error": "data_unavailable",
        }


def _fetch_agent_events_series(
    client:      Any,
    db:          str,
    tenant_id:   str,
    since:       str,
    until:       str,
    category:    str | None,
    pool_id:     str | None,
    skill_id:    str | None,
    granularity: str,
) -> dict:
    # Truncation function per granularity
    trunc = {
        "hour": "toStartOfHour(emitted_at)",
        "week": "toMonday(emitted_at)",
    }.get(granularity, "toDate(emitted_at)")

    conditions = [
        "tenant_id = {tenant_id:String}",
        f"emitted_at >= '{since}'",
        f"emitted_at <= '{until}'",
    ]
    params: dict = {"tenant_id": tenant_id}

    if category:
        # Support prefix match: "pool.skill" matches "pool.skill.metric_x"
        conditions.append("startsWith(category, {category:String})")
        params["category"] = category
    if pool_id:
        conditions.append("pool_id = {pool_id:String}")
        params["pool_id"] = pool_id
    if skill_id:
        conditions.append("skill_id = {skill_id:String}")
        params["skill_id"] = skill_id

    where = " AND ".join(conditions)

    result = client.query(f"""
        SELECT
            {trunc}              AS period,
            category,
            category_l1,
            category_l2,
            category_l3,
            category_l4,
            count()              AS count,
            sum(value)           AS total_value,
            avg(value)           AS avg_value,
            min(value)           AS min_value,
            max(value)           AS max_value
        FROM {db}.agent_business_events
        WHERE {where}
        GROUP BY period, category, category_l1, category_l2, category_l3, category_l4
        ORDER BY period ASC, category ASC
    """, parameters=params)

    return {
        "data": _rows_to_dicts(result),
        "meta": {
            "from_dt":     since,
            "to_dt":       until,
            "granularity": granularity,
            "category":    category,
            "pool_id":     pool_id,
            "skill_id":    skill_id,
        },
    }


async def query_agent_events_summary(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    category: str | None = None,
    pool_id:  str | None = None,
    group_by: str = "category",  # category | skill_id | pool_id | agent_type_id
    page:     int = 1,
    page_size: int = 100,
) -> dict:
    """
    Aggregated summary of agent business events grouped by the chosen dimension.

    group_by:
      category      — one row per distinct category value  [default]
      skill_id      — one row per skill_id
      pool_id       — one row per pool_id
      agent_type_id — one row per agent_type_id

    Returns:
      data: list of {group_key, count, total_value, avg_value, min_value, max_value,
                     first_seen, last_seen}
      meta: pagination info
    """
    # Validate group_by to prevent injection
    VALID_GROUP_BY = {"category", "skill_id", "pool_id", "agent_type_id"}
    if group_by not in VALID_GROUP_BY:
        group_by = "category"

    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt)   if to_dt   else _default_to()
    try:
        return await asyncio.to_thread(
            _fetch_agent_events_summary,
            client, database, tenant_id, since, until,
            category, pool_id, group_by, page, page_size,
        )
    except Exception as exc:
        logger.warning("query_agent_events_summary failed tenant=%s: %s", tenant_id, exc)
        return {
            "data": [],
            "meta": _meta(page, page_size, 0, since, until),
            "error": "data_unavailable",
        }


def _fetch_agent_events_summary(
    client:    Any,
    db:        str,
    tenant_id: str,
    since:     str,
    until:     str,
    category:  str | None,
    pool_id:   str | None,
    group_by:  str,
    page:      int,
    page_size: int,
) -> dict:
    conditions = [
        "tenant_id = {tenant_id:String}",
        f"emitted_at >= '{since}'",
        f"emitted_at <= '{until}'",
    ]
    params: dict = {"tenant_id": tenant_id}

    if category:
        conditions.append("startsWith(category, {category:String})")
        params["category"] = category
    if pool_id:
        conditions.append("pool_id = {pool_id:String}")
        params["pool_id"] = pool_id

    where = " AND ".join(conditions)

    count_sql = f"""
        SELECT count() FROM (
            SELECT {group_by} AS group_key
            FROM {db}.agent_business_events
            WHERE {where}
            GROUP BY group_key
        ) AS s
    """
    total = _count(client, count_sql, params)

    offset = (page - 1) * page_size
    result = client.query(f"""
        SELECT
            {group_by}           AS group_key,
            count()              AS count,
            sum(value)           AS total_value,
            avg(value)           AS avg_value,
            min(value)           AS min_value,
            max(value)           AS max_value,
            min(emitted_at)      AS first_seen,
            max(emitted_at)      AS last_seen
        FROM {db}.agent_business_events
        WHERE {where}
        GROUP BY group_key
        ORDER BY count DESC
        LIMIT {page_size} OFFSET {offset}
    """, parameters=params)

    return {
        "data": _rows_to_dicts(result),
        "meta": _meta(page, page_size, total, since, until),
    }


async def query_agent_events_categories(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    pool_id:  str | None = None,
    skill_id: str | None = None,
) -> dict:
    """
    Catalogue of distinct category values active in the time window.

    Used by the Dashboard AddCardModal to populate the category selector.

    Returns:
      data: list of {category, category_l1, category_l2, category_l3, category_l4,
                     event_count, last_seen}
      sorted by category ASC.
    """
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt)   if to_dt   else _default_to()
    try:
        return await asyncio.to_thread(
            _fetch_agent_events_categories,
            client, database, tenant_id, since, until,
            pool_id, skill_id,
        )
    except Exception as exc:
        logger.warning("query_agent_events_categories failed tenant=%s: %s", tenant_id, exc)
        return {"data": [], "meta": {"from_dt": since, "to_dt": until}, "error": "data_unavailable"}


def _fetch_agent_events_categories(
    client:   Any,
    db:       str,
    tenant_id: str,
    since:    str,
    until:    str,
    pool_id:  str | None,
    skill_id: str | None,
) -> dict:
    conditions = [
        "tenant_id = {tenant_id:String}",
        f"emitted_at >= '{since}'",
        f"emitted_at <= '{until}'",
    ]
    params: dict = {"tenant_id": tenant_id}

    if pool_id:
        conditions.append("pool_id = {pool_id:String}")
        params["pool_id"] = pool_id
    if skill_id:
        conditions.append("skill_id = {skill_id:String}")
        params["skill_id"] = skill_id

    where = " AND ".join(conditions)

    result = client.query(f"""
        SELECT
            category,
            any(category_l1)     AS category_l1,
            any(category_l2)     AS category_l2,
            any(category_l3)     AS category_l3,
            any(category_l4)     AS category_l4,
            count()              AS event_count,
            max(emitted_at)      AS last_seen
        FROM {db}.agent_business_events
        WHERE {where}
        GROUP BY category
        ORDER BY category ASC
        LIMIT 500
    """, parameters=params)

    return {
        "data": _rows_to_dicts(result),
        "meta": {"from_dt": since, "to_dt": until, "pool_id": pool_id, "skill_id": skill_id},
    }


# ─── /reports/evaluator-calibration (Arc 13) ─────────────────────────────────

async def query_evaluator_calibration(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None = None,
    to_dt:     str | None = None,
    *,
    campaign_id:  str | None = None,
    evaluator_id: str | None = None,
    skill_version: str | None = None,
    granularity:   str = "day",   # "day" | "week"
) -> dict:
    """
    Calibration score time-series per skill version × time.

    calibration_score = approved_count / total_reviewed × 100

    Returns:
      data:    list of {period, skill_version, evaluator_id, total, approved,
                        recalibrated, bias_flagged, calibration_score}
      summary: {total, approved, recalibrated, bias_flagged, calibration_score}
      meta:    {from_dt, to_dt, campaign_id, evaluator_id, granularity}
    """
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt)   if to_dt   else _default_to()
    try:
        return await asyncio.to_thread(
            _fetch_evaluator_calibration,
            client, database, tenant_id, since, until,
            campaign_id, evaluator_id, skill_version, granularity,
        )
    except Exception as exc:
        logger.warning("query_evaluator_calibration failed tenant=%s: %s", tenant_id, exc)
        return {
            "data": [],
            "summary": {"total": 0, "approved": 0, "recalibrated": 0, "bias_flagged": 0, "calibration_score": None},
            "meta": {"from_dt": since, "to_dt": until, "campaign_id": campaign_id, "granularity": granularity},
            "error": "data_unavailable",
        }


def _fetch_evaluator_calibration(
    client:       Any,
    db:           str,
    tenant_id:    str,
    since:        str,
    until:        str,
    campaign_id:  str | None,
    evaluator_id: str | None,
    skill_version: str | None,
    granularity:  str,
) -> dict:
    conditions = [
        "tenant_id = {tenant_id:String}",
        f"event_time >= '{since}'",
        f"event_time < '{until}'",
    ]
    params: dict = {"tenant_id": tenant_id}

    if campaign_id:
        conditions.append("campaign_id = {campaign_id:String}")
        params["campaign_id"] = campaign_id
    if evaluator_id:
        conditions.append("evaluator_id = {evaluator_id:String}")
        params["evaluator_id"] = evaluator_id
    if skill_version:
        conditions.append("skill_version = {skill_version:String}")
        params["skill_version"] = skill_version

    where = " AND ".join(conditions)

    # Period truncation expression per granularity
    if granularity == "week":
        period_expr = "toMonday(event_time)"
    else:  # day (default)
        period_expr = "toDate(event_time)"

    # Time-series: group by (period, skill_version, evaluator_id)
    ts_result = client.query(f"""
        SELECT
            {period_expr}                                                         AS period,
            skill_version,
            evaluator_id,
            count()                                                               AS total,
            countIf(decision = 'approved')                                        AS approved,
            countIf(decision = 'recalibrated')                                    AS recalibrated,
            countIf(decision = 'bias_flagged')                                    AS bias_flagged,
            round(
                if(count() > 0, countIf(decision = 'approved') * 100.0 / count(), null),
                2
            )                                                                     AS calibration_score
        FROM {db}.calibration_events FINAL
        WHERE {where}
        GROUP BY period, skill_version, evaluator_id
        ORDER BY period ASC, skill_version ASC
    """, parameters=params)

    rows = _rows_to_dicts(ts_result)

    # Aggregate summary across the whole period
    agg_result = client.query(f"""
        SELECT
            count()                               AS total,
            countIf(decision = 'approved')        AS approved,
            countIf(decision = 'recalibrated')    AS recalibrated,
            countIf(decision = 'bias_flagged')    AS bias_flagged,
            round(
                if(count() > 0,
                   countIf(decision = 'approved') * 100.0 / count(),
                   null),
                2
            )                                     AS calibration_score
        FROM {db}.calibration_events FINAL
        WHERE {where}
    """, parameters=params)

    agg_rows = _rows_to_dicts(agg_result)
    summary  = agg_rows[0] if agg_rows else {
        "total": 0, "approved": 0, "recalibrated": 0, "bias_flagged": 0, "calibration_score": None,
    }

    # Normalise period to ISO string for JSON serialisation
    for row in rows:
        p = row.get("period")
        if p is not None and not isinstance(p, str):
            row["period"] = str(p)

    return {
        "data":    rows,
        "summary": summary,
        "meta": {
            "from_dt":      since,
            "to_dt":        until,
            "campaign_id":  campaign_id,
            "evaluator_id": evaluator_id,
            "skill_version": skill_version,
            "granularity":  granularity,
        },
    }
