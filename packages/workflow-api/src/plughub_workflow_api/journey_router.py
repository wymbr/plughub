"""
journey_router.py
REST endpoints for Journey lifecycle (Arc 10, Arc 16).

All mutating operations that create or modify journeys are also triggered via
the MCP tool journey_start / journey_link_session / journey_merge — those tools
call these endpoints internally, ensuring every operation is audited by
McpInterceptor.

Endpoints:
  POST   /v1/journeys                                  — create journey + trigger workflow
  POST   /v1/journeys/from-instance/{instance_id}      — Arc 10 Phase B: creates_journey:true auto-link
  GET    /v1/journeys/{journey_id}                     — get journey
  GET    /v1/journeys                                  — list journeys (tenant, status, customer_id, skill_id, pool_id)
  POST   /v1/journeys/{journey_id}/link-session        — link additional session
  POST   /v1/journeys/{journey_id}/merge               — merge secondary into primary
  GET    /v1/journeys/{journey_id}/collect-sessions     — list collect session IDs for split picker (Arc 10 Phase F)
  POST   /v1/journeys/{journey_id}/split               — extract collect sessions into new journey (Arc 10 Phase F)
  PATCH  /v1/journeys/{journey_id}/status              — update status (internal use: workflow lifecycle)
  POST   /v1/journeys/{journey_id}/resume              — Arc 16 Phase B: resume a suspended journey (Tier 1 poller)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .db import (
    db_create_journey,
    db_create_journey_for_instance,
    db_get_instance,
    db_get_journey,
    db_get_instance_sessions,
    db_list_journey_instances,
    db_list_journeys,
    db_merge_journeys,
    db_resume_instance,
    db_set_journey_workflow_instance,
    db_split_journey,
    db_update_journey_status,
)
from .kafka_emitter import (
    emit_journey_merged,
    emit_journey_session_linked,
    emit_journey_split,
    emit_journey_started,
    emit_journey_status_changed,
    emit_resumed,
)

logger = logging.getLogger("plughub.workflow.journey")

journey_router = APIRouter(prefix="/v1/journeys", tags=["journeys"])

_JOURNEY_TOPIC = "journey.events"

# ── Request / Response models ─────────────────────────────────────────────────


class JourneyCreateRequest(BaseModel):
    skill_id:          str  = Field(..., description="Skill-flow that governs this service process")
    origin_session_id: str  = Field(..., description="Session that initiates the journey")
    customer_id:       str | None = Field(None, description="Customer identifier (caller.*)")
    pool_id:           str | None = Field(None, description="Pool that originated this journey — enables Tier 1 poller listing")
    # Arc 17: JourneyType governance
    journey_type_id:   str | None = Field(None, description="Arc 17: registered journey type slug (e.g. 'portabilidade_telco')")
    sla_ms:            int | None = Field(None, description="Arc 17: denormalized sla_ms from JourneyType at creation time")
    metadata:          dict[str, Any] | None = Field(None)


class JourneyLinkSessionRequest(BaseModel):
    session_id:         str           = Field(..., description="Session to associate with the journey")
    # D.5 enrichment — optional; populate when session has ended so the audit
    # log captures the workflow progression across contacts.
    current_step:       str | None    = Field(None, description="Workflow step at time of linking")
    session_outcome:    str | None    = Field(None, description="Outcome of this session within the journey")
    session_started_at: str | None    = Field(None, description="ISO datetime the session opened")
    session_ended_at:   str | None    = Field(None, description="ISO datetime the session closed")


class JourneyMergeRequest(BaseModel):
    journey_id_secondary: str = Field(..., description="Journey to be absorbed (will become merged)")


class JourneySplitRequest(BaseModel):
    session_ids: list[str] = Field(..., min_length=1, description=(
        "Collect session IDs to move to the new journey. "
        "Must not include the source journey's origin_session_id."
    ))
    skill_id:    str | None      = Field(None, description="If provided, triggers a new workflow for the new journey")
    metadata:    dict | None     = Field(None, description="Additional context for the new journey")


class JourneyResumeRequest(BaseModel):
    """
    Arc 16 Phase B — Tier 1 poller resume.

    context: optional key/value pairs that the Tier 1 Business Workflow has
    prepared for the resuming workflow instance. Passed as `payload` in the
    workflow.resumed Kafka event so the skill-flow-worker can inject them into
    resumeContext.payload for the engine.

    Note: for durable journey-namespace storage, the caller (MCP tool
    journey_resume) should call journey_context_set before calling this endpoint.
    Context here is ephemeral — it lives only in the Kafka event payload.
    """
    context:  dict[str, Any] | None = Field(None, description="Optional context to pass into the resumed workflow")
    decision: str                   = Field("input", description="Resume decision: input | approval | webhook")


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
            f"{workflow_url}/v1/workflow/trigger",
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
        pool_id           = body.pool_id,
        journey_type_id   = body.journey_type_id,   # Arc 17
        sla_ms            = body.sla_ms,            # Arc 17: denormalized from JourneyType
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
        # Arc 17: JourneyType governance
        journey_type_id      = body.journey_type_id,
        pool_id              = body.pool_id,
    )

    return {**journey, "workflow_instance_id": instance_id}


class JourneyFromInstanceRequest(BaseModel):
    """Arc 17: optional body for creates_journey:true auto-creation path."""
    journey_type_id: str | None = Field(None, description="Arc 17: registered journey type slug from skill YAML")


@journey_router.post("/from-instance/{instance_id}", status_code=201)
async def create_journey_for_instance(
    instance_id: str,
    request:     Request,
    body:        JourneyFromInstanceRequest = JourneyFromInstanceRequest(),
) -> dict:
    """
    Arc 10 Phase B — creates_journey: true support.

    Called by skill-flow-worker when it detects creates_journey=true on the
    skill YAML and the running instance has no journey_id yet.

    Idempotent: if the instance already has a journey_id, returns the existing
    Journey record with HTTP 200 (not 201).

    The skill-flow-worker calls this endpoint at the start of execution (before
    engine.run()), so the journey exists before any step emits events.

    Arc 17: optional body.journey_type_id carries the type slug from the YAML.
    """
    tenant_id = _tenant(request)
    pool      = request.app.state.pool
    producer  = request.app.state.producer

    journey = await db_create_journey_for_instance(
        pool, instance_id, tenant_id,
        journey_type_id = body.journey_type_id,
    )
    if not journey:
        raise HTTPException(status_code=404, detail="WorkflowInstance not found")

    # Publish journey_started only for freshly created journeys
    # (idempotent re-call returns the same journey without re-emitting)
    if journey.get("workflow_instance_id") == instance_id:
        await emit_journey_started(
            producer,
            _JOURNEY_TOPIC,
            journey_id           = journey["journey_id"],
            tenant_id            = tenant_id,
            skill_id             = journey["skill_id"],
            origin_session_id    = journey.get("origin_session_id", ""),
            workflow_instance_id = instance_id,
            customer_id          = journey.get("customer_id"),
            # Arc 17: JourneyType governance (from skill YAML via request body)
            journey_type_id      = body.journey_type_id,
        )

    return journey


@journey_router.get("/{journey_id}")
async def get_journey(journey_id: str, request: Request) -> dict:
    tenant_id = _tenant(request)
    journey   = await db_get_journey(request.app.state.pool, journey_id, tenant_id)
    if not journey:
        raise HTTPException(status_code=404, detail="Journey not found")
    return journey


@journey_router.get("")
async def list_journeys(
    request:         Request,
    status:          str | None = Query(None),
    customer_id:     str | None = Query(None),
    skill_id:        str | None = Query(None),
    pool_id:         str | None = Query(None, description="Filter by originating pool (Arc 16 Tier 1 poller)"),
    journey_type_id: str | None = Query(None, description="Arc 17/18: filter by journey type slug"),
    from_dt:         str | None = Query(None, description="Arc 18: ISO date lower bound (inclusive)"),
    to_dt:           str | None = Query(None, description="Arc 18: ISO date upper bound (inclusive)"),
    limit:           int = Query(50, ge=1, le=200),
    offset:          int = Query(0, ge=0),
) -> dict:
    tenant_id = _tenant(request)
    items = await db_list_journeys(
        request.app.state.pool,
        tenant_id       = tenant_id,
        status          = status,
        customer_id     = customer_id,
        skill_id        = skill_id,
        pool_id         = pool_id,
        journey_type_id = journey_type_id,
        from_dt         = from_dt,
        to_dt           = to_dt,
        limit           = limit,
        offset          = offset,
    )
    return {"items": items, "total": len(items), "limit": limit, "offset": offset}


@journey_router.get("/{journey_id}/instances")
async def list_journey_instances(
    journey_id: str,
    request:    Request,
    limit:      int = Query(50, ge=1, le=200),
    offset:     int = Query(0, ge=0),
) -> dict:
    """
    Arc 18 C1 — List workflow instances linked to a journey.

    Used by Analytics/Journeys drill-down:
      /analise/journeys?journey=:id → shows the instances (processes) of that journey.

    Returns { items, total, journey_id } ordered by created_at ASC.
    """
    tenant_id = _tenant(request)
    pool      = request.app.state.pool

    journey = await db_get_journey(pool, journey_id, tenant_id)
    if not journey:
        raise HTTPException(status_code=404, detail="Journey not found")

    items = await db_list_journey_instances(pool, journey_id, tenant_id, limit, offset)
    return {
        "journey_id": journey_id,
        "items":      items,
        "total":      len(items),
        "limit":      limit,
        "offset":     offset,
    }


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
        journey_id         = journey_id,
        tenant_id          = tenant_id,
        skill_id           = journey["skill_id"],
        session_id         = body.session_id,
        current_step       = body.current_step,
        session_outcome    = body.session_outcome,
        session_started_at = body.session_started_at,
        session_ended_at   = body.session_ended_at,
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


@journey_router.get("/{journey_id}/collect-sessions", status_code=200)
async def list_journey_collect_sessions(journey_id: str, request: Request) -> dict:
    """
    Arc 10 Phase F — List collect sessions belonging to this journey.
    Returns session IDs (from workflow.instances.session_id) linked via collect_instances.
    Used by the Monitor UI to populate the session picker for journey_split.
    """
    tenant_id = _tenant(request)
    pool      = request.app.state.pool

    journey = await db_get_journey(pool, journey_id, tenant_id)
    if not journey:
        raise HTTPException(status_code=404, detail="Journey not found")

    rows = await pool.fetch(
        """
        SELECT DISTINCT wi.session_id
        FROM workflow.collect_instances ci
        JOIN workflow.instances wi ON ci.instance_id = wi.id
        WHERE ci.journey_id = $1
          AND ci.tenant_id  = $2
          AND wi.session_id IS NOT NULL
        ORDER BY wi.session_id
        """,
        __import__("uuid").UUID(journey_id), tenant_id,
    )
    session_ids = [r["session_id"] for r in rows]

    return {
        "journey_id":        journey_id,
        "origin_session_id": journey["origin_session_id"],
        "collect_sessions":  session_ids,
        "total":             len(session_ids),
    }


@journey_router.post("/{journey_id}/split", status_code=201)
async def split_journey(journey_id: str, body: JourneySplitRequest, request: Request) -> dict:
    """
    Arc 10 Phase F — Extract collect sessions from this journey into a new independent journey.
    Called by mcp-server-plughub journey_split tool.

    Constraints enforced:
      - session_ids must be collect sessions of this journey (journey_id matches)
      - origin_session_id of source journey cannot be in session_ids
      - source journey must not be merged
    """
    tenant_id = _tenant(request)
    pool      = request.app.state.pool
    producer  = request.app.state.producer

    source = await db_get_journey(pool, journey_id, tenant_id)
    if not source:
        raise HTTPException(status_code=404, detail="Journey not found")
    if source["status"] == "merged":
        raise HTTPException(status_code=409, detail="Merged journeys are read-only")
    if source["origin_session_id"] in body.session_ids:
        raise HTTPException(status_code=400, detail="origin_session_cannot_be_split")

    # Determine skill_id: use provided or inherit from source
    skill_id = body.skill_id or source["skill_id"]

    try:
        result = await db_split_journey(
            pool,
            source_journey_id = journey_id,
            session_ids       = body.session_ids,
            skill_id          = skill_id,
            tenant_id         = tenant_id,
            metadata          = body.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    new_journey_id = result["new_journey_id"]

    # Optionally trigger a new workflow for the new journey
    new_workflow_instance_id: str | None = None
    if body.skill_id:
        try:
            new_workflow_instance_id = await _trigger_workflow(
                request,
                skill_id          = body.skill_id,
                origin_session_id = body.session_ids[0],
                journey_id        = new_journey_id,
                tenant_id         = tenant_id,
                metadata          = body.metadata,
            )
            if new_workflow_instance_id:
                await db_set_journey_workflow_instance(pool, new_journey_id, new_workflow_instance_id)
        except HTTPException:
            logger.warning("journey_split: workflow trigger failed for new journey %s — continuing without workflow", new_journey_id)

    # Publish journey_split event
    await emit_journey_split(
        producer,
        "journey.events",
        source_journey_id = journey_id,
        new_journey_id    = new_journey_id,
        tenant_id         = tenant_id,
        skill_id          = skill_id,
        session_ids       = body.session_ids,
    )

    return {
        "new_journey_id":           new_journey_id,
        "new_journey":              result["new_journey"],
        "new_workflow_instance_id": new_workflow_instance_id,
        "moved_count":              result["moved_count"],
        "source_journey_id":        journey_id,
    }


@journey_router.post("/{journey_id}/resume", status_code=200)
async def resume_journey(journey_id: str, body: JourneyResumeRequest, request: Request) -> dict:
    """
    Arc 16 Phase B — Resume a suspended Journey.

    Used by Tier 1 poller workflows (journey_list_suspended + journey_resume MCP tools).
    Encapsulates the resume_token internally so callers never need to handle it.

    Flow:
      1. Validate journey is suspended and has a linked workflow instance
      2. Fetch the workflow instance to get resume_token (must be suspended)
      3. Resume the workflow instance in DB (suspended → active), emit workflow.resumed
      4. Update journey status to active, emit journey_resumed

    If body.context is provided, it is forwarded as payload in the workflow.resumed
    Kafka event. For durable journey-namespace writes, call journey_context_set first.
    """
    tenant_id = _tenant(request)
    db_pool   = request.app.state.pool
    producer  = request.app.state.producer
    settings  = request.app.state.settings

    journey = await db_get_journey(db_pool, journey_id, tenant_id)
    if not journey:
        raise HTTPException(status_code=404, detail="Journey not found")
    if journey["status"] == "merged":
        raise HTTPException(status_code=409, detail="Merged journeys are read-only")
    if journey["status"] != "suspended":
        raise HTTPException(
            status_code=409,
            detail=f"Journey is not suspended (current status: '{journey['status']}')",
        )

    workflow_instance_id = journey.get("workflow_instance_id")
    if not workflow_instance_id:
        raise HTTPException(status_code=409, detail="Journey has no associated workflow instance")

    instance = await db_get_instance(db_pool, workflow_instance_id)
    if not instance:
        raise HTTPException(status_code=404, detail="Workflow instance not found")
    if instance["status"] != "suspended":
        raise HTTPException(
            status_code=409,
            detail=f"Workflow instance is not suspended (current status: '{instance['status']}')",
        )

    # Resume the workflow instance (clears resume_token, sets status=active)
    updated_instance = await db_resume_instance(
        db_pool,
        instance_id    = instance["id"],
        pipeline_state = instance["pipeline_state"],
    )
    if not updated_instance:
        raise HTTPException(status_code=409, detail="Resume failed — concurrent update detected")

    # Calculate wait duration
    wait_ms = 0
    if instance.get("suspended_at"):
        suspended_dt = datetime.fromisoformat(instance["suspended_at"])
        if suspended_dt.tzinfo is None:
            suspended_dt = suspended_dt.replace(tzinfo=timezone.utc)
        wait_ms = int((datetime.now(timezone.utc) - suspended_dt).total_seconds() * 1000)

    # Emit workflow.resumed so skill-flow-worker picks up and runs the engine
    await emit_resumed(
        producer,
        settings.kafka_topic,
        tenant_id        = tenant_id,
        instance_id      = instance["id"],
        flow_id          = instance["flow_id"],
        decision         = body.decision,
        resumed_from     = instance.get("current_step") or "unknown",
        next_step        = "__pending_engine__",
        wait_duration_ms = wait_ms,
        journey_id       = journey_id,
    )

    # Update journey status to active + emit journey_resumed
    updated_journey = await db_update_journey_status(db_pool, journey_id, "active")
    await emit_journey_status_changed(
        producer,
        _JOURNEY_TOPIC,
        event_type = "journey_resumed",
        journey_id = journey_id,
        tenant_id  = tenant_id,
        skill_id   = journey["skill_id"],
    )

    return {
        "journey_id":         journey_id,
        "status":             "active",
        "workflow_instance_id": workflow_instance_id,
        "decision":           body.decision,
        "wait_duration_ms":   wait_ms,
        "journey":            updated_journey,
    }


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
