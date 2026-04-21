"""
supervisor.py
Supervisor intervention API — operator-console users join live sessions as
supervisors, send coaching/intervention messages, and leave cleanly.

Write path: direct XADD to session:{id}:stream using the same field format
that StreamSubscriber._map_event() expects.  Human operators are not AI agents,
so they bypass the MCP lifecycle (agent_login / agent_ready / agent_done).

Three endpoints:
  POST /supervisor/join     — join session, write participant_joined to stream
  POST /supervisor/message  — XADD message (agents_only or all visibility)
  POST /supervisor/leave    — write participant_left, clean up Redis state

Redis keys:
  supervisor:{session_id}:active  →  JSON { participant_id, tenant_id, operator_id, joined_at }
  TTL: 4h (same as other session-scoped data)

Audit: Kafka mcp.audit integration deferred to next iteration.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logger = logging.getLogger("plughub.analytics.supervisor")

router = APIRouter(prefix="/supervisor", tags=["supervisor"])

_SESSION_TTL = 14_400   # 4 h
_VIS_ALLOWED = {"agents_only", "all"}


# ── Pydantic models ────────────────────────────────────────────────────────────

class JoinRequest(BaseModel):
    tenant_id:   str
    session_id:  str
    operator_id: str = "operator"

class MessageRequest(BaseModel):
    tenant_id:      str
    session_id:     str
    participant_id: str
    text:           str
    visibility:     str = "agents_only"

class LeaveRequest(BaseModel):
    tenant_id:      str
    session_id:     str
    participant_id: str


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _xadd(redis, session_id: str, fields: dict[str, str]) -> str:
    """Append to session stream; return the Redis entry ID (string)."""
    key = f"session:{session_id}:stream"
    eid = await redis.xadd(key, fields)
    return eid if isinstance(eid, str) else eid.decode()


async def _get_state(redis, session_id: str) -> dict | None:
    raw = await redis.get(f"supervisor:{session_id}:active")
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/join")
async def join_session(body: JoinRequest, request: Request) -> JSONResponse:
    """
    Join a live session as supervisor.
    Creates supervisor state in Redis and appends participant_joined to stream.
    """
    redis = request.app.state.redis

    # Verify session exists
    meta_raw = await redis.get(f"session:{body.session_id}:meta")
    if not meta_raw:
        raise HTTPException(status_code=404, detail="Session not found or expired")

    try:
        meta = json.loads(meta_raw)
    except Exception:
        meta = {}

    if meta.get("tenant_id", body.tenant_id) != body.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant mismatch")

    # Reject double-join for the same session (idempotency guard)
    existing = await _get_state(redis, body.session_id)
    if existing:
        # Return existing state — caller may retry safely
        return JSONResponse(content={
            "participant_id": existing["participant_id"],
            "session_id":     body.session_id,
            "joined_at":      existing["joined_at"],
            "already_active": True,
        })

    participant_id = str(uuid.uuid4())
    joined_at      = datetime.now(timezone.utc).isoformat()

    await redis.set(
        f"supervisor:{body.session_id}:active",
        json.dumps({
            "participant_id": participant_id,
            "session_id":     body.session_id,
            "tenant_id":      body.tenant_id,
            "operator_id":    body.operator_id,
            "joined_at":      joined_at,
        }),
        ex=_SESSION_TTL,
    )

    # participant_joined — agents_only so it doesn't reach the customer
    await _xadd(redis, body.session_id, {
        "type":       "participant_joined",
        "visibility": "agents_only",
        "author":     json.dumps({"role": "supervisor", "participant_id": participant_id}),
        "payload":    json.dumps({"operator_id": body.operator_id}),
        "event_id":   participant_id,
        "timestamp":  joined_at,
    })

    logger.info("supervisor joined session=%s participant=%s", body.session_id, participant_id)

    return JSONResponse(content={
        "participant_id": participant_id,
        "session_id":     body.session_id,
        "joined_at":      joined_at,
    })


@router.post("/message")
async def send_message(body: MessageRequest, request: Request) -> JSONResponse:
    """
    Send a supervisor message into the session stream.
    visibility="agents_only" → coaching (agents see it, customer does not).
    visibility="all"         → direct intervention (everyone sees it).
    """
    redis = request.app.state.redis

    state = await _get_state(redis, body.session_id)
    if state is None:
        raise HTTPException(status_code=403, detail="Not joined to session as supervisor")
    if state["participant_id"] != body.participant_id:
        raise HTTPException(status_code=403, detail="participant_id mismatch")
    if state["tenant_id"] != body.tenant_id:
        raise HTTPException(status_code=403, detail="Tenant mismatch")

    if not body.text.strip():
        raise HTTPException(status_code=422, detail="Message text cannot be empty")

    visibility = body.visibility if body.visibility in _VIS_ALLOWED else "agents_only"
    event_id   = str(uuid.uuid4())
    timestamp  = datetime.now(timezone.utc).isoformat()

    # Field format matches StreamSubscriber._map_event / _map_message
    stream_eid = await _xadd(redis, body.session_id, {
        "type":       "message",
        "visibility": visibility,
        "author":     json.dumps({"role": "supervisor", "participant_id": body.participant_id}),
        "payload":    json.dumps({"content": {"type": "text", "text": body.text.strip()}}),
        "event_id":   event_id,
        "timestamp":  timestamp,
    })

    logger.debug(
        "supervisor message session=%s vis=%s eid=%s",
        body.session_id, visibility, stream_eid,
    )

    return JSONResponse(content={"event_id": stream_eid, "timestamp": timestamp})


@router.post("/leave")
async def leave_session(body: LeaveRequest, request: Request) -> JSONResponse:
    """
    Leave the session as supervisor.  Idempotent — returns acknowledged:true
    even if the supervisor has already left or was never joined.
    """
    redis = request.app.state.redis

    state = await _get_state(redis, body.session_id)
    if state is None:
        return JSONResponse(content={"acknowledged": True})

    if state["participant_id"] != body.participant_id:
        raise HTTPException(status_code=403, detail="participant_id mismatch")

    timestamp = datetime.now(timezone.utc).isoformat()

    await _xadd(redis, body.session_id, {
        "type":       "participant_left",
        "visibility": "agents_only",
        "author":     json.dumps({"role": "supervisor", "participant_id": body.participant_id}),
        "event_id":   body.participant_id,
        "timestamp":  timestamp,
    })

    await redis.delete(f"supervisor:{body.session_id}:active")

    logger.info("supervisor left session=%s participant=%s", body.session_id, body.participant_id)

    return JSONResponse(content={"acknowledged": True})
