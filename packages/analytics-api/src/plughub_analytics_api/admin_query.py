"""
admin_query.py
ClickHouse query helpers for GET /admin/consolidated.

Returns a cross-tenant (admin) or single-tenant (operator) aggregated snapshot:
  by_channel — sessions grouped by tenant + channel, with outcome breakdown
  by_pool    — sessions grouped by tenant + pool, with sentiment overlay

All queries share a single time window (from_dt → to_dt, default last 24h).
Tenant filtering is applied by the caller via `auth.Principal.effective_tenant()`.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("plughub.analytics.admin")


# ─── defaults ────────────────────────────────────────────────────────────────

def _default_from() -> str:
    return (datetime.utcnow() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")


def _default_to() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _ch_fmt(iso: str | None) -> str:
    if not iso:
        return _default_to()
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return _default_to()


# ─── public API ───────────────────────────────────────────────────────────────

async def query_consolidated(
    client:    Any,
    database:  str,
    tenant_id: str | None,   # None → all tenants (admin)
    from_dt:   str | None = None,
    to_dt:     str | None = None,
) -> dict:
    """
    Returns {scope, period, by_channel, by_pool}.

    tenant_id=None  → admin view (all tenants)
    tenant_id="..."  → operator view (single tenant)
    """
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt)   if to_dt   else _default_to()
    scope = tenant_id or "all_tenants"
    try:
        result = await asyncio.to_thread(
            _fetch_consolidated, client, database, tenant_id, since, until
        )
        return {
            "scope":      scope,
            "period":     {"from": since + "Z", "to": until + "Z"},
            "by_channel": result["by_channel"],
            "by_pool":    result["by_pool"],
        }
    except Exception as exc:
        logger.warning("query_consolidated failed scope=%s: %s", scope, exc)
        return {
            "scope":      scope,
            "period":     {"from": since + "Z", "to": until + "Z"},
            "by_channel": [],
            "by_pool":    [],
            "error":      "data_unavailable",
        }


# ─── synchronous implementation (runs in to_thread) ──────────────────────────

def _tenant_filter(tenant_id: str | None, params: dict, table_alias: str = "") -> str:
    """Returns a SQL fragment and mutates params. Empty string if no filter."""
    prefix = f"{table_alias}." if table_alias else ""
    if tenant_id:
        params["tenant_id"] = tenant_id
        return f"AND {prefix}tenant_id = {{tenant_id:String}}"
    return ""


def _fetch_consolidated(
    client: Any, db: str, tenant_id: str | None, since: str, until: str
) -> dict:
    by_channel = _fetch_by_channel(client, db, tenant_id, since, until)
    by_pool    = _fetch_by_pool(client, db, tenant_id, since, until)
    return {"by_channel": by_channel, "by_pool": by_pool}


def _fetch_by_channel(
    client: Any, db: str, tenant_id: str | None, since: str, until: str
) -> list[dict]:
    """
    Groups sessions by (tenant_id, channel, outcome).
    Collapses into per-(tenant, channel) entries with by_outcome breakdown.
    """
    params: dict = {}
    tf = _tenant_filter(tenant_id, params)
    result = client.query(f"""
        SELECT
            tenant_id,
            channel,
            outcome,
            count()                   AS sessions,
            avgOrNull(handle_time_ms) AS avg_handle_ms
        FROM {db}.sessions
        WHERE opened_at >= '{since}'
          AND opened_at  < '{until}'
          {tf}
        GROUP BY tenant_id, channel, outcome
        ORDER BY tenant_id, channel
    """, parameters=params)

    # Collapse rows into per-(tenant, channel) entries
    index: dict[tuple, dict] = {}
    for row in result.result_rows:
        t_id, ch, outcome, sess, avg_ms = row
        key = (t_id, ch)
        if key not in index:
            index[key] = {
                "tenant_id":    t_id,
                "channel":      ch,
                "sessions":     0,
                "avg_handle_ms": None,
                "by_outcome":   {},
                "_handle_list": [],
            }
        entry = index[key]
        entry["sessions"] += int(sess)
        oc = outcome or "unknown"
        entry["by_outcome"][oc] = entry["by_outcome"].get(oc, 0) + int(sess)
        if avg_ms is not None:
            entry["_handle_list"].append(float(avg_ms))

    out = []
    for entry in index.values():
        hl = entry.pop("_handle_list")
        entry["avg_handle_ms"] = round(sum(hl) / len(hl)) if hl else None
        out.append(entry)
    return out


def _fetch_by_pool(
    client: Any, db: str, tenant_id: str | None, since: str, until: str
) -> list[dict]:
    """
    Groups sessions by (tenant_id, pool_id) and overlays avg_sentiment.
    """
    params: dict = {}
    tf = _tenant_filter(tenant_id, params)

    # ── sessions per pool ─────────────────────────────────────────────────────
    sess_result = client.query(f"""
        SELECT
            tenant_id,
            pool_id,
            count()                   AS sessions,
            avgOrNull(handle_time_ms) AS avg_handle_ms
        FROM {db}.sessions
        WHERE opened_at >= '{since}'
          AND opened_at  < '{until}'
          {tf}
        GROUP BY tenant_id, pool_id
        ORDER BY tenant_id, pool_id
    """, parameters=params)

    pool_map: dict[tuple, dict] = {}
    for row in sess_result.result_rows:
        t_id, pool, sess, avg_ms = row
        key = (t_id, pool)
        pool_map[key] = {
            "tenant_id":             t_id,
            "pool_id":               pool,
            "sessions":              int(sess),
            "avg_handle_ms":         round(float(avg_ms)) if avg_ms is not None else None,
            "avg_sentiment":         None,
            "sentiment_sample_count": 0,
        }

    # ── sentiment overlay ─────────────────────────────────────────────────────
    params2: dict = {}
    tf2 = _tenant_filter(tenant_id, params2)
    sent_result = client.query(f"""
        SELECT
            tenant_id,
            pool_id,
            round(avg(score), 4) AS avg_sentiment,
            count()              AS sample_count
        FROM {db}.sentiment_events
        WHERE timestamp >= '{since}'
          AND timestamp  < '{until}'
          {tf2}
        GROUP BY tenant_id, pool_id
    """, parameters=params2)

    for row in sent_result.result_rows:
        t_id, pool, avg_s, cnt = row
        key = (t_id, pool)
        if key in pool_map:
            pool_map[key]["avg_sentiment"]          = float(avg_s) if avg_s is not None else None
            pool_map[key]["sentiment_sample_count"] = int(cnt)

    return list(pool_map.values())
