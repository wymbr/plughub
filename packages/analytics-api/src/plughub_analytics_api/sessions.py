"""
sessions.py
FastAPI router for session drill-down endpoints (Phase 2 — read-only).

Routes:
  GET /sessions/active?tenant_id=xxx&pool_id=xxx&limit=N
      List of active sessions for a pool, ordered by worst sentiment first.
      Queries ClickHouse for sessions opened in the last 24h with closed_at IS NULL,
      then overlays latest sentiment score from Redis.

  GET /sessions/customer/{customer_id}?tenant_id=xxx&limit=N
      Contact history for a customer (closed sessions, most recent first).
      Returns session_id, channel, pool_id, opened_at, closed_at, duration_ms,
      outcome, close_reason for each past contact.

  GET /sessions/{session_id}/stream?tenant_id=xxx
      SSE stream of the Redis session stream (session:{id}:stream).
      First event type "history" delivers all existing entries.
      Subsequent events type "entry" deliver new entries as they arrive.
      Sends ':keepalive' comment every 15s to prevent proxy timeouts.
      Read-only — no participant is registered in the session.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

logger = logging.getLogger("plughub.analytics.sessions")

router = APIRouter(prefix="/sessions")

_STREAM_BLOCK_MS   = 2_000   # XREAD blocking timeout
_SSE_KEEPALIVE_S   = 15      # send comment to keep connection alive
_ACTIVE_WINDOW_H   = 24      # look back N hours for "active" sessions
_DEFAULT_LIMIT     = 50
_MAX_LIMIT         = 200


# ─── GET /sessions/active ─────────────────────────────────────────────────────

@router.get("/active")
async def list_active_sessions(
    request:   Request,
    tenant_id: str = Query(..., description="Tenant identifier"),
    pool_id:   str = Query(..., description="Pool to list sessions for"),
    limit:     int = Query(_DEFAULT_LIMIT, ge=1, le=_MAX_LIMIT),
) -> JSONResponse:
    """
    Active sessions for a pool, worst sentiment first.

    A session is considered 'active' when it appears in ClickHouse with
    closed_at IS NULL and was opened within the last 24 hours.

    Each entry includes:
      session_id, channel, opened_at, handle_time_ms (running),
      latest_score (from Redis), latest_category
    """
    store = request.app.state.store
    redis = request.app.state.redis
    try:
        sessions = await asyncio.to_thread(
            _fetch_active_sessions,
            store.new_client(), store._database, tenant_id, pool_id, limit,
        )
        # Overlay sentiment scores from Redis (pipeline for efficiency)
        if sessions:
            sessions = await _overlay_sentiment(redis, sessions)
        return JSONResponse(content=sessions)
    except Exception as exc:
        logger.warning("list_active_sessions failed tenant=%s pool=%s: %s", tenant_id, pool_id, exc)
        return JSONResponse(content=[], status_code=200)


def _fetch_active_sessions(
    client: Any, db: str, tenant_id: str, pool_id: str, limit: int,
) -> list[dict]:
    from datetime import timedelta
    since = (datetime.utcnow() - timedelta(hours=_ACTIVE_WINDOW_H)).strftime("%Y-%m-%d %H:%M:%S")
    result = client.query(f"""
        SELECT
            session_id,
            channel,
            opened_at,
            wait_time_ms
        FROM {db}.sessions
        WHERE tenant_id = {{tenant_id:String}}
          AND pool_id   = {{pool_id:String}}
          AND closed_at IS NULL
          AND opened_at >= '{since}'
        ORDER BY opened_at ASC
        LIMIT {limit}
    """, parameters={"tenant_id": tenant_id, "pool_id": pool_id})

    now_ms = int(time.time() * 1000)
    rows   = []
    for r in result.result_rows:
        session_id, channel, opened_at, wait_time_ms = r
        # opened_at comes back as a datetime object from clickhouse-connect
        if isinstance(opened_at, datetime):
            opened_ts_ms = int(opened_at.replace(tzinfo=timezone.utc).timestamp() * 1000)
        else:
            opened_ts_ms = 0
        handle_time_ms = now_ms - opened_ts_ms if opened_ts_ms else None
        rows.append({
            "session_id":    session_id,
            "channel":       channel,
            "opened_at":     opened_at.isoformat() if isinstance(opened_at, datetime) else str(opened_at),
            "handle_time_ms": handle_time_ms,
            "wait_time_ms":  wait_time_ms,
            "latest_score":  None,   # filled by _overlay_sentiment
            "latest_category": None,
        })
    return rows


async def _overlay_sentiment(redis: Any, sessions: list[dict]) -> list[dict]:
    """
    Fetches the latest sentiment score for each session from Redis.
    Key: session:{id}:sentiment  (list of {score, timestamp} JSON objects)
    Gets only the last element via LRANGE ... -1 -1 (pipeline).
    """
    keys = [f"session:{s['session_id']}:sentiment" for s in sessions]
    try:
        pipe = redis.pipeline()
        for key in keys:
            pipe.lrange(key, -1, -1)
        results = await pipe.execute()

        for session, raw_list in zip(sessions, results):
            if raw_list:
                try:
                    entry = json.loads(raw_list[0])
                    score = float(entry.get("score", 0.0))
                    session["latest_score"]    = round(score, 4)
                    session["latest_category"] = _classify(score)
                except Exception:
                    pass
    except Exception as exc:
        logger.warning("_overlay_sentiment failed: %s", exc)

    # Sort: worst score first (None at end)
    sessions.sort(key=lambda s: (s["latest_score"] is None, s["latest_score"] or 0))
    return sessions


def _classify(score: float) -> str:
    if score >=  0.3: return "satisfied"
    if score >= -0.3: return "neutral"
    if score >= -0.6: return "frustrated"
    return "angry"


# ─── GET /sessions/customer/{customer_id} ────────────────────────────────────

_DEFAULT_HISTORY_LIMIT = 20
_MAX_HISTORY_LIMIT     = 100


@router.get("/customer/{customer_id}")
async def customer_history(
    customer_id: str,
    request:     Request,
    tenant_id:   str = Query(..., description="Tenant identifier"),
    limit:       int = Query(_DEFAULT_HISTORY_LIMIT, ge=1, le=_MAX_HISTORY_LIMIT),
) -> JSONResponse:
    """
    Contact history for a customer — last N closed sessions, most recent first.

    Each entry includes:
      session_id, channel, pool_id, opened_at, closed_at,
      duration_ms, outcome, close_reason
    """
    store = request.app.state.store
    try:
        rows = await asyncio.to_thread(
            _fetch_customer_history,
            store.new_client(), store._database, tenant_id, customer_id, limit,
        )
        return JSONResponse(content=rows)
    except Exception as exc:
        logger.warning(
            "customer_history failed tenant=%s customer=%s: %s",
            tenant_id, customer_id, exc,
        )
        return JSONResponse(content=[], status_code=200)


def _fetch_customer_history(
    client: Any, db: str, tenant_id: str, customer_id: str, limit: int,
) -> list[dict]:
    """
    Queries ClickHouse for closed sessions belonging to the given customer,
    ordered by opened_at DESC.  Uses FINAL to force ReplacingMergeTree dedup
    so we don't return stale open-row duplicates that haven't merged yet.
    """
    result = client.query(f"""
        SELECT
            session_id,
            channel,
            pool_id,
            opened_at,
            closed_at,
            handle_time_ms,
            outcome,
            close_reason
        FROM {db}.sessions FINAL
        WHERE tenant_id   = {{tenant_id:String}}
          AND customer_id = {{customer_id:String}}
          AND closed_at IS NOT NULL
        ORDER BY opened_at DESC
        LIMIT {limit}
    """, parameters={"tenant_id": tenant_id, "customer_id": customer_id})

    rows = []
    for r in result.result_rows:
        session_id, channel, pool_id, opened_at, closed_at, handle_time_ms, outcome, close_reason = r

        def _dt(val: Any) -> str | None:
            if val is None:
                return None
            return val.isoformat() if isinstance(val, datetime) else str(val)

        # Derive duration from handle_time_ms when available, fall back to timestamps.
        if handle_time_ms is not None:
            duration_ms: int | None = int(handle_time_ms)
        elif opened_at and closed_at:
            try:
                o = opened_at if isinstance(opened_at, datetime) else datetime.fromisoformat(str(opened_at))
                c = closed_at if isinstance(closed_at, datetime) else datetime.fromisoformat(str(closed_at))
                duration_ms = int((c - o).total_seconds() * 1000)
            except Exception:
                duration_ms = None
        else:
            duration_ms = None

        rows.append({
            "session_id":   session_id,
            "channel":      channel,
            "pool_id":      pool_id,
            "opened_at":    _dt(opened_at),
            "closed_at":    _dt(closed_at),
            "duration_ms":  duration_ms,
            "outcome":      outcome,
            "close_reason": close_reason,
        })
    return rows


# ─── GET /sessions/{session_id}/stream ───────────────────────────────────────

@router.get("/{session_id}/stream")
async def session_stream(
    session_id: str,
    request:    Request,
    tenant_id:  str = Query(..., description="Tenant identifier"),
) -> StreamingResponse:
    """
    SSE stream of Redis session stream (read-only XREAD).

    First sends a 'history' event with all existing entries,
    then sends 'entry' events as new entries arrive.
    Sends ':keepalive' comment every 15s to prevent proxy timeouts.
    """
    redis      = request.app.state.redis
    stream_key = f"session:{session_id}:stream"

    async def event_generator():
        yield f"retry: 3000\n\n"

        # ── history ───────────────────────────────────────────────────────────
        try:
            raw_entries = await redis.xrange(stream_key, "-", "+")
            history     = [_parse_entry(e_id, e_data) for e_id, e_data in raw_entries]
            yield f"event: history\ndata: {json.dumps(history)}\nid: 0\n\n"
            cursor = raw_entries[-1][0] if raw_entries else "0"
        except Exception as exc:
            logger.warning("session_stream history failed id=%s: %s", session_id, exc)
            yield f"event: history\ndata: []\nid: 0\n\n"
            cursor = "0"

        # ── live tail ─────────────────────────────────────────────────────────
        last_keepalive = time.time()
        try:
            while True:
                if await request.is_disconnected():
                    break

                # keepalive comment
                if time.time() - last_keepalive >= _SSE_KEEPALIVE_S:
                    yield ": keepalive\n\n"
                    last_keepalive = time.time()

                try:
                    result = await redis.xread(
                        {stream_key: cursor},
                        block=_STREAM_BLOCK_MS,
                        count=20,
                    )
                except Exception as exc:
                    logger.warning("xread failed id=%s: %s", session_id, exc)
                    await asyncio.sleep(1)
                    continue

                if not result:
                    continue

                for _stream, entries in result:
                    for e_id, e_data in entries:
                        parsed = _parse_entry(e_id, e_data)
                        yield f"event: entry\ndata: {json.dumps(parsed)}\nid: {e_id}\n\n"
                        cursor = e_id

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.warning("session_stream error id=%s: %s", session_id, exc)
            yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _parse_entry(entry_id: str | bytes, data: dict) -> dict:
    """Converts a raw Redis stream entry to a clean dict for the frontend."""
    if isinstance(entry_id, bytes):
        entry_id = entry_id.decode()

    # Decode bytes values if needed
    clean: dict[str, Any] = {}
    for k, v in data.items():
        if isinstance(k, bytes): k = k.decode()
        if isinstance(v, bytes): v = v.decode()
        clean[k] = v

    return {
        "entry_id":   entry_id,
        "type":       clean.get("type", "unknown"),
        "timestamp":  clean.get("timestamp"),
        "author_id":  clean.get("author_id"),
        "author_role": clean.get("author_role"),
        "visibility": clean.get("visibility", "all"),
        "content":    _safe_json(clean.get("content")),
        "payload":    _safe_json(clean.get("payload")),
    }


def _safe_json(raw: str | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return raw
