"""
timeseries_query.py
ClickHouse query helpers for the /reports/timeseries/* endpoints.

All three helpers follow the same contract:
  - Bucket rows with toStartOfInterval(timestamp, toIntervalMinute(interval))
  - Optional breakdown_by column (pool_id | channel) produces a breakdown list per bucket
  - Optional pool_id filter; accessible_pools scoping from JWT
  - Return: {"buckets": list[TimeseriesBucket], "meta": {...}}

TimeseriesBucket shape:
  { "bucket": "ISO8601", "value": float, "breakdown": [{"label": str, "value": float}] }
"""
from __future__ import annotations

import asyncio
import csv
import io
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("plughub.analytics.timeseries")

# ─── valid breakdown columns (whitelist — never interpolate user input directly) ─
_VALID_BREAKDOWN = {"pool_id", "channel"}

# ─── defaults ─────────────────────────────────────────────────────────────────

def _default_from() -> str:
    return (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")


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


def _clamp_interval(minutes: int) -> int:
    """Clamp interval to sane values: 1 min … 1440 min (1 day)."""
    return max(1, min(minutes, 1440))


def _pool_scope_clause(accessible_pools: list[str] | None) -> str:
    """Return an AND clause string (or empty string) for pool scoping."""
    if accessible_pools is None:
        return ""
    if not accessible_pools:
        return "AND 1=0"  # no access — returns zero rows
    pool_list = ", ".join(f"'{p}'" for p in accessible_pools)
    return f"AND pool_id IN ({pool_list})"


def _rows_to_buckets(
    rows: list[dict],
    breakdown_by: str | None,
    value_col: str = "value",
) -> list[dict]:
    """
    Aggregates raw ClickHouse rows (bucket, [breakdown_label,] value) into
    the canonical TimeseriesBucket list.

    Without breakdown_by the rows already have one row per bucket.
    With breakdown_by, multiple rows share the same bucket — we merge them.
    """
    if not breakdown_by:
        result = []
        for row in rows:
            bucket_val = row.get("bucket")
            if isinstance(bucket_val, datetime):
                bucket_val = bucket_val.isoformat()
            result.append({
                "bucket": bucket_val,
                "value":  round(float(row.get(value_col, 0) or 0), 4),
                "breakdown": [],
            })
        return result

    # Group by bucket, collect breakdown entries
    from collections import OrderedDict
    buckets: dict[str, dict] = OrderedDict()
    for row in rows:
        bucket_val = row.get("bucket")
        if isinstance(bucket_val, datetime):
            bucket_val = bucket_val.isoformat()
        label = str(row.get("breakdown_label") or "unknown")
        value = round(float(row.get(value_col, 0) or 0), 4)

        if bucket_val not in buckets:
            buckets[bucket_val] = {"bucket": bucket_val, "value": 0.0, "breakdown": []}
        buckets[bucket_val]["breakdown"].append({"label": label, "value": value})

    # Recompute bucket total as sum of breakdown values
    for entry in buckets.values():
        entry["value"] = round(sum(b["value"] for b in entry["breakdown"]), 4)

    return list(buckets.values())


def _to_csv_timeseries(buckets: list[dict]) -> str:
    """Flatten timeseries buckets to CSV (one row per bucket, breakdown as JSON string)."""
    import json
    if not buckets:
        return ""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["bucket", "value", "breakdown"])
    for b in buckets:
        writer.writerow([b["bucket"], b["value"], json.dumps(b["breakdown"])])
    return buf.getvalue()


# ─── volume ───────────────────────────────────────────────────────────────────

def _fetch_volume(
    client:     Any,
    database:   str,
    tenant_id:  str,
    since:      str,
    until:      str,
    interval:   int,
    breakdown_by: str | None,
    pool_id:    str | None,
    accessible_pools: list[str] | None,
) -> list[dict]:
    pool_clause = _pool_scope_clause(accessible_pools)
    pool_filter = f"AND pool_id = '{pool_id}'" if pool_id else ""

    if breakdown_by:
        sql = f"""
            SELECT
                toStartOfInterval(opened_at, toIntervalMinute({interval})) AS bucket,
                {breakdown_by} AS breakdown_label,
                count() AS value
            FROM {database}.sessions
            WHERE tenant_id = {{tenant_id:String}}
              AND opened_at >= {{since:String}}
              AND opened_at <  {{until:String}}
              {pool_clause}
              {pool_filter}
            GROUP BY bucket, breakdown_label
            ORDER BY bucket ASC, breakdown_label ASC
        """
    else:
        sql = f"""
            SELECT
                toStartOfInterval(opened_at, toIntervalMinute({interval})) AS bucket,
                count() AS value
            FROM {database}.sessions
            WHERE tenant_id = {{tenant_id:String}}
              AND opened_at >= {{since:String}}
              AND opened_at <  {{until:String}}
              {pool_clause}
              {pool_filter}
            GROUP BY bucket
            ORDER BY bucket ASC
        """

    result = client.query(sql, parameters={"tenant_id": tenant_id, "since": since, "until": until})
    rows = []
    for row in result.result_rows:
        d = dict(zip(result.column_names, row))
        rows.append(d)
    return rows


async def query_volume_timeseries(
    client:     Any,
    database:   str,
    tenant_id:  str,
    from_dt:    str | None = None,
    to_dt:      str | None = None,
    *,
    interval:         int              = 60,
    breakdown_by:     str | None       = None,
    pool_id:          str | None       = None,
    accessible_pools: list[str] | None = None,
) -> dict:
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt)   if to_dt   else _default_to()
    iv    = _clamp_interval(interval)
    bd    = breakdown_by if breakdown_by in _VALID_BREAKDOWN else None

    if accessible_pools is not None and not accessible_pools:
        return {"buckets": [], "meta": _meta(iv, since, until, 0)}

    try:
        rows = await asyncio.to_thread(
            _fetch_volume, client, database, tenant_id, since, until, iv, bd, pool_id, accessible_pools
        )
        buckets = _rows_to_buckets(rows, bd)
        return {
            "buckets": buckets,
            "meta":    _meta(iv, since, until, sum(b["value"] for b in buckets)),
        }
    except Exception as exc:
        logger.warning("query_volume_timeseries failed tenant=%s: %s", tenant_id, exc)
        return {"buckets": [], "meta": _meta(iv, since, until, 0), "error": "data_unavailable"}


# ─── handle time ──────────────────────────────────────────────────────────────

def _fetch_handle_time(
    client:     Any,
    database:   str,
    tenant_id:  str,
    since:      str,
    until:      str,
    interval:   int,
    breakdown_by: str | None,
    pool_id:    str | None,
    accessible_pools: list[str] | None,
) -> list[dict]:
    pool_clause = _pool_scope_clause(accessible_pools)
    pool_filter = f"AND pool_id = '{pool_id}'" if pool_id else ""

    if breakdown_by:
        sql = f"""
            SELECT
                toStartOfInterval(opened_at, toIntervalMinute({interval})) AS bucket,
                {breakdown_by} AS breakdown_label,
                avg(duration_ms) AS value
            FROM {database}.sessions
            WHERE tenant_id = {{tenant_id:String}}
              AND opened_at >= {{since:String}}
              AND opened_at <  {{until:String}}
              AND duration_ms > 0
              {pool_clause}
              {pool_filter}
            GROUP BY bucket, breakdown_label
            ORDER BY bucket ASC, breakdown_label ASC
        """
    else:
        sql = f"""
            SELECT
                toStartOfInterval(opened_at, toIntervalMinute({interval})) AS bucket,
                avg(duration_ms) AS value
            FROM {database}.sessions
            WHERE tenant_id = {{tenant_id:String}}
              AND opened_at >= {{since:String}}
              AND opened_at <  {{until:String}}
              AND duration_ms > 0
              {pool_clause}
              {pool_filter}
            GROUP BY bucket
            ORDER BY bucket ASC
        """

    result = client.query(sql, parameters={"tenant_id": tenant_id, "since": since, "until": until})
    rows = []
    for row in result.result_rows:
        rows.append(dict(zip(result.column_names, row)))
    return rows


async def query_handle_time_timeseries(
    client:     Any,
    database:   str,
    tenant_id:  str,
    from_dt:    str | None = None,
    to_dt:      str | None = None,
    *,
    interval:         int              = 60,
    breakdown_by:     str | None       = None,
    pool_id:          str | None       = None,
    accessible_pools: list[str] | None = None,
) -> dict:
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt)   if to_dt   else _default_to()
    iv    = _clamp_interval(interval)
    bd    = breakdown_by if breakdown_by in _VALID_BREAKDOWN else None

    if accessible_pools is not None and not accessible_pools:
        return {"buckets": [], "meta": _meta(iv, since, until, 0)}

    try:
        rows = await asyncio.to_thread(
            _fetch_handle_time, client, database, tenant_id, since, until, iv, bd, pool_id, accessible_pools
        )
        buckets = _rows_to_buckets(rows, bd)
        # meta.total = overall avg across all buckets (weighted would be better but avg is fine here)
        total_val = (
            sum(b["value"] for b in buckets) / len(buckets) if buckets else 0.0
        )
        return {
            "buckets": buckets,
            "meta":    _meta(iv, since, until, round(total_val, 2)),
        }
    except Exception as exc:
        logger.warning("query_handle_time_timeseries failed tenant=%s: %s", tenant_id, exc)
        return {"buckets": [], "meta": _meta(iv, since, until, 0), "error": "data_unavailable"}


# ─── score ────────────────────────────────────────────────────────────────────

def _fetch_score(
    client:      Any,
    database:    str,
    tenant_id:   str,
    since:       str,
    until:       str,
    interval:    int,
    breakdown_by: str | None,
    campaign_id: str | None,
    accessible_pools: list[str] | None,
) -> list[dict]:
    # evaluation_results does not have pool_id directly; scoping is via campaign filter only
    campaign_filter = f"AND campaign_id = '{campaign_id}'" if campaign_id else ""
    # breakdown for score only supports campaign_id or form_id (not pool_id / channel)
    bd_col = breakdown_by if breakdown_by in {"campaign_id", "form_id"} else None

    if bd_col:
        sql = f"""
            SELECT
                toStartOfInterval(timestamp, toIntervalMinute({interval})) AS bucket,
                {bd_col} AS breakdown_label,
                avg(overall_score) AS value
            FROM {database}.evaluation_results FINAL
            WHERE tenant_id = {{tenant_id:String}}
              AND timestamp >= {{since:String}}
              AND timestamp <  {{until:String}}
              {campaign_filter}
            GROUP BY bucket, breakdown_label
            ORDER BY bucket ASC, breakdown_label ASC
        """
    else:
        sql = f"""
            SELECT
                toStartOfInterval(timestamp, toIntervalMinute({interval})) AS bucket,
                avg(overall_score) AS value
            FROM {database}.evaluation_results FINAL
            WHERE tenant_id = {{tenant_id:String}}
              AND timestamp >= {{since:String}}
              AND timestamp <  {{until:String}}
              {campaign_filter}
            GROUP BY bucket
            ORDER BY bucket ASC
        """

    result = client.query(sql, parameters={"tenant_id": tenant_id, "since": since, "until": until})
    rows = []
    for row in result.result_rows:
        rows.append(dict(zip(result.column_names, row)))
    return rows


async def query_score_timeseries(
    client:     Any,
    database:   str,
    tenant_id:  str,
    from_dt:    str | None = None,
    to_dt:      str | None = None,
    *,
    interval:         int              = 60,
    breakdown_by:     str | None       = None,
    campaign_id:      str | None       = None,
    accessible_pools: list[str] | None = None,
) -> dict:
    since = _ch_fmt(from_dt) if from_dt else _default_from()
    until = _ch_fmt(to_dt)   if to_dt   else _default_to()
    iv    = _clamp_interval(interval)
    # score scoping: no pool filter (evaluation_results has no pool_id)
    # accessible_pools is respected by not returning data if empty
    if accessible_pools is not None and not accessible_pools:
        return {"buckets": [], "meta": _meta(iv, since, until, 0)}

    try:
        rows = await asyncio.to_thread(
            _fetch_score, client, database, tenant_id, since, until, iv, breakdown_by, campaign_id, None
        )
        buckets = _rows_to_buckets(rows, breakdown_by if breakdown_by in {"campaign_id", "form_id"} else None)
        total_val = (
            sum(b["value"] for b in buckets) / len(buckets) if buckets else 0.0
        )
        return {
            "buckets": buckets,
            "meta":    _meta(iv, since, until, round(total_val, 4)),
        }
    except Exception as exc:
        logger.warning("query_score_timeseries failed tenant=%s: %s", tenant_id, exc)
        return {"buckets": [], "meta": _meta(iv, since, until, 0), "error": "data_unavailable"}


# ─── shared meta builder ──────────────────────────────────────────────────────

def _meta(interval_minutes: int, from_dt: str, to_dt: str, total: float) -> dict:
    return {
        "interval_minutes": interval_minutes,
        "from_dt":          from_dt,
        "to_dt":            to_dt,
        "total":            total,
    }


# ─── CSV export helper (public) ───────────────────────────────────────────────

def timeseries_to_csv(buckets: list[dict]) -> str:
    return _to_csv_timeseries(buckets)
