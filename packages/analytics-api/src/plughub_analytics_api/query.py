"""
query.py
ClickHouse query helpers for the dashboard and reports endpoints.

All functions accept an AnalyticsStore instance and return plain dicts/lists.
Wrapped with asyncio.to_thread() so they never block the event loop.

Conventions:
  - tenant_id is always a WHERE clause parameter (tenant isolation by design).
  - Timestamps are ISO8601 strings in the return dicts.
  - Missing / NULL values are returned as None.
  - All queries have a safe guard: if ClickHouse is unavailable the function
    returns an empty / zero result rather than propagating the exception.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("plughub.analytics.query")


# ─── helpers ──────────────────────────────────────────────────────────────────

def _ch_now() -> str:
    """Returns a ClickHouse-compatible datetime string for now() UTC."""
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _ch_24h_ago() -> str:
    return (datetime.utcnow() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")


def _run_query(client: Any, sql: str, params: dict | None = None) -> list[dict]:
    """Executes a SELECT and returns rows as a list of dicts."""
    result = client.query(sql, parameters=params or {})
    col_names = result.column_names
    return [dict(zip(col_names, row)) for row in result.result_rows]


def _run_command(client: Any, sql: str, params: dict | None = None) -> Any:
    """Executes a command (CREATE, INSERT, etc.)."""
    return client.command(sql, parameters=params or {})


# ─── dashboard/metrics — last 24h ─────────────────────────────────────────────

async def get_metrics_24h(client: Any, database: str, tenant_id: str) -> dict:
    """
    Aggregated metrics for the last 24 hours for a given tenant.

    Returns:
      sessions:       { total, avg_handle_ms, by_channel, by_outcome, by_close_reason }
      agent_events:   { total_routed, total_done, by_outcome }
      usage:          { by_dimension: {dim: total_quantity} }
      sentiment:      { avg_score, by_category, sample_count }
    """
    db = database
    try:
        return await asyncio.to_thread(
            _fetch_metrics_24h, client, db, tenant_id
        )
    except Exception as exc:
        logger.warning("get_metrics_24h failed tenant=%s: %s", tenant_id, exc)
        return _empty_metrics()


def _fetch_metrics_24h(client: Any, db: str, tenant_id: str) -> dict:
    since = _ch_24h_ago()

    # ── sessions ──────────────────────────────────────────────────────────────
    sess_rows = _run_query(client, f"""
        SELECT
            count()                                  AS total,
            avgOrNull(handle_time_ms)                AS avg_handle_ms,
            channel,
            outcome,
            close_reason
        FROM {db}.sessions
        WHERE tenant_id = {{tenant_id:String}}
          AND opened_at >= '{since}'
        GROUP BY channel, outcome, close_reason
    """, {"tenant_id": tenant_id})

    total_sessions  = sum(r["total"] for r in sess_rows)
    avg_handle_list = [r["avg_handle_ms"] for r in sess_rows if r["avg_handle_ms"] is not None]
    avg_handle_ms   = round(sum(avg_handle_list) / len(avg_handle_list)) if avg_handle_list else None

    by_channel: dict[str, int] = {}
    by_outcome: dict[str, int] = {}
    by_close_reason: dict[str, int] = {}
    for r in sess_rows:
        ch = r["channel"] or "unknown"
        by_channel[ch] = by_channel.get(ch, 0) + r["total"]
        oc = r["outcome"] or "unknown"
        by_outcome[oc] = by_outcome.get(oc, 0) + r["total"]
        cr = r["close_reason"] or "unknown"
        by_close_reason[cr] = by_close_reason.get(cr, 0) + r["total"]

    # ── agent_events ──────────────────────────────────────────────────────────
    ae_rows = _run_query(client, f"""
        SELECT event_type, outcome, count() AS cnt
        FROM {db}.agent_events
        WHERE tenant_id = {{tenant_id:String}}
          AND timestamp >= '{since}'
        GROUP BY event_type, outcome
    """, {"tenant_id": tenant_id})

    total_routed = sum(r["cnt"] for r in ae_rows if r["event_type"] == "routed")
    total_done   = sum(r["cnt"] for r in ae_rows if r["event_type"] == "agent_done")
    by_ae_outcome: dict[str, int] = {}
    for r in ae_rows:
        if r["event_type"] == "agent_done":
            oc = r["outcome"] or "unknown"
            by_ae_outcome[oc] = by_ae_outcome.get(oc, 0) + r["cnt"]

    # ── usage ─────────────────────────────────────────────────────────────────
    usage_rows = _run_query(client, f"""
        SELECT dimension, sum(quantity) AS total_qty
        FROM {db}.usage_events
        WHERE tenant_id = {{tenant_id:String}}
          AND timestamp >= '{since}'
        GROUP BY dimension
    """, {"tenant_id": tenant_id})

    by_dimension = {r["dimension"]: int(r["total_qty"]) for r in usage_rows}

    # ── sentiment ─────────────────────────────────────────────────────────────
    sent_rows = _run_query(client, f"""
        SELECT category, count() AS cnt, avg(score) AS avg_sc
        FROM {db}.sentiment_events
        WHERE tenant_id = {{tenant_id:String}}
          AND timestamp >= '{since}'
        GROUP BY category
    """, {"tenant_id": tenant_id})

    sent_total  = sum(r["cnt"] for r in sent_rows)
    sent_scores = [r["avg_sc"] * r["cnt"] for r in sent_rows if r["avg_sc"] is not None]
    sent_avg    = round(sum(sent_scores) / sent_total, 4) if sent_total else None
    by_category = {r["category"]: int(r["cnt"]) for r in sent_rows}

    return {
        "period":    "last_24h",
        "since":     since + "Z",
        "tenant_id": tenant_id,
        "sessions": {
            "total":          total_sessions,
            "avg_handle_ms":  avg_handle_ms,
            "by_channel":     by_channel,
            "by_outcome":     by_outcome,
            "by_close_reason": by_close_reason,
        },
        "agent_events": {
            "total_routed": total_routed,
            "total_done":   total_done,
            "by_outcome":   by_ae_outcome,
        },
        "usage": {
            "by_dimension": by_dimension,
        },
        "sentiment": {
            "avg_score":    sent_avg,
            "sample_count": sent_total,
            "by_category":  by_category,
        },
    }


def _empty_metrics() -> dict:
    return {
        "period": "last_24h",
        "error":  "data_unavailable",
        "sessions":     {"total": 0, "avg_handle_ms": None, "by_channel": {}, "by_outcome": {}, "by_close_reason": {}},
        "agent_events": {"total_routed": 0, "total_done": 0, "by_outcome": {}},
        "usage":        {"by_dimension": {}},
        "sentiment":    {"avg_score": None, "sample_count": 0, "by_category": {}},
    }


# ─── Redis helpers ────────────────────────────────────────────────────────────

async def get_pool_snapshots(redis: Any, tenant_id: str) -> list[dict]:
    """
    Scans Redis for all pool snapshots for a tenant.
    Key pattern: {tenant_id}:pool:*:snapshot  TTL: 120s
    Returns only snapshots younger than 120s (TTL not expired).
    """
    import json as _json
    pattern = f"{tenant_id}:pool:*:snapshot"
    try:
        keys: list[str] = []
        cursor = 0
        while True:
            cursor, batch = await redis.scan(cursor, match=pattern, count=100)
            keys.extend(batch)
            if cursor == 0:
                break

        if not keys:
            return []

        values = await redis.mget(*keys)
        result: list[dict] = []
        for raw in values:
            if raw:
                try:
                    result.append(_json.loads(raw))
                except Exception:
                    pass
        return result
    except Exception as exc:
        logger.warning("get_pool_snapshots failed tenant=%s: %s", tenant_id, exc)
        return []


async def get_sentiment_live(redis: Any, tenant_id: str) -> list[dict]:
    """
    Reads all sentiment_live hashes for a tenant.
    Key pattern: {tenant_id}:pool:*:sentiment_live  TTL: 300s
    Returns list of {pool_id, avg_score, count, distribution, updated_at}.
    """
    pattern = f"{tenant_id}:pool:*:sentiment_live"
    try:
        keys: list[str] = []
        cursor = 0
        while True:
            cursor, batch = await redis.scan(cursor, match=pattern, count=100)
            keys.extend(batch)
            if cursor == 0:
                break

        result: list[dict] = []
        for key in keys:
            # Extract pool_id from key: {tenant_id}:pool:{pool_id}:sentiment_live
            parts = key.split(":")
            pool_id = parts[2] if len(parts) >= 4 else "unknown"

            raw = await redis.hgetall(key)
            if not raw:
                continue

            result.append({
                "pool_id":    pool_id,
                "tenant_id":  tenant_id,
                "avg_score":  float(raw.get("avg_score", 0.0)),
                "count":      int(raw.get("count", 0)),
                "distribution": {
                    "satisfied":  int(raw.get("satisfied", 0)),
                    "neutral":    int(raw.get("neutral", 0)),
                    "frustrated": int(raw.get("frustrated", 0)),
                    "angry":      int(raw.get("angry", 0)),
                },
                "last_session_id": raw.get("last_session_id"),
                "updated_at":      raw.get("updated_at"),
            })
        return result
    except Exception as exc:
        logger.warning("get_sentiment_live failed tenant=%s: %s", tenant_id, exc)
        return []
