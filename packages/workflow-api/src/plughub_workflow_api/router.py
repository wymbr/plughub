"""
router.py
FastAPI routes for the Workflow API.

Endpoints:
  POST /v1/workflow/trigger                     — create + start a WorkflowInstance
  POST /v1/workflow/instances/{id}/persist-suspend — called by Skill Flow engine on suspend
  POST /v1/workflow/resume                      — resume a suspended instance (token-based)
  POST /v1/workflow/instances/{id}/complete     — mark an instance completed (called by engine)
  POST /v1/workflow/instances/{id}/fail         — mark an instance failed (called by engine)
  GET  /v1/workflow/instances                   — list instances
  GET  /v1/workflow/instances/{id}              — get instance detail
  POST /v1/workflow/instances/{id}/cancel       — cancel active/suspended instance

Architecture note:
  The Skill Flow engine runs in a TypeScript worker process. When it hits a
  suspend step, it calls POST /persist-suspend to delegate persistence and
  deadline calculation to this service. The worker also calls /complete and
  /fail to report the final outcome.

  When an external actor sends a resume signal (approval, input, webhook, etc.),
  they call POST /resume with the resume_token. The workflow-api records the
  decision and emits workflow.resumed to Kafka. A Kafka consumer (or the worker
  itself) picks up the event and calls engine.run() with resumeContext set.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .calendar_client import calculate_deadline
from .db import (
    db_cancel_instance,
    db_complete_collect,
    db_complete_instance,
    db_create_collect,
    db_create_instance,
    db_fail_instance,
    db_get_collect_by_token,
    db_get_instance,
    db_get_instance_by_token,
    db_list_collects_by_campaign,
    db_list_instances,
    db_resume_instance,
    db_suspend_instance,
)
from .kafka_emitter import (
    emit_cancelled,
    emit_collect_requested,
    emit_collect_responded,
    emit_completed,
    emit_failed,
    emit_resumed,
    emit_started,
    emit_suspended,
)

logger = logging.getLogger("plughub.workflow.router")
router = APIRouter()


def _pool(request: Request):
    return request.app.state.pool


def _producer(request: Request):
    return getattr(request.app.state, "producer", None)


def _settings(request: Request):
    return request.app.state.settings


# ── Trigger ───────────────────────────────────────────────────────────────────

class TriggerRequest(BaseModel):
    tenant_id:    str
    flow_id:      str
    trigger_type: str = "manual"
    session_id:   str | None = None
    pool_id:      str | None = None
    context:      dict = Field(default_factory=dict)
    metadata:     dict = Field(default_factory=dict)


@router.post("/v1/workflow/trigger", status_code=201)
async def trigger_workflow(
    body:    TriggerRequest,
    request: Request,
    pool=Depends(_pool),
) -> dict[str, Any]:
    """
    Create a new WorkflowInstance and signal that execution should begin.
    The actual Skill Flow execution is delegated to a TypeScript worker that
    consumes the workflow.started Kafka event.
    """
    settings = _settings(request)
    producer = _producer(request)

    instance = await db_create_instance(pool, {
        "installation_id": settings.installation_id,
        "organization_id": settings.organization_id,
        "tenant_id":       body.tenant_id,
        "flow_id":         body.flow_id,
        "session_id":      body.session_id,
        "pool_id":         body.pool_id,
        "metadata":        body.metadata,
        "pipeline_state":  {"contact_context": body.context},
    })

    await emit_started(
        producer, settings.kafka_topic,
        installation_id=settings.installation_id,
        organization_id=settings.organization_id,
        tenant_id=body.tenant_id,
        instance_id=instance["id"],
        flow_id=body.flow_id,
        session_id=body.session_id,
        trigger_type=body.trigger_type,
    )

    return instance


# ── Persist Suspend ───────────────────────────────────────────────────────────

class PersistSuspendRequest(BaseModel):
    step_id:        str
    resume_token:   str
    reason:         str
    timeout_hours:  float = 48.0
    business_hours: bool  = True
    # Optional: entity to use for calendar association lookup
    entity_type:    str   = "workflow"
    entity_id:      str | None = None
    calendar_id:    str | None = None      # reserved — future direct-calendar override
    pipeline_state: dict  = Field(default_factory=dict)
    metadata:       dict  = Field(default_factory=dict)


@router.post("/v1/workflow/instances/{instance_id}/persist-suspend", status_code=200)
async def persist_suspend(
    instance_id: str,
    body:        PersistSuspendRequest,
    request:     Request,
    pool=Depends(_pool),
) -> dict[str, Any]:
    """
    Called by the TypeScript Skill Flow engine when it hits a suspend step.
    Calculates the business-hours deadline, persists suspension state to PostgreSQL,
    and publishes workflow.suspended to Kafka.

    Returns { resume_expires_at } so the engine can store it in pipeline_state.
    """
    instance = await db_get_instance(pool, instance_id)
    if not instance:
        raise HTTPException(404, "workflow instance not found")
    if instance["status"] not in ("active", "suspended"):
        raise HTTPException(
            409,
            f"Cannot suspend instance in status '{instance['status']}'"
        )

    settings = _settings(request)
    producer = _producer(request)

    # Calculate deadline
    now_utc = datetime.now(timezone.utc)

    if body.business_hours and body.entity_id:
        deadline = await calculate_deadline(
            calendar_api_url=settings.calendar_api_url,
            tenant_id=instance["tenant_id"],
            entity_type=body.entity_type,
            entity_id=body.entity_id,
            from_dt=now_utc,
            hours=body.timeout_hours,
        )
    else:
        from datetime import timedelta
        deadline = now_utc + timedelta(hours=body.timeout_hours)

    resume_expires_at = deadline.isoformat()

    updated = await db_suspend_instance(
        pool,
        instance_id=instance_id,
        step_id=body.step_id,
        resume_token=body.resume_token,
        suspend_reason=body.reason,
        resume_expires_at=resume_expires_at,
        pipeline_state=body.pipeline_state or instance["pipeline_state"],
    )
    if not updated:
        raise HTTPException(409, "Suspension failed — concurrent update detected")

    await emit_suspended(
        producer, settings.kafka_topic,
        tenant_id=instance["tenant_id"],
        instance_id=instance_id,
        flow_id=instance["flow_id"],
        current_step=body.step_id,
        suspend_reason=body.reason,
        resume_expires_at=resume_expires_at,
    )

    return {"resume_expires_at": resume_expires_at, "instance": updated}


# ── Resume ────────────────────────────────────────────────────────────────────

class ResumeRequest(BaseModel):
    token:    str
    decision: str   # approved | rejected | input | timeout
    payload:  dict  = Field(default_factory=dict)


@router.post("/v1/workflow/resume", status_code=200)
async def resume_workflow(
    body:    ResumeRequest,
    request: Request,
    pool=Depends(_pool),
) -> dict[str, Any]:
    """
    Resume a suspended workflow instance using its resume_token.
    Validates the token, checks expiry, records the decision, and
    publishes workflow.resumed to Kafka.

    The Skill Flow worker picks up workflow.resumed and calls engine.run()
    with resumeContext = { decision, step_id, payload }.
    """
    instance = await db_get_instance_by_token(pool, body.token)
    if not instance:
        raise HTTPException(404, "resume_token not found or already consumed")
    if instance["status"] != "suspended":
        raise HTTPException(
            409,
            f"Instance is not suspended (current status: '{instance['status']}')"
        )

    # Check expiry (only for non-timeout decisions — timeout is system-generated)
    if body.decision != "timeout" and instance["resume_expires_at"]:
        expires = datetime.fromisoformat(instance["resume_expires_at"])
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) > expires:
            raise HTTPException(410, "resume_token has expired")

    # Determine next step based on decision
    # (The actual step routing happens in the engine via resumeContext)
    # We just record the decision and notify the worker via Kafka

    settings = _settings(request)
    producer = _producer(request)

    # Build next_step hint from pipeline_state (optional, for Kafka event only)
    current_step = instance.get("current_step") or "unknown"

    # Resume the instance in DB (clears resume_token, sets status=active)
    updated = await db_resume_instance(
        pool,
        instance_id=instance["id"],
        pipeline_state=instance["pipeline_state"],
    )
    if not updated:
        raise HTTPException(409, "Resume failed — concurrent update detected")

    # Calculate wait duration
    wait_ms = 0
    if instance.get("suspended_at"):
        suspended_dt = datetime.fromisoformat(instance["suspended_at"])
        if suspended_dt.tzinfo is None:
            suspended_dt = suspended_dt.replace(tzinfo=timezone.utc)
        wait_ms = int((datetime.now(timezone.utc) - suspended_dt).total_seconds() * 1000)

    await emit_resumed(
        producer, settings.kafka_topic,
        tenant_id=instance["tenant_id"],
        instance_id=instance["id"],
        flow_id=instance["flow_id"],
        decision=body.decision,
        resumed_from=current_step,
        next_step="__pending_engine__",   # engine resolves after resumeContext is processed
        wait_duration_ms=wait_ms,
    )

    return {
        "instance_id":  instance["id"],
        "flow_id":      instance["flow_id"],
        "decision":     body.decision,
        "wait_duration_ms": wait_ms,
        "instance":     updated,
    }


# ── Complete / Fail (called by engine worker) ─────────────────────────────────

class CompleteRequest(BaseModel):
    outcome:        str
    pipeline_state: dict = Field(default_factory=dict)


@router.post("/v1/workflow/instances/{instance_id}/complete", status_code=200)
async def complete_workflow(
    instance_id: str,
    body:        CompleteRequest,
    request:     Request,
    pool=Depends(_pool),
) -> dict[str, Any]:
    """Called by the Skill Flow worker when the engine returns outcome='resolved'."""
    instance = await db_get_instance(pool, instance_id)
    if not instance:
        raise HTTPException(404, "workflow instance not found")

    settings = _settings(request)
    producer = _producer(request)

    # Calculate duration
    created = datetime.fromisoformat(instance["created_at"])
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    duration_ms = int((datetime.now(timezone.utc) - created).total_seconds() * 1000)

    updated = await db_complete_instance(
        pool, instance_id, body.outcome, body.pipeline_state
    )
    if not updated:
        raise HTTPException(409, f"Cannot complete instance in status '{instance['status']}'")

    await emit_completed(
        producer, settings.kafka_topic,
        tenant_id=instance["tenant_id"],
        instance_id=instance_id,
        flow_id=instance["flow_id"],
        outcome=body.outcome,
        duration_ms=duration_ms,
    )

    return updated


class FailRequest(BaseModel):
    error: str


@router.post("/v1/workflow/instances/{instance_id}/fail", status_code=200)
async def fail_workflow(
    instance_id: str,
    body:        FailRequest,
    request:     Request,
    pool=Depends(_pool),
) -> dict[str, Any]:
    """Called by the Skill Flow worker when the engine raises an unrecoverable error."""
    instance = await db_get_instance(pool, instance_id)
    if not instance:
        raise HTTPException(404, "workflow instance not found")

    settings = _settings(request)
    producer = _producer(request)

    updated = await db_fail_instance(pool, instance_id, body.error)
    if not updated:
        raise HTTPException(409, f"Cannot fail instance in status '{instance['status']}'")

    await emit_failed(
        producer, settings.kafka_topic,
        tenant_id=instance["tenant_id"],
        instance_id=instance_id,
        flow_id=instance["flow_id"],
        current_step=instance.get("current_step"),
        error=body.error,
    )

    return updated


# ── List / Detail ─────────────────────────────────────────────────────────────

@router.get("/v1/workflow/instances")
async def list_instances(
    tenant_id: str,
    status:    str | None = None,
    flow_id:   str | None = None,
    limit:     int = 50,
    offset:    int = 0,
    pool=Depends(_pool),
) -> list[dict]:
    if limit > 200:
        limit = 200
    return await db_list_instances(pool, tenant_id, status, flow_id, limit, offset)


@router.get("/v1/workflow/instances/{instance_id}")
async def get_instance(
    instance_id: str,
    pool=Depends(_pool),
) -> dict[str, Any]:
    instance = await db_get_instance(pool, instance_id)
    if not instance:
        raise HTTPException(404, "workflow instance not found")
    return instance


# ── Cancel ────────────────────────────────────────────────────────────────────

class CancelRequest(BaseModel):
    cancelled_by: str = "operator"
    reason:       str | None = None


@router.post("/v1/workflow/instances/{instance_id}/cancel", status_code=200)
async def cancel_instance(
    instance_id: str,
    body:        CancelRequest,
    request:     Request,
    pool=Depends(_pool),
) -> dict[str, Any]:
    instance = await db_get_instance(pool, instance_id)
    if not instance:
        raise HTTPException(404, "workflow instance not found")
    if instance["status"] in ("completed", "failed", "timed_out", "cancelled"):
        raise HTTPException(
            409,
            f"Instance already in terminal status '{instance['status']}'"
        )

    settings = _settings(request)
    producer = _producer(request)

    updated = await db_cancel_instance(pool, instance_id)
    if not updated:
        raise HTTPException(409, "Cancel failed — concurrent update detected")

    await emit_cancelled(
        producer, settings.kafka_topic,
        tenant_id=instance["tenant_id"],
        instance_id=instance_id,
        flow_id=instance["flow_id"],
        cancelled_by=body.cancelled_by,
        reason=body.reason,
    )

    return updated


# ── Collect: Persist ──────────────────────────────────────────────────────────

class CollectPersistRequest(BaseModel):
    """
    Called by the Skill Flow engine (TypeScript worker) when it executes a
    collect step.  The workflow-api calculates send_at and expires_at using
    the calendar-api (or wall-clock fallback) and creates the collect_instance.
    """
    step_id:        str
    collect_token:  str
    target:         dict                  # { type, id }
    channel:        str
    interaction:    str
    prompt:         str
    options:        list = Field(default_factory=list)
    fields:         list = Field(default_factory=list)
    scheduled_at:   str | None = None     # ISO-8601 absolute send time
    delay_hours:    float | None = None   # relative send time from now
    timeout_hours:  float = 48.0
    business_hours: bool  = True
    entity_type:    str   = "workflow"
    entity_id:      str | None = None     # for calendar association lookup
    calendar_id:    str | None = None     # reserved for direct calendar override
    campaign_id:    str | None = None


@router.post("/v1/workflow/instances/{instance_id}/collect/persist", status_code=201)
async def persist_collect(
    instance_id: str,
    body:        CollectPersistRequest,
    request:     Request,
    pool=Depends(_pool),
) -> dict[str, Any]:
    """
    Called by the TypeScript Skill Flow engine when it hits a collect step.

    1. Determines send_at from scheduled_at / delay_hours / now
    2. Calculates expires_at = send_at + timeout_hours (business-hours-aware)
    3. Persists collect_instance with status='requested'
    4. Publishes collect.requested to Kafka (channel-gateway picks this up
       at send_at to initiate outbound contact)

    Returns { send_at, expires_at } so the engine can store them in pipeline_state.
    """
    instance = await db_get_instance(pool, instance_id)
    if not instance:
        raise HTTPException(404, "workflow instance not found")

    settings = _settings(request)
    producer = _producer(request)
    now_utc   = datetime.now(timezone.utc)

    # ── Determine send_at ─────────────────────────────────────────────────────
    if body.scheduled_at:
        send_dt = datetime.fromisoformat(body.scheduled_at)
        if send_dt.tzinfo is None:
            send_dt = send_dt.replace(tzinfo=timezone.utc)
    elif body.delay_hours is not None:
        from datetime import timedelta
        send_dt = now_utc + timedelta(hours=body.delay_hours)
    else:
        send_dt = now_utc

    # ── Calculate expires_at (business-hours-aware) ───────────────────────────
    if body.business_hours and body.entity_id:
        expires_dt = await calculate_deadline(
            calendar_api_url=settings.calendar_api_url,
            tenant_id=instance["tenant_id"],
            entity_type=body.entity_type,
            entity_id=body.entity_id,
            from_dt=send_dt,
            hours=body.timeout_hours,
        )
    else:
        from datetime import timedelta
        expires_dt = send_dt + timedelta(hours=body.timeout_hours)

    send_at    = send_dt.isoformat()
    expires_at = expires_dt.isoformat()

    # ── Persist collect_instance ──────────────────────────────────────────────
    collect = await db_create_collect(
        pool,
        collect_token=body.collect_token,
        instance_id=instance_id,
        tenant_id=instance["tenant_id"],
        flow_id=instance["flow_id"],
        campaign_id=body.campaign_id or instance.get("campaign_id"),
        step_id=body.step_id,
        target_type=body.target.get("type", "customer"),
        target_id=body.target.get("id", ""),
        channel=body.channel,
        interaction=body.interaction,
        prompt=body.prompt,
        options=body.options,
        fields=body.fields,
        send_at=send_dt,
        expires_at=expires_dt,
    )

    # ── Publish collect.requested ─────────────────────────────────────────────
    await emit_collect_requested(
        producer, settings.collect_topic,
        tenant_id=instance["tenant_id"],
        instance_id=instance_id,
        flow_id=instance["flow_id"],
        campaign_id=collect.get("campaign_id"),
        step_id=body.step_id,
        collect_token=body.collect_token,
        target_type=body.target.get("type", "customer"),
        target_id=body.target.get("id", ""),
        channel=body.channel,
        interaction=body.interaction,
        prompt=body.prompt,
        options=body.options,
        fields=body.fields,
        send_at=send_at,
        expires_at=expires_at,
    )

    return {"send_at": send_at, "expires_at": expires_at, "collect": collect}


# ── Collect: Respond ──────────────────────────────────────────────────────────

class CollectRespondRequest(BaseModel):
    """
    Called by the channel-gateway (or any external actor) when the target
    responds to a collect request.  The collect_token is the correlation key.
    """
    collect_token: str
    response_data: dict  = Field(default_factory=dict)
    channel:       str   = ""
    session_id:    str | None = None


@router.post("/v1/workflow/collect/respond", status_code=200)
async def respond_collect(
    body:    CollectRespondRequest,
    request: Request,
    pool=Depends(_pool),
) -> dict[str, Any]:
    """
    Receives a collect response from the channel-gateway.

    1. Looks up the collect_instance by collect_token
    2. Transitions it to responded
    3. Resumes the parent WorkflowInstance by delegating to resume_workflow logic
    4. Publishes collect.responded to Kafka

    The Skill Flow worker receives workflow.resumed and calls engine.run()
    with resumeContext = { decision: "input", step_id, payload: response_data }.
    """
    collect = await db_get_collect_by_token(pool, body.collect_token)
    if not collect:
        raise HTTPException(404, "collect_token not found")
    if collect["status"] not in ("requested", "sent"):
        raise HTTPException(
            409,
            f"Collect is not awaiting response (status: '{collect['status']}')"
        )

    settings = _settings(request)
    producer = _producer(request)

    # Complete the collect_instance
    updated_collect = await db_complete_collect(
        pool, body.collect_token, body.response_data
    )
    if not updated_collect:
        raise HTTPException(409, "Collect completion failed — concurrent update")

    elapsed_ms = updated_collect.get("elapsed_ms") or 0

    # Resume the parent workflow instance
    instance = await db_get_instance(pool, collect["instance_id"])
    if not instance:
        logger.error(
            "collect.respond: parent instance %s not found for token %s",
            collect["instance_id"], body.collect_token,
        )
        raise HTTPException(404, "parent workflow instance not found")

    # Only resume if still suspended (idempotency)
    if instance["status"] == "suspended":
        wait_ms = 0
        if instance.get("suspended_at"):
            suspended_dt = datetime.fromisoformat(instance["suspended_at"])
            if suspended_dt.tzinfo is None:
                suspended_dt = suspended_dt.replace(tzinfo=timezone.utc)
            wait_ms = int((datetime.now(timezone.utc) - suspended_dt).total_seconds() * 1000)

        updated_instance = await db_resume_instance(
            pool,
            instance_id=instance["id"],
            pipeline_state=instance["pipeline_state"],
        )

        await emit_resumed(
            producer, settings.kafka_topic,
            tenant_id=instance["tenant_id"],
            instance_id=instance["id"],
            flow_id=instance["flow_id"],
            decision="input",
            resumed_from=instance.get("current_step") or "unknown",
            next_step="__pending_engine__",
            wait_duration_ms=wait_ms,
        )
    else:
        updated_instance = instance

    # Publish collect.responded
    await emit_collect_responded(
        producer, settings.collect_topic,
        tenant_id=instance["tenant_id"],
        instance_id=instance["id"],
        collect_token=body.collect_token,
        channel=body.channel or collect["channel"],
        response_data=body.response_data,
        elapsed_ms=elapsed_ms,
    )

    return {
        "collect_token": body.collect_token,
        "elapsed_ms":    elapsed_ms,
        "collect":       updated_collect,
        "instance":      updated_instance,
    }


# ── Campaign query ─────────────────────────────────────────────────────────────

@router.get("/v1/workflow/campaigns/{campaign_id}/collects")
async def list_campaign_collects(
    campaign_id: str,
    tenant_id:   str,
    limit:       int = 200,
    offset:      int = 0,
    pool=Depends(_pool),
) -> list[dict]:
    """List all collect_instances for a campaign (for CampaignPanel)."""
    if limit > 1000:
        limit = 1000
    return await db_list_collects_by_campaign(pool, tenant_id, campaign_id, limit, offset)
