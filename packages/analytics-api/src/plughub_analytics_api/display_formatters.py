"""
display_formatters.py
Query helpers and data-shape formatters for the /reports/display/* endpoints.

Each formatter produces a dict matching the frontend DisplayTool data contracts
defined in platform-ui/src/dashboard/tools/types.ts:

  BarChartData / LineChartData (identical shape):
    { x_labels: string[], series: [{name, data: number[], color?}], stacked?, y_label? }

  DonutData:
    { labels: string[], values: number[], total? }

  TableData:
    { columns: [{key, label, sortable?, align?}], rows: [{...}], total? }

  MetricCardData:
    { value: number, label: string, format: str, trend?: number }

All formatters are async. Callers (display.py) stay thin — one formatter call per route.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from .query import get_pool_snapshots
from .reports_query import query_agent_performance_report
from .timeseries_query import (
    query_handle_time_timeseries,
    query_score_timeseries,
    query_volume_timeseries,
)

logger = logging.getLogger("plughub.analytics.display")


# ─── datetime helpers ──────────────────────────────────────────────────────────

_FMT = "%Y-%m-%d %H:%M:%S"


def _default_from() -> str:
    return (datetime.utcnow() - timedelta(days=7)).strftime(_FMT)


def _default_to() -> str:
    return datetime.utcnow().strftime(_FMT)


def _fmt_dt(s: str | None) -> str:
    """
    Parses:
      - ISO8601 or 'YYYY-MM-DD'  → ClickHouse UTC datetime string
      - Relative offsets '-Nd'   → N days ago (e.g. '-7d', '-30d')
      - Empty / None             → now
      - Anything else            → now (safe fallback)
    """
    if not s:
        return _default_to()
    stripped = s.strip()
    # Relative date: -Nd (e.g. '-7d' means 7 days ago)
    if stripped.startswith("-") and stripped.endswith("d"):
        try:
            days = int(stripped[1:-1])
            return (datetime.utcnow() - timedelta(days=days)).strftime(_FMT)
        except ValueError:
            return _default_from()
    try:
        s2 = stripped.replace("Z", "+00:00")
        if "T" in s2 or " " in s2:
            dt = datetime.fromisoformat(s2).astimezone(timezone.utc)
        else:
            # date-only "YYYY-MM-DD" (from <input type="date">)
            parts = s2.split("+")[0].split("-")
            dt = datetime(int(parts[0]), int(parts[1]), int(parts[2]),
                          tzinfo=timezone.utc)
        return dt.strftime(_FMT)
    except Exception:
        return _default_to()


def _prev_period(since: str, until: str) -> tuple[str, str]:
    """Returns the prior period of the same length immediately before `since`."""
    try:
        s = datetime.strptime(since, _FMT).replace(tzinfo=timezone.utc)
        u = datetime.strptime(until, _FMT).replace(tzinfo=timezone.utc)
        duration = u - s
        if duration.total_seconds() <= 0:
            duration = timedelta(days=7)
        return (s - duration).strftime(_FMT), s.strftime(_FMT)
    except Exception:
        since_dt = datetime.utcnow() - timedelta(days=14)
        until_dt = datetime.utcnow() - timedelta(days=7)
        return since_dt.strftime(_FMT), until_dt.strftime(_FMT)


def _pool_clause(accessible_pools: list[str] | None) -> str:
    if accessible_pools is None:
        return ""
    if not accessible_pools:
        return "AND 1=0"
    pool_list = ", ".join(f"'{p}'" for p in accessible_pools)
    return f"AND pool_id IN ({pool_list})"


def _run(client: Any, sql: str, params: dict) -> list[dict]:
    result = client.query(sql, parameters=params)
    cols = result.column_names
    return [dict(zip(cols, row)) for row in result.result_rows]


# ─── shared: convert buckets → { x_labels, series } ──────────────────────────

def _buckets_to_chart(
    buckets: list[dict],
    series_name: str,
    y_label: str | None = None,
) -> dict:
    """
    Converts timeseries buckets from query_*_timeseries into the shared
    BarChartData / LineChartData shape:
      { x_labels: [...], series: [{ name, data }], y_label? }
    """
    x_labels = [b.get("bucket", "") for b in buckets]
    # Convert datetime objects to ISO strings if needed
    x_strs: list[str] = []
    for x in x_labels:
        if isinstance(x, datetime):
            if x.tzinfo is None:
                x = x.replace(tzinfo=timezone.utc)
            x_strs.append(x.isoformat())
        else:
            x_strs.append(str(x) if x is not None else "")

    data = [round(float(b.get("value", 0) or 0), 4) for b in buckets]
    result: dict = {
        "x_labels": x_strs,
        "series":   [{"name": series_name, "data": data}],
    }
    if y_label:
        result["y_label"] = y_label
    return result


# ─── /reports/display/session-volume ─────────────────────────────────────────

async def fmt_session_volume(
    client:           Any,
    database:         str,
    tenant_id:        str,
    from_dt:          str | None,
    to_dt:            str | None,
    pool_id:          str | None,
    accessible_pools: list[str] | None,
) -> dict:
    """Returns BarChartData/LineChartData — sessions per time bucket."""
    result = await query_volume_timeseries(
        client           = client,
        database         = database,
        tenant_id        = tenant_id,
        from_dt          = from_dt,
        to_dt            = to_dt,
        interval         = 60,
        pool_id          = pool_id,
        accessible_pools = accessible_pools,
    )
    return _buckets_to_chart(result.get("buckets", []), "Sessões")


# ─── /reports/display/handle-time ─────────────────────────────────────────────

async def fmt_handle_time(
    client:           Any,
    database:         str,
    tenant_id:        str,
    from_dt:          str | None,
    to_dt:            str | None,
    pool_id:          str | None,
    accessible_pools: list[str] | None,
) -> dict:
    """Returns BarChartData/LineChartData — avg handle time (ms) per bucket."""
    result = await query_handle_time_timeseries(
        client           = client,
        database         = database,
        tenant_id        = tenant_id,
        from_dt          = from_dt,
        to_dt            = to_dt,
        interval         = 60,
        pool_id          = pool_id,
        accessible_pools = accessible_pools,
    )
    return _buckets_to_chart(result.get("buckets", []), "Tempo Médio (ms)", y_label="ms")


# ─── /reports/display/evaluation-score ───────────────────────────────────────

async def fmt_evaluation_score(
    client:           Any,
    database:         str,
    tenant_id:        str,
    from_dt:          str | None,
    to_dt:            str | None,
    pool_id:          str | None,  # no-op for evaluation_results
    accessible_pools: list[str] | None,
) -> dict:
    """Returns BarChartData/LineChartData — avg evaluation score per bucket."""
    result = await query_score_timeseries(
        client           = client,
        database         = database,
        tenant_id        = tenant_id,
        from_dt          = from_dt,
        to_dt            = to_dt,
        interval         = 60,
        accessible_pools = accessible_pools,
    )
    return _buckets_to_chart(result.get("buckets", []), "Nota Média", y_label="score")


# ─── /reports/display/sessions-by-pool ───────────────────────────────────────

def _fetch_sessions_by_pool(
    client:           Any,
    db:               str,
    tenant_id:        str,
    since:            str,
    until:            str,
    accessible_pools: list[str] | None,
) -> list[dict]:
    pool_clause = _pool_clause(accessible_pools)
    sql = f"""
        SELECT pool_id, count() AS cnt
        FROM {db}.sessions
        WHERE tenant_id = {{tenant_id:String}}
          AND opened_at >= '{since}'
          AND opened_at <  '{until}'
          {pool_clause}
        GROUP BY pool_id
        ORDER BY cnt DESC
    """
    return _run(client, sql, {"tenant_id": tenant_id})


async def fmt_sessions_by_pool(
    client:           Any,
    database:         str,
    tenant_id:        str,
    from_dt:          str | None,
    to_dt:            str | None,
    accessible_pools: list[str] | None,
) -> dict:
    """Returns BarChartData — session count grouped by pool_id."""
    since = _fmt_dt(from_dt) if from_dt else _default_from()
    until = _fmt_dt(to_dt)   if to_dt   else _default_to()

    empty = {"x_labels": [], "series": [{"name": "Sessões", "data": []}]}
    if accessible_pools is not None and not accessible_pools:
        return empty

    try:
        rows = await asyncio.to_thread(
            _fetch_sessions_by_pool, client, database, tenant_id, since, until, accessible_pools
        )
        return {
            "x_labels": [r.get("pool_id") or "unknown" for r in rows],
            "series":   [{"name": "Sessões", "data": [int(r["cnt"]) for r in rows]}],
        }
    except Exception as exc:
        logger.warning("fmt_sessions_by_pool failed tenant=%s: %s", tenant_id, exc)
        return empty


# ─── /reports/display/outcome-distribution ────────────────────────────────────

def _fetch_outcome_distribution(
    client:           Any,
    db:               str,
    tenant_id:        str,
    since:            str,
    until:            str,
    pool_id:          str | None,
    accessible_pools: list[str] | None,
) -> list[dict]:
    pool_clause = _pool_clause(accessible_pools)
    pool_filter = f"AND pool_id = '{pool_id}'" if pool_id else ""
    sql = f"""
        SELECT outcome, count() AS cnt
        FROM {db}.sessions
        WHERE tenant_id = {{tenant_id:String}}
          AND opened_at >= '{since}'
          AND opened_at <  '{until}'
          {pool_clause}
          {pool_filter}
        GROUP BY outcome
        ORDER BY cnt DESC
    """
    return _run(client, sql, {"tenant_id": tenant_id})


async def fmt_outcome_distribution(
    client:           Any,
    database:         str,
    tenant_id:        str,
    from_dt:          str | None,
    to_dt:            str | None,
    pool_id:          str | None,
    accessible_pools: list[str] | None,
) -> dict:
    """Returns DonutData — session counts by outcome."""
    since = _fmt_dt(from_dt) if from_dt else _default_from()
    until = _fmt_dt(to_dt)   if to_dt   else _default_to()

    empty: dict = {"labels": [], "values": []}
    if accessible_pools is not None and not accessible_pools:
        return empty

    try:
        rows = await asyncio.to_thread(
            _fetch_outcome_distribution,
            client, database, tenant_id, since, until, pool_id, accessible_pools
        )
        return {
            "labels": [r.get("outcome") or "unknown" for r in rows],
            "values": [int(r["cnt"]) for r in rows],
        }
    except Exception as exc:
        logger.warning("fmt_outcome_distribution failed tenant=%s: %s", tenant_id, exc)
        return empty


# ─── /reports/display/pool-status ─────────────────────────────────────────────

async def fmt_pool_status(redis: Any, tenant_id: str) -> dict:
    """Returns TableData — live pool snapshots from Redis."""
    snapshots = await get_pool_snapshots(redis, tenant_id)

    columns = [
        {"key": "pool_id",       "label": "Pool",          "sortable": True},
        {"key": "available",     "label": "Disponíveis",   "sortable": True, "align": "right"},
        {"key": "queue_length",  "label": "Fila",          "sortable": True, "align": "right"},
        {"key": "sla_target_ms", "label": "SLA (ms)",      "sortable": True, "align": "right"},
        {"key": "channel_types", "label": "Canais"},
        {"key": "updated_at",    "label": "Atualizado em"},
    ]

    rows = [
        {
            "pool_id":       s.get("pool_id", ""),
            "available":     s.get("available", 0),
            "queue_length":  s.get("queue_length", 0),
            "sla_target_ms": s.get("sla_target_ms", "-"),
            "channel_types": ", ".join(s.get("channel_types") or []),
            "updated_at":    s.get("updated_at", ""),
        }
        for s in snapshots
    ]

    return {"columns": columns, "rows": rows}


# ─── /reports/display/agent-performance ──────────────────────────────────────

async def fmt_agent_performance(
    client:           Any,
    database:         str,
    tenant_id:        str,
    from_dt:          str | None,
    to_dt:            str | None,
    pool_id:          str | None,
    accessible_pools: list[str] | None,
) -> dict:
    """Returns TableData — per-agent resolution/escalation rates."""
    result = await query_agent_performance_report(
        client           = client,
        database         = database,
        tenant_id        = tenant_id,
        from_dt          = from_dt,
        to_dt            = to_dt,
        pool_id          = pool_id,
        accessible_pools = accessible_pools,
    )
    raw = result.get("data", [])

    columns = [
        {"key": "agent_type_id",   "label": "Agent",        "sortable": True},
        {"key": "pool_id",         "label": "Pool",          "sortable": True},
        {"key": "role",            "label": "Papel"},
        {"key": "total_sessions",  "label": "Sessões",       "sortable": True, "align": "right"},
        {"key": "avg_duration_ms", "label": "Duração Média", "sortable": True, "align": "right"},
        {"key": "resolution_rate", "label": "Resolução %",   "sortable": True, "align": "right"},
        {"key": "escalation_rate", "label": "Escalação %",   "sortable": True, "align": "right"},
    ]

    rows = []
    for r in raw:
        avg_ms = r.get("avg_duration_ms")
        rows.append({
            "agent_type_id":   r.get("agent_type_id", ""),
            "pool_id":         r.get("pool_id", "-"),
            "role":            r.get("role", "-"),
            "total_sessions":  int(r.get("total_sessions", 0)),
            "avg_duration_ms": f"{int(avg_ms):,} ms" if avg_ms else "-",
            "resolution_rate": f"{float(r.get('resolution_rate', 0)) * 100:.1f}%",
            "escalation_rate": f"{float(r.get('escalation_rate', 0)) * 100:.1f}%",
        })

    return {"columns": columns, "rows": rows}


# ─── KPI helpers ──────────────────────────────────────────────────────────────

def _count_sessions_sync(
    client:           Any,
    db:               str,
    tenant_id:        str,
    since:            str,
    until:            str,
    pool_id:          str | None,
    accessible_pools: list[str] | None,
) -> int:
    pool_clause = _pool_clause(accessible_pools)
    pool_filter = f"AND pool_id = '{pool_id}'" if pool_id else ""
    sql = f"""
        SELECT count() AS cnt FROM {db}.sessions
        WHERE tenant_id = {{tenant_id:String}}
          AND opened_at >= '{since}'
          AND opened_at <  '{until}'
          {pool_clause}
          {pool_filter}
    """
    rows = _run(client, sql, {"tenant_id": tenant_id})
    return int(rows[0].get("cnt", 0)) if rows else 0


def _resolution_rate_sync(
    client:           Any,
    db:               str,
    tenant_id:        str,
    since:            str,
    until:            str,
    pool_id:          str | None,
    accessible_pools: list[str] | None,
) -> float | None:
    pool_clause = _pool_clause(accessible_pools)
    pool_filter = f"AND pool_id = '{pool_id}'" if pool_id else ""
    sql = f"""
        SELECT count() AS total, countIf(outcome = 'resolved') AS resolved
        FROM {db}.sessions
        WHERE tenant_id = {{tenant_id:String}}
          AND opened_at >= '{since}'
          AND opened_at <  '{until}'
          {pool_clause}
          {pool_filter}
    """
    rows = _run(client, sql, {"tenant_id": tenant_id})
    if not rows:
        return None
    total   = int(rows[0].get("total", 0))
    resolved = int(rows[0].get("resolved", 0))
    return resolved / total if total > 0 else None


def _avg_score_sync(
    client:           Any,
    db:               str,
    tenant_id:        str,
    since:            str,
    until:            str,
) -> float | None:
    # evaluation_results has no pool_id — scoping not applicable
    sql = f"""
        SELECT avgOrNull(overall_score) AS avg_score
        FROM {db}.evaluation_results FINAL
        WHERE tenant_id = {{tenant_id:String}}
          AND timestamp >= '{since}'
          AND timestamp <  '{until}'
    """
    rows = _run(client, sql, {"tenant_id": tenant_id})
    if not rows or rows[0].get("avg_score") is None:
        return None
    return round(float(rows[0]["avg_score"]), 4)


def _trend_pct(current: float | None, previous: float | None) -> float | None:
    if current is None or previous is None or previous == 0:
        return None
    return round((current - previous) / abs(previous) * 100, 1)


# ─── /reports/display/kpi-sessions ───────────────────────────────────────────

async def fmt_kpi_sessions(
    client:           Any,
    database:         str,
    tenant_id:        str,
    from_dt:          str | None,
    to_dt:            str | None,
    pool_id:          str | None,
    accessible_pools: list[str] | None,
) -> dict:
    """Returns MetricCardData — total sessions in period with trend."""
    since = _fmt_dt(from_dt) if from_dt else _default_from()
    until = _fmt_dt(to_dt)   if to_dt   else _default_to()
    prev_since, prev_until = _prev_period(since, until)

    empty: dict = {"value": 0, "label": "Sessões", "format": "number"}
    if accessible_pools is not None and not accessible_pools:
        return empty

    try:
        current, previous = await asyncio.gather(
            asyncio.to_thread(
                _count_sessions_sync, client, database, tenant_id,
                since, until, pool_id, accessible_pools,
            ),
            asyncio.to_thread(
                _count_sessions_sync, client, database, tenant_id,
                prev_since, prev_until, pool_id, accessible_pools,
            ),
        )
        result: dict = {"value": current, "label": "Sessões", "format": "number"}
        trend = _trend_pct(float(current), float(previous))
        if trend is not None:
            result["trend"] = trend
        return result
    except Exception as exc:
        logger.warning("fmt_kpi_sessions failed tenant=%s: %s", tenant_id, exc)
        return empty


# ─── /reports/display/kpi-resolution ─────────────────────────────────────────

async def fmt_kpi_resolution(
    client:           Any,
    database:         str,
    tenant_id:        str,
    from_dt:          str | None,
    to_dt:            str | None,
    pool_id:          str | None,
    accessible_pools: list[str] | None,
) -> dict:
    """Returns MetricCardData — resolution rate % with trend."""
    since = _fmt_dt(from_dt) if from_dt else _default_from()
    until = _fmt_dt(to_dt)   if to_dt   else _default_to()
    prev_since, prev_until = _prev_period(since, until)

    empty: dict = {"value": 0.0, "label": "Resolução", "format": "percent"}
    if accessible_pools is not None and not accessible_pools:
        return empty

    try:
        current_rate, prev_rate = await asyncio.gather(
            asyncio.to_thread(
                _resolution_rate_sync, client, database, tenant_id,
                since, until, pool_id, accessible_pools,
            ),
            asyncio.to_thread(
                _resolution_rate_sync, client, database, tenant_id,
                prev_since, prev_until, pool_id, accessible_pools,
            ),
        )
        # MetricCardTool 'percent' format multiplies by 100 — return raw ratio (0-1)
        value      = round(current_rate or 0.0, 4)
        prev_value = round(prev_rate    or 0.0, 4) if prev_rate is not None else None
        result: dict = {"value": value, "label": "Resolução", "format": "percent"}
        trend = _trend_pct(value, prev_value)
        if trend is not None:
            result["trend"] = trend
        return result
    except Exception as exc:
        logger.warning("fmt_kpi_resolution failed tenant=%s: %s", tenant_id, exc)
        return empty


# ─── /reports/display/kpi-score ──────────────────────────────────────────────

async def fmt_kpi_score(
    client:           Any,
    database:         str,
    tenant_id:        str,
    from_dt:          str | None,
    to_dt:            str | None,
    pool_id:          str | None,  # no-op — evaluation_results has no pool_id
    accessible_pools: list[str] | None,
) -> dict:
    """Returns MetricCardData — avg evaluation score with trend."""
    since = _fmt_dt(from_dt) if from_dt else _default_from()
    until = _fmt_dt(to_dt)   if to_dt   else _default_to()
    prev_since, prev_until = _prev_period(since, until)

    empty: dict = {"value": 0.0, "label": "Nota Média", "format": "score"}
    if accessible_pools is not None and not accessible_pools:
        return empty

    try:
        current_score, prev_score = await asyncio.gather(
            asyncio.to_thread(
                _avg_score_sync, client, database, tenant_id, since, until,
            ),
            asyncio.to_thread(
                _avg_score_sync, client, database, tenant_id, prev_since, prev_until,
            ),
        )
        value = current_score or 0.0
        result: dict = {"value": value, "label": "Nota Média", "format": "score"}
        trend = _trend_pct(value, prev_score)
        if trend is not None:
            result["trend"] = trend
        return result
    except Exception as exc:
        logger.warning("fmt_kpi_score failed tenant=%s: %s", tenant_id, exc)
        return empty


# ─── Journey formatters ────────────────────────────────────────────────────────

def _fetch_journey_active_count_sync(
    client:          Any,
    db:              str,
    tenant_id:       str,
    since:           str,
    until:           str,
    skill_id:        str | None = None,
    journey_type_id: str | None = None,
    pool_id:         str | None = None,
) -> int:
    """Returns count of journeys whose latest status is 'active' in the period."""
    extra = ""
    if skill_id:
        extra += f" AND skill_id = '{skill_id}'"
    if journey_type_id:
        extra += f" AND journey_type_id = '{journey_type_id}'"
    if pool_id:
        extra += f" AND pool_id = '{pool_id}'"
    sql = f"""
        SELECT countIf(latest_status = 'active') AS active_cnt
        FROM (
            SELECT journey_id, argMax(status, event_time) AS latest_status
            FROM {db}.journey_events FINAL
            WHERE tenant_id = {{tenant_id:String}}
              AND event_time >= '{since}'
              AND event_time <  '{until}'
              {extra}
            GROUP BY journey_id
        )
    """
    rows = _run(client, sql, {"tenant_id": tenant_id})
    return int(rows[0].get("active_cnt", 0)) if rows else 0


async def fmt_journey_active_count(
    client:          Any,
    database:        str,
    tenant_id:       str,
    from_dt:         str | None,
    to_dt:           str | None,
    skill_id:        str | None = None,
    journey_type_id: str | None = None,  # Arc 17: filter by journey_type_id
    pool_id:         str | None = None,  # Arc 17: filter by pool_id
) -> dict:
    """Returns MetricCardData — count of active journeys with trend vs prior period."""
    since = _fmt_dt(from_dt) if from_dt else _default_from()
    until = _fmt_dt(to_dt)   if to_dt   else _default_to()
    prev_since, prev_until = _prev_period(since, until)

    empty: dict = {"value": 0, "label": "Jornadas Ativas", "format": "number"}
    try:
        current, previous = await asyncio.gather(
            asyncio.to_thread(
                _fetch_journey_active_count_sync, client, database, tenant_id, since, until,
                skill_id, journey_type_id, pool_id,
            ),
            asyncio.to_thread(
                _fetch_journey_active_count_sync, client, database, tenant_id, prev_since, prev_until,
                skill_id, journey_type_id, pool_id,
            ),
        )
        result: dict = {"value": current, "label": "Jornadas Ativas", "format": "number"}
        trend = _trend_pct(float(current), float(previous))
        if trend is not None:
            result["trend"] = trend
        return result
    except Exception as exc:
        logger.warning("fmt_journey_active_count failed tenant=%s: %s", tenant_id, exc)
        return empty


# ─── journey-resolution-rate ──────────────────────────────────────────────────

def _fetch_journey_resolution_rate_sync(
    client:          Any,
    db:              str,
    tenant_id:       str,
    since:           str,
    until:           str,
    skill_id:        str | None,
    journey_type_id: str | None = None,
    pool_id:         str | None = None,
) -> list[dict]:
    """
    Resolution rate per skill_id — only journeys that have reached a terminal
    status (completed / failed / cancelled) are counted as denominator so that
    in-flight journeys don't dilute the rate.
    """
    extra = f"AND skill_id = '{skill_id}'" if skill_id else ""
    if journey_type_id:
        extra += f" AND journey_type_id = '{journey_type_id}'"
    if pool_id:
        extra += f" AND pool_id = '{pool_id}'"
    sql = f"""
        SELECT
            skill_id,
            countIf(latest_status = 'completed') AS completed,
            count()                               AS total
        FROM (
            SELECT
                journey_id,
                skill_id,
                argMax(status, event_time) AS latest_status
            FROM {db}.journey_events FINAL
            WHERE tenant_id = {{tenant_id:String}}
              AND event_time >= '{since}'
              AND event_time <  '{until}'
              {extra}
            GROUP BY journey_id, skill_id
            HAVING latest_status IN ('completed', 'failed', 'cancelled')
        )
        GROUP BY skill_id
        ORDER BY skill_id
    """
    return _run(client, sql, {"tenant_id": tenant_id})


async def fmt_journey_resolution_rate(
    client:          Any,
    database:        str,
    tenant_id:       str,
    from_dt:         str | None,
    to_dt:           str | None,
    skill_id:        str | None,
    journey_type_id: str | None = None,  # Arc 17
    pool_id:         str | None = None,  # Arc 17
) -> dict:
    """Returns BarChartData — journey resolution rate % per skill_id."""
    since = _fmt_dt(from_dt) if from_dt else _default_from()
    until = _fmt_dt(to_dt)   if to_dt   else _default_to()

    empty: dict = {
        "x_labels": [],
        "series":   [{"name": "Resolução %", "data": []}],
        "y_label":  "%",
    }
    try:
        rows = await asyncio.to_thread(
            _fetch_journey_resolution_rate_sync,
            client, database, tenant_id, since, until, skill_id, journey_type_id, pool_id,
        )
        x_labels = [r.get("skill_id") or "unknown" for r in rows]
        data = [
            round(int(r.get("completed", 0)) / int(r.get("total", 1)) * 100, 1)
            if int(r.get("total", 0)) > 0 else 0.0
            for r in rows
        ]
        return {"x_labels": x_labels, "series": [{"name": "Resolução %", "data": data}], "y_label": "%"}
    except Exception as exc:
        logger.warning("fmt_journey_resolution_rate failed tenant=%s: %s", tenant_id, exc)
        return empty


# ─── journey-funnel ───────────────────────────────────────────────────────────

def _fetch_journey_funnel_sync(
    client:          Any,
    db:              str,
    tenant_id:       str,
    since:           str,
    until:           str,
    skill_id:        str | None,
    journey_type_id: str | None = None,
    pool_id:         str | None = None,
) -> list[dict]:
    """Returns status distribution (latest status) across all journeys in period."""
    extra = f"AND skill_id = '{skill_id}'" if skill_id else ""
    if journey_type_id:
        extra += f" AND journey_type_id = '{journey_type_id}'"
    if pool_id:
        extra += f" AND pool_id = '{pool_id}'"
    sql = f"""
        SELECT latest_status, count() AS cnt
        FROM (
            SELECT journey_id, argMax(status, event_time) AS latest_status
            FROM {db}.journey_events FINAL
            WHERE tenant_id = {{tenant_id:String}}
              AND event_time >= '{since}'
              AND event_time <  '{until}'
              {extra}
            GROUP BY journey_id
        )
        GROUP BY latest_status
        ORDER BY cnt DESC
    """
    return _run(client, sql, {"tenant_id": tenant_id})


async def fmt_journey_funnel(
    client:          Any,
    database:        str,
    tenant_id:       str,
    from_dt:         str | None,
    to_dt:           str | None,
    skill_id:        str | None,
    journey_type_id: str | None = None,  # Arc 17
    pool_id:         str | None = None,  # Arc 17
) -> dict:
    """Returns DonutData — journey distribution by status (active/suspended/completed/failed/cancelled)."""
    since = _fmt_dt(from_dt) if from_dt else _default_from()
    until = _fmt_dt(to_dt)   if to_dt   else _default_to()

    empty: dict = {"labels": [], "values": []}
    try:
        rows = await asyncio.to_thread(
            _fetch_journey_funnel_sync, client, database, tenant_id, since, until, skill_id,
            journey_type_id, pool_id,
        )
        return {
            "labels": [r.get("latest_status") or "unknown" for r in rows],
            "values": [int(r["cnt"]) for r in rows],
        }
    except Exception as exc:
        logger.warning("fmt_journey_funnel failed tenant=%s: %s", tenant_id, exc)
        return empty


# ─── journey-median-duration ─────────────────────────────────────────────────

def _fetch_journey_median_duration_sync(
    client:          Any,
    db:              str,
    tenant_id:       str,
    since:           str,
    until:           str,
    skill_id:        str | None,
    journey_type_id: str | None = None,
    pool_id:         str | None = None,
) -> list[dict]:
    """
    Median journey duration in ms per skill_id for terminal journeys only.
    Duration = max(event_time) − min(event_time) across events for each journey.
    """
    extra = f"AND skill_id = '{skill_id}'" if skill_id else ""
    if journey_type_id:
        extra += f" AND journey_type_id = '{journey_type_id}'"
    if pool_id:
        extra += f" AND pool_id = '{pool_id}'"
    sql = f"""
        SELECT
            skill_id,
            median(duration_ms) AS median_dur_ms
        FROM (
            SELECT
                journey_id,
                skill_id,
                toUnixTimestamp64Milli(max(event_time))
                    - toUnixTimestamp64Milli(min(event_time)) AS duration_ms,
                argMax(status, event_time) AS latest_status
            FROM {db}.journey_events FINAL
            WHERE tenant_id = {{tenant_id:String}}
              AND event_time >= '{since}'
              AND event_time <  '{until}'
              {extra}
            GROUP BY journey_id, skill_id
            HAVING latest_status IN ('completed', 'failed', 'cancelled')
        )
        GROUP BY skill_id
        ORDER BY median_dur_ms DESC
    """
    return _run(client, sql, {"tenant_id": tenant_id})


async def fmt_journey_median_duration(
    client:          Any,
    database:        str,
    tenant_id:       str,
    from_dt:         str | None,
    to_dt:           str | None,
    skill_id:        str | None,
    journey_type_id: str | None = None,  # Arc 17
    pool_id:         str | None = None,  # Arc 17
) -> dict:
    """Returns BarChartData — journey p50 duration in minutes per skill_id."""
    since = _fmt_dt(from_dt) if from_dt else _default_from()
    until = _fmt_dt(to_dt)   if to_dt   else _default_to()

    empty: dict = {
        "x_labels": [],
        "series":   [{"name": "Duração Mediana (min)", "data": []}],
        "y_label":  "min",
    }
    try:
        rows = await asyncio.to_thread(
            _fetch_journey_median_duration_sync,
            client, database, tenant_id, since, until, skill_id, journey_type_id, pool_id,
        )
        x_labels = [r.get("skill_id") or "unknown" for r in rows]
        data = [
            round(float(r.get("median_dur_ms") or 0.0) / 60_000, 1)
            for r in rows
        ]
        return {
            "x_labels": x_labels,
            "series":   [{"name": "Duração Mediana (min)", "data": data}],
            "y_label":  "min",
        }
    except Exception as exc:
        logger.warning("fmt_journey_median_duration failed tenant=%s: %s", tenant_id, exc)
        return empty


# ─── Arc 12: Agent Business Events display formatters ─────────────────────────

def _fetch_agent_event_timeseries_sync(
    client:    Any,
    db:        str,
    tenant_id: str,
    since:     str,
    until:     str,
    category:  str,
    pool_id:   str | None,
) -> list[dict]:
    """
    Daily time-series for a specific category (exact or prefix match).
    Returns {period, total_value, avg_value, event_count}.
    """
    conditions = [
        "tenant_id = {tenant_id:String}",
        f"emitted_at >= '{since}'",
        f"emitted_at <= '{until}'",
        "startsWith(category, {category:String})",
    ]
    params: dict = {"tenant_id": tenant_id, "category": category}
    if pool_id:
        conditions.append("pool_id = {pool_id:String}")
        params["pool_id"] = pool_id

    where = " AND ".join(conditions)
    result = client.query(f"""
        SELECT
            toDate(emitted_at)   AS period,
            sum(value)           AS total_value,
            avg(value)           AS avg_value,
            count()              AS event_count
        FROM {db}.agent_business_events
        WHERE {where}
        GROUP BY period
        ORDER BY period ASC
    """, parameters=params)
    return _run(client, "", {}) if not result else [
        {
            "period":      str(row[0]),
            "total_value": float(row[1]),
            "avg_value":   round(float(row[2]), 4),
            "event_count": int(row[3]),
        }
        for row in result.result_rows
    ]


def _fetch_agent_event_summary_sync(
    client:   Any,
    db:       str,
    tenant_id: str,
    since:    str,
    until:    str,
    category: str | None,
    pool_id:  str | None,
    group_by: str,
) -> list[dict]:
    """
    Aggregated summary per group_by dimension.
    Returns {group_key, event_count, total_value, avg_value}.
    """
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
    result = client.query(f"""
        SELECT
            {group_by}           AS group_key,
            count()              AS event_count,
            sum(value)           AS total_value,
            avg(value)           AS avg_value
        FROM {db}.agent_business_events
        WHERE {where}
        GROUP BY group_key
        ORDER BY event_count DESC
        LIMIT 20
    """, parameters=params)
    return [
        {
            "group_key":   str(row[0]),
            "event_count": int(row[1]),
            "total_value": float(row[2]),
            "avg_value":   round(float(row[3]), 4),
        }
        for row in result.result_rows
    ]


async def fmt_agent_event_timeseries(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None,
    to_dt:     str | None,
    category:  str | None,
    pool_id:   str | None,
) -> dict:
    """
    Returns LineChartData — daily time-series for the given category.

    category is required (no category = empty chart). Supports prefix matching:
      category=retencao_humano.skill_v2  matches all metric keys under that skill.

    Series:
      'Total' — sum(value) per day
      'Média' — avg(value) per day  (second Y axis hint via y2)

    x_labels: ISO date strings.
    """
    since = _fmt_dt(from_dt) if from_dt else _default_from()
    until = _fmt_dt(to_dt)   if to_dt   else _default_to()

    empty: dict = {
        "x_labels": [],
        "series":   [
            {"name": "Total", "data": []},
            {"name": "Média", "data": []},
        ],
        "y_label": "",
    }
    if not category:
        return empty
    try:
        rows = await asyncio.to_thread(
            _fetch_agent_event_timeseries_sync,
            client, database, tenant_id, since, until, category, pool_id,
        )
        x_labels  = [r["period"]      for r in rows]
        total_data = [r["total_value"] for r in rows]
        avg_data   = [r["avg_value"]   for r in rows]
        return {
            "x_labels": x_labels,
            "series": [
                {"name": "Total", "data": total_data},
                {"name": "Média", "data": avg_data},
            ],
            "y_label": category.split(".")[-1] if category else "",
        }
    except Exception as exc:
        logger.warning("fmt_agent_event_timeseries failed tenant=%s cat=%s: %s", tenant_id, category, exc)
        return empty


async def fmt_agent_event_summary(
    client:    Any,
    database:  str,
    tenant_id: str,
    from_dt:   str | None,
    to_dt:     str | None,
    category:  str | None,
    pool_id:   str | None,
    group_by:  str = "category",
) -> dict:
    """
    Returns BarChartData — aggregated event totals grouped by group_by dimension.

    group_by: category (default) | skill_id | pool_id | agent_type_id
    Bar height = event_count; tooltip carries total_value and avg_value.
    """
    VALID_GROUP_BY = {"category", "skill_id", "pool_id", "agent_type_id"}
    if group_by not in VALID_GROUP_BY:
        group_by = "category"

    since = _fmt_dt(from_dt) if from_dt else _default_from()
    until = _fmt_dt(to_dt)   if to_dt   else _default_to()

    empty: dict = {
        "x_labels": [],
        "series":   [{"name": "Eventos", "data": []}],
        "y_label":  "count",
    }
    try:
        rows = await asyncio.to_thread(
            _fetch_agent_event_summary_sync,
            client, database, tenant_id, since, until, category, pool_id, group_by,
        )
        x_labels = [r["group_key"]   for r in rows]
        counts   = [r["event_count"] for r in rows]
        return {
            "x_labels": x_labels,
            "series":   [{"name": "Eventos", "data": counts}],
            "y_label":  "count",
        }
    except Exception as exc:
        logger.warning("fmt_agent_event_summary failed tenant=%s: %s", tenant_id, exc)
        return empty
