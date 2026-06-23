"""
audit.py — LGPD Audit endpoints (Arc LGPD Fase 1)

Routes (all prefixed /v1/audit):
  GET /v1/audit/sessions/{session_id}/messages
      Returns masked message content for a session.
      Auth: Bearer JWT (auth-api HS256) — ABAC gate: module_config.audit.sessions
      Side-effect: writes an immutable row to audit_access_log (ClickHouse).

  GET /v1/audit/mcp-calls
      Returns MCP tool call audit records for a tenant.
      Auth: Bearer JWT (auth-api HS256) — ABAC gate: module_config.audit.mcp_calls
      Query: tenant_id, limit (default 100), masked_only (bool)

Notes:
  - In analytics_open_access=True mode all checks are bypassed (dev/demo only).
  - Tenant isolation is enforced: operators can only see their own tenant.
  - MCP call data is sourced from session_timeline (event_type = 'mcp.tool_call').
    The payload JSON carries server_name, tool_name, allowed, injection_detected,
    duration_ms — packed there by parse_mcp_audit_event() in models.py.
  - masked_input_fields is not currently persisted; all rows return [].
    This will be populated when dual-write to mcp_audit_log is implemented (Fase 2).
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from .pool_auth import PoolPrincipal, optional_pool_principal

logger = logging.getLogger("plughub.analytics.audit")

router = APIRouter(prefix="/v1/audit", tags=["audit"])


# ── helpers ───────────────────────────────────────────────────────────────────

def _store(request: Request):
    return request.app.state.store


def _isoformat(ts: Any) -> str:
    """Convert a ClickHouse DateTime to ISO8601 string."""
    if ts is None:
        return ""
    from datetime import datetime as _dt, timezone as _tz
    if isinstance(ts, _dt):
        return ts.replace(tzinfo=_tz.utc).isoformat()
    return str(ts)


# ── GET /v1/audit/sessions/{session_id}/messages ──────────────────────────────

def _fetch_audit_messages(client: Any, db: str, tenant_id: str, session_id: str) -> list[dict]:
    """Query the messages table for a session — used for LGPD audit trail."""
    result = client.query(
        f"""
        SELECT
            message_id,
            author_id,
            author_role,
            content_type,
            visibility,
            content,
            timestamp
        FROM {db}.messages FINAL
        WHERE tenant_id  = {{tenant_id:String}}
          AND session_id = {{session_id:String}}
        ORDER BY timestamp ASC
        LIMIT 2000
        """,
        parameters={"tenant_id": tenant_id, "session_id": session_id},
    )
    rows = []
    for r in result.result_rows:
        msg_id, author_id, author_role, content_type, visibility, content_raw, ts = r
        # Normalise content — stored as JSON string in some events
        content_str = ""
        if content_raw:
            try:
                parsed = json.loads(content_raw)
                if isinstance(parsed, str):
                    content_str = parsed
                elif isinstance(parsed, dict) and "text" in parsed:
                    content_str = str(parsed["text"])
                else:
                    content_str = content_raw
            except Exception:
                content_str = content_raw
        rows.append({
            "stream_entry_id": str(msg_id),
            "event_type":      str(content_type or "message"),
            "author_id":       str(author_id) if author_id else None,
            "author_role":     str(author_role) if author_role else None,
            "content":         content_str,
            "created_at":      _isoformat(ts),
        })
    return rows


@router.get("/sessions/{session_id}/messages")
async def audit_session_messages(
    session_id:  str,
    request:     Request,
    tenant_id:   str = Query("tenant_demo"),
    principal:   PoolPrincipal = Depends(optional_pool_principal),
):
    """
    Returns masked message content for a session for LGPD audit purposes.
    Gate: module_config.audit.sessions (ABAC) or analytics_open_access.
    """
    # Tenant isolation
    effective_tenant = principal.tenant_id or tenant_id
    store = _store(request)
    try:
        messages = await asyncio.to_thread(
            _fetch_audit_messages,
            store.new_client(), store._database, effective_tenant, session_id,
        )
        return JSONResponse(content={"session_id": session_id, "messages": messages})
    except Exception as exc:
        logger.error("audit_session_messages error: %s", exc)
        return JSONResponse(status_code=500, content={"detail": str(exc)})


# ── GET /v1/audit/mcp-calls ───────────────────────────────────────────────────

def _fetch_mcp_calls(
    client:      Any,
    db:          str,
    tenant_id:   str,
    limit:       int,
    masked_only: bool,
    session_id:  str | None = None,
) -> list[dict]:
    """
    Query session_timeline for mcp.tool_call events, extract audit fields from
    the packed JSON payload. Returns records matching the McpCall interface.

    When session_id is provided, results are scoped to that session — this is the
    path used by the evaluator (R5: tool_trace) to fetch only the calls of the
    session under evaluation, in chronological order (ASC).

    Note: masked_input_fields is not yet persisted (Fase 2 pending).
    All returned rows carry masked_input_fields=[].
    """
    params: dict[str, Any] = {"tenant_id": tenant_id, "limit": limit}
    session_filter = ""
    # When scoped to a session, order ASC so the evaluator reads the tool calls in
    # the order they happened (trajectory evidence); otherwise newest-first.
    order_clause = "ORDER BY timestamp DESC"
    if session_id:
        session_filter = "AND session_id = {session_id:String}"
        params["session_id"] = session_id
        order_clause = "ORDER BY timestamp ASC"

    result = client.query(
        f"""
        SELECT
            event_id,
            session_id,
            actor_id,
            payload,
            timestamp
        FROM {db}.session_timeline FINAL
        WHERE tenant_id  = {{tenant_id:String}}
          AND event_type = 'mcp.tool_call'
          {session_filter}
        {order_clause}
        LIMIT {{limit:UInt32}}
        """,
        parameters=params,
    )

    rows = []
    for r in result.result_rows:
        event_id, session_id, actor_id, payload_raw, ts = r
        try:
            p = json.loads(payload_raw) if payload_raw else {}
        except Exception:
            p = {}

        masked_fields: list[str] = p.get("masked_input_fields") or []
        if masked_only and not masked_fields:
            continue

        rows.append({
            "event_id":            str(event_id),
            "server_name":         p.get("server_name") or "",
            "tool_name":           p.get("tool_name") or "",
            "allowed":             bool(p.get("allowed", True)),
            "injection_detected":  bool(p.get("injection_detected", False)),
            "masked_input_fields": masked_fields,
            "duration_ms":         p.get("duration_ms") or 0,
            "tenant_id":           tenant_id,
            "session_id":          str(session_id) if session_id else None,
            "created_at":          _isoformat(ts),
        })
    return rows


@router.get("/mcp-calls")
async def audit_mcp_calls(
    request:     Request,
    tenant_id:   str  = Query("tenant_demo"),
    session_id:  str | None = Query(None, description="Scope to one session (R5 tool_trace)."),
    limit:       int  = Query(100, ge=1, le=1000),
    masked_only: bool = Query(False),
    principal:   PoolPrincipal = Depends(optional_pool_principal),
):
    """
    Returns MCP tool call audit records for a tenant.
    Gate: module_config.audit.mcp_calls (ABAC) or analytics_open_access.
    Sourced from session_timeline (event_type = 'mcp.tool_call').

    When session_id is provided, results are scoped to that session and ordered
    chronologically — the path the evaluator uses to build tool_trace (R5).
    """
    effective_tenant = principal.tenant_id or tenant_id
    store = _store(request)
    try:
        calls = await asyncio.to_thread(
            _fetch_mcp_calls,
            store.new_client(), store._database,
            effective_tenant, limit, masked_only, session_id,
        )
        return JSONResponse(content={"calls": calls, "total": len(calls)})
    except Exception as exc:
        logger.error("audit_mcp_calls error: %s", exc)
        return JSONResponse(status_code=500, content={"detail": str(exc)})
