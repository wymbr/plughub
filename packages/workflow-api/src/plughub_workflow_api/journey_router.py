"""
journey_router.py
REST endpoints for Journey lifecycle (Arc 10).

All mutating operations that create or modify journeys are also triggered via
the MCP tool journey_start / journey_link_session / journey_merge — those tools
call these endpoints internally, ensuring every operation is audited by
McpInterceptor.

Endpoints:
  POST   /v1/journeys                          — create journey + trigger workflow
  GET    /v1/journeys/{journey_id}             — get journey
  GET    /v1/journeys                          — list journeys (tenant, status, customer_id, skill_id)
  POST   /v1/journeys/{journey_id}/link-session — link additional session
  POST   /v1/journeys/{journey_id}/merge       — merge secondary into primary
  PATCH  /v1/journeys/{journey_id}/status      — update status (internal use: workflow lifecycle)
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .db import (
    db_create_journey,
    db_get_journey,
    db_list_journeys,
    db_merge_journeys,
    db_set_journey_workflow_instance,
    db_update_journey_status,
)
from .kafka_emitter import (
    emit_journey_merged,
    emit_journey_session_linked,
    emit_journey_started,
    emit_journey_status_changed,
)

logger = logging.getLogger("plughub.workflow.journey")

journey_router = APIRouter(prefix="/v1/journeys", tags=["journeys"])

_JOURNEY_TOPIC = "journey.events"

# ── Request / Response models ─────────────────────────────────────────────────


class JourneyCreateRequest(BaseModel):
    skill_id:          str  = Field(..., description="Skill-flow that governs this service process")
    origin_session_id: str  = Field(..., description="Session that initiates the journey")
    customer_id:       str | None = Field(None, description="Customer identifier (caller.*)")
    metadata:          dict[str, Any] | None = Field(None)


class JourneyLinkSessionRequest(BaseModel):
    session_id: str = Field(..., description="Session to associate with the journey")


class JourneyMergeRequest(BaseModel):
    journey_id_secondary: str = Field(..., description="Journey to be absorbed (will become merged)")


class JourneyStatusPatchRequest(BaseModel):
    status: str = Field(..., description="New status: suspended|resumed|completed|failed|cancelled")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _tenant(request: Request) -> str:
    tid = request.headers.get("x-tenant-id")
    if not tid:
        raise HTTPException(status_code=400, detail="x-tenant-id header required")
    return tid


async def _trigger_workflow(
    request:           Request,
    skill_id:          str,
    origin_session_id: str,
    journey_id:        str,
    tenant_id:         str,
    metadata:          dict | None,
) -> str:
    """
    Trigger workflow-api /v1/trigger for the journey's skill-flow.
    Returns the workflow instance_id.
    """
    settings = request.app.state.settings
    workflow_url = getattr(settings, "workflow_api_url", "http://localhost:3800")

    payload = {
        "flow_id":          skill_id,
        "tenant_id":        tenant_id,
        "session_id":       origin_session_id,
        "origin_session_id": origin_session_id,
        "journey_id":       journey_id,
        "trigger_type":     "journey",
        "metadata":         metadata or {},
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(
            f"{workflow_url}/v1/trigger",
            json=payload,
            headers={"x-tenant-id": tenant_id, "x-internal": "1"},
        )
        if resp.status_code not in (200, 201):
            raise HTTPException(
                status_code=502,
                detail=f"Workflow trigger failed: {resp.status_code} {resp.text}",
            )
        body = resp.json()
        return body.get("instance_id") or body.get("id") or ""


# ── Endpoints ─────────────────────────────────────────────────────────────────

@journey_router.post("", status_code=201)
async def create_journey(body: JourneyCreateRequest, request: Request) -> dict:
    """
    Create a Journey and trigger its governing skill-flow workflow.
    Called by mcp-server-plughub journey_start tool (never directly from UI).
    """
    tenant_id = _tenant(request)
    pool      = request.app.state.pool
    producer  = request.app.state.producer

    # 1. Create the Journey record
    journey = await db_create_journey(
        pool,
        tenant_id         = tenant_id,
        skill_id          = body.skill_id,
        origin_session_id = body.origin_session_id,
        customer_id       = body.customer_id,
        metadata          = body.metadata,
    )
    journey_id = journey["journey_id"]

    # 2. Trigger the workflow
    instance_id: str | None = None
    try:
        instance_id = await _trigger_workflow(
            request,
            skill_id          = body.skill_id,
            origin_session_id = body.origin_session_id,
            journey_id        = journey_id,
            tenant_id         = tenant_id,
            metadata          = body.metadata,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Workflow trigger failed for journey %s: %s", journey_id, exc)
        await db_update_journey_status(pool, journey_id, "failed")
        raise HTTPException(status_code=502, detail=f"Workflow trigger error: {exc}") from exc

    # 3. Associate the workflow instance
    if instance_id:
        journey = await db_set_journey_workflow_instance(pool, journey_id, instance_id) or journey

    # 4. Publish journey_started
    await emit_journey_started(
        producer,
        _JOURNEY_TOPIC,
        journey_id           = journey_id,
        tenant_id            = tenant_id,
        skill_id             = body.skill_id,
        origin_session_id    = body.origin_session_id,
        workflow_instance_id = instance_id,
        customer_id          = body.customer_id,
        metadata             = body.metadata,
    )

    return {**journey, "workflow_instance_id": instance_id}


@journey_router.get("/{journey_id}")
async def get_journey(journey_id: str, request: Request) -> dict:
    tenant_id = _tenant(request)
    journey   = await db_get_journey(request.app.state.pool, journey_id, tenant_id)
    if not journey:
        raise HTTPException(status_code=404, detail="Journey not found")
    return journey


@journey_router.get("")
async def list_journeys(
    request:     Request,
    status:      str | None = Query(None),
    customer_id: str | None = Query(None),
    skill_id:    str | None = Query(None),
    limit:       int = Query(50, ge=1, le=200),
    offset:      int = Query(0, ge=0),
) -> dict:
    tenant_id = _tenant(request)
    items = await db_list_journeys(
        request.app.state.pool,
        tenant_id   = tenant_id,
        status      = status,
        customer_id = customer_id,
        skill_id    = skill_id,
        limit       = limit,
        offset      = offset,
    )
    return {"items": items, "total": len(items), "limit": limit, "offset": offset}


@journey_router.post("/{journey_id}/link-session", status_code=200)
async def link_session(journey_id: str, body: JourneyLinkSessionRequest, request: Request) -> dict:
    """
    Associate an additional session with an existing journey.
    Called by mcp-server-plughub journey_link_session tool.
    """
    tenant_id = _tenant(request)
    pool      = request.app.state.pool
    producer  = request.app.state.producer

    journey = await db_get_journey(pool, journey_id, tenant_id)
    if not journey:
        raise HTTPException(status_code=404, detail="Journey not found")
    if journey["status"] == "merged":
        raise HTTPException(status_code=409, detail="Cannot link session to a merged journey")

    await emit_journey_session_linked(
        producer,
        _JOURNEY_TOPIC,
        journey_id = journey_id,
        tenant_id  = tenant_id,
        skill_id   = journey["skill_id"],
        session_id = body.session_id,
    )

    return {"ok": True, "journey_id": journey_id, "session_id": body.session_id}


@journey_router.post("/{journey_id}/merge", status_code=200)
async def merge_journey(journey_id: str, body: JourneyMergeRequest, request: Request) -> dict:
    """
    Merge secondary journey (body.journey_id_secondary) into this journey (primary).
    Called by Monitor UI via mcp-server-plughub journey_merge tool.
    Invariant: merged journey is read-only and irreversible.
    """
    tenant_id = _tenant(request)
    pool      = request.app.state.pool
    producer  = request.app.state.producer

    primary   = await db_get_journey(pool, journey_id,               tenant_id)
    secondary = await db_get_journey(pool, body.journey_id_secondary, tenant_id)

    if not primary:
        raise HTTPException(status_code=404, detail="Primary journey not found")
    if not secondary:
        raise HTTPException(status_code=404, detail="Secondary journey not found")
    if secondary["status"] == "merged":
        raise HTTPException(status_code=409, detail="Secondary journey is already merged")
    if primary["status"] == "merged":
        raise HTTPException(status_code=409, detail="Primary journey is itself merged — choose a non-merged journey as target")

    updated = await db_merge_journeys(pool, journey_id, body.journey_id_secondary)
    if not updated:
        raise HTTPException(status_code=409, detail="Merge failed — check journey statuses")

    await emit_journey_merged(
        producer,
        _JOURNEY_TOPIC,
        journey_id         = body.journey_id_secondary,
        journey_id_primary = journey_id,
        tenant_id          = tenant_id,
        skill_id           = secondary["skill_id"],
    )

    return updated


@journey_router.patch("/{journey_id}/status", status_code=200)
async def patch_journey_status(journey_id: str, body: JourneyStatusPatchRequest, request: Request) -> dict:
    """
    Internal endpoint — called by workflow lifecycle events (suspended, resumed, completed, etc.)
    to keep the Journey status in sync with its WorkflowInstance.
    """
    tenant_id = _tenant(request)
    pool      = request.app.state.pool
    producer  = request.app.state.producer

    valid_transitions = {"suspended", "active", "completed", "failed", "cancelled"}
    if body.status not in valid_transitions:
        raise HTTPException(status_code=400, detail=f"Invalid status: {body.status}. Use: {valid_transitions}")

    journey = await db_get_journey(pool, journey_id, tenant_id)
    if not journey:
        raise HTTPException(status_code=404, detail="Journey not found")
    if journey["status"] == "merged":
        raise HTTPException(status_code=409, detail="Merged journeys are read-only")

    # Map internal status to Kafka event type
    _event_map = {
        "suspended": "journey_suspended",
        "active":    "journey_resumed",
        "completed": "journey_completed",
        "failed":    "journey_failed",
        "cancelled": "journey_cancelled",
    }
    updated = await db_update_journey_status(pool, journey_id, body.status)
    if not updated:
        raise HTTPException(status_code=409, detail="Status update failed")

    await emit_journey_status_changed(
        producer,
        _JOURNEY_TOPIC,
        event_type = _event_map[body.status],
        journey_id = journey_id,
        tenant_id  = tenant_id,
        skill_id   = journey["skill_id"],
    )

    return updated
