"""
router.py
FastAPI routes for the Workflow API.

Endpoints:
  POST /v1/workflow/trigger                       — create + start a WorkflowInstance
  POST /v1/workflow/instances/{id}/persist-suspend — called by Skill Flow engine on suspend
  POST /v1/workflow/resume                        — resume a suspended instance (token-based)
  POST /v1/workflow/instances/{id}/complete       — mark an instance completed (called by engine)
  POST /v1/workflow/instances/{id}/fail           — mark an instance failed (called by engine)
  GET  /v1/workflow/instances                     — list instances
  GET  /v1/workflow/instances/{id}                — get instance detail
  (POST /v1/workflow/instances/{id}/cancel        — REMOVIDA 2026-08-07, ver § Cancel)

  ── Webhook Trigger ───────────────────────────────────────────────────────────
  POST /v1/workflow/webhooks                      — register a webhook (admin)
  GET  /v1/workflow/webhooks                      — list webhooks for tenant (admin)
  GET  /v1/workflow/webhooks/{webhook_id}         — get webhook detail (admin)
  PATCH /v1/workflow/webhooks/{webhook_id}        — update active/description/context (admin)
  POST /v1/workflow/webhooks/{webhook_id}/rotate  — rotate token (admin)
  DELETE /v1/workflow/webhooks/{webhook_id}       — delete webhook (admin)
  GET  /v1/workflow/webhooks/{webhook_id}/deliveries — delivery log (admin)
  POST /v1/workflow/webhook/{webhook_id}          — PUBLIC trigger endpoint (X-Webhook-Token)

Architecture note:
  The Skill Flow engine runs in a TypeScript worker process. When it hits a
  suspend step, it calls POST /persist-suspend to delegate persistence and
  deadline calculation to this service. The worker also calls /complete and
  /fail to report the final outcome.

  When an external actor sends a resume signal (approval, input, webhook, etc.),
  they call POST /resume with the resume_token. The workflow-api records the
  decision and emits workflow.resumed to Kafka. A Kafka consumer (or the worker
  itself) picks up the event and calls engine.run() with resumeContext set.

  Webhook tokens are stored as SHA-256 hashes — plain tokens are shown once at
  creation and never stored. Authentication uses X-Webhook-Token header with
  constant-time hash comparison.
"""
from __future__ import annotations

import hashlib
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .calendar_client import calculate_deadline
from .db import (
    db_cancel_instance,
    db_complete_collect,
    db_complete_instance,
    db_create_collect,
    db_create_instance,
    db_create_webhook,
    db_delete_webhook,
    db_fail_instance,
    db_get_collect_by_token,
    db_get_instance,
    db_get_instance_by_token,
    db_get_instance_sessions,
    db_get_webhook,
    db_get_webhook_by_token_hash,
    db_list_collects_by_campaign,
    db_list_deliveries,
    db_list_instances,
    db_list_webhooks,
    db_record_delivery,
    db_resume_instance,
    db_rotate_webhook_token,
    db_suspend_instance,
    db_update_webhook,
)
from .kafka_emitter import (
    emit_cancelled,
    emit_collect_requested,
    emit_collect_responded,
    emit_completed,
    emit_events_batch,
    emit_failed,
    emit_resumed,
    emit_started,
    emit_suspended,
)
from .webhooks import generate_token, verify_token

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
    tenant_id:         str
    flow_id:           str
    trigger_type:      str = "manual"
    session_id:        str | None = None
    # When triggered from an active customer session, pass the session_id here.
    # The worker will use this as the ContextStore key so @ctx.* reads/writes
    # target {tenant}:ctx:{origin_session_id} rather than the workflow UUID.
    origin_session_id: str | None = None
    pool_id:           str | None = None
    context:           dict = Field(default_factory=dict)
    metadata:          dict = Field(default_factory=dict)


_SKILL_ID_RE = re.compile(r"^skill_[a-z0-9_]+$")  # id estável (Skill Versioning Fase A); _v\d+ legado casa o slug


async def _resolve_flow_definition(
    flow_id: str,
    tenant_id: str,
    metadata: dict,
    registry_url: str,
) -> dict:
    """
    If metadata already contains 'flow_definition', return metadata unchanged.
    Otherwise, for skill_* flow IDs, fetch the skill from agent-registry and inject
    the 'flow' field as 'flow_definition' so the skill-flow-worker can execute it.
    """
    if "flow_definition" in metadata:
        return metadata

    if not _SKILL_ID_RE.match(flow_id):
        return metadata  # not a skill — infrastructure flows may have definitions elsewhere

    try:
        url = f"{registry_url}/v1/skills/{flow_id}"
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, headers={"x-tenant-id": tenant_id})
        if resp.status_code == 200:
            skill = resp.json()
            if skill.get("flow"):
                return {**metadata, "flow_definition": dict(skill["flow"])}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not fetch flow_definition for %s from registry: %s", flow_id, exc)

    return metadata


@router.post("/v1/workflow/trigger", status_code=201)
async def trigger_workflow(
    body:    TriggerRequest,
    request: Request,
) -> dict[str, Any]:
    """
    Arc 19 Fase D — proxies to the channel-gateway WebhookAdapter.

    POST /v1/channels/webhook/{flow_id} creates a normal webhook session via
    conversations.inbound Kafka.  The session_id returned by channel-gateway
    is the single persistent identifier for the workflow execution.

    Legacy fields (origin_session_id, pool_id, journey_id, context) are
    forwarded inside metadata so the webhook adapter / orchestrator-bridge can
    read them from pipeline_state.contact_context on first step.
    """
    settings = _settings(request)
    trigger_type = body.trigger_type if body.trigger_type != "manual" else "api"
    metadata: dict = dict(body.metadata)
    if body.context:
        metadata.setdefault("context", body.context)
    if body.origin_session_id:
        metadata.setdefault("origin_session_id", body.origin_session_id)
    if body.pool_id:
        metadata.setdefault("pool_id", body.pool_id)

    gw_url = f"{settings.channel_gateway_url}/v1/channels/webhook/{body.flow_id}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(gw_url, json={
                "tenant_id":    body.tenant_id,
                "trigger_type": trigger_type,
                "metadata":     metadata or None,
                "customer_id":  body.session_id or None,
            })
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            exc.response.status_code,
            f"Channel gateway error: {exc.response.text}",
        ) from exc
    except Exception as exc:
        raise HTTPException(502, f"Channel gateway unreachable: {exc}") from exc


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
    # Optional: absolute ISO-8601 datetime for timer-based suspends (scheduled deploys, etc.)
    # When provided, used directly as resume_expires_at (overrides timeout_hours + business_hours)
    scheduled_at:   str | None = None
    pipeline_state: dict  = Field(default_factory=dict)
    metadata:       dict  = Field(default_factory=dict)


@router.post("/v1/workflow/instances/{instance_id}/persist-suspend", status_code=410)
async def persist_suspend(
    instance_id: str,
    request:     Request,
) -> dict[str, Any]:
    """
    Arc 19 Fase D — deprecated.

    Suspend state is now managed by the channel-gateway WebhookAdapter via
    Redis (status suspended + TTL extension) and the orchestrator-bridge
    persistSuspendWebhook callback.  This endpoint is no longer called.
    """
    raise HTTPException(
        410,
        "Deprecated in Arc 19 Fase D. Suspend is managed by the channel-gateway "
        "WebhookAdapter and orchestrator-bridge persistSuspendWebhook callback.",
    )


# ── Resume ────────────────────────────────────────────────────────────────────

class ResumeRequest(BaseModel):
    token:     str
    decision:  str   # approved | rejected | input | timeout
    payload:   dict  = Field(default_factory=dict)
    # Arc 19 Fase D: when tenant_id is present the token belongs to a webhook
    # session managed by channel-gateway; proxy the call there.
    # When absent fall back to the legacy PostgreSQL-backed path for pre-Arc19
    # workflow instances that still exist in the database.
    tenant_id: str | None = None


@router.post("/v1/workflow/resume", status_code=200)
async def resume_workflow(
    body:    ResumeRequest,
    request: Request,
    pool=Depends(_pool),
) -> dict[str, Any]:
    """
    Resume a suspended workflow / webhook session using its resume_token.

    Arc 19 Fase D behaviour:
    - If body.tenant_id is provided → proxy to channel-gateway WebhookAdapter
      (POST /v1/channels/webhook/resume/{token}).  The session is managed in
      Redis; channel-gateway handles TTL, stream events, and re-allocation.
    - If body.tenant_id is absent → legacy path: look up the WorkflowInstance
      in PostgreSQL, validate the token, publish workflow.resumed to Kafka.
      Kept for backward compatibility with pre-Arc19 instances.
    """
    settings = _settings(request)

    # ── Arc 19: webhook session managed by channel-gateway ────────────────────
    if body.tenant_id:
        gw_url = f"{settings.channel_gateway_url}/v1/channels/webhook/resume/{body.token}"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(gw_url, json={
                    "tenant_id": body.tenant_id,
                    "payload":   {**body.payload, "decision": body.decision},
                })
                resp.raise_for_status()
                return resp.json()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                exc.response.status_code,
                f"Channel gateway error: {exc.response.text}",
            ) from exc
        except Exception as exc:
            raise HTTPException(502, f"Channel gateway unreachable: {exc}") from exc

    # ── Legacy: PostgreSQL-backed WorkflowInstance ────────────────────────────
    producer = _producer(request)

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

    current_step = instance.get("current_step") or "unknown"

    updated = await db_resume_instance(
        pool,
        instance_id=instance["id"],
        pipeline_state=instance["pipeline_state"],
    )
    if not updated:
        raise HTTPException(409, "Resume failed — concurrent update detected")

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
        next_step="__pending_engine__",
        wait_duration_ms=wait_ms,
    )

    return {
        "instance_id":      instance["id"],
        "flow_id":          instance["flow_id"],
        "decision":         body.decision,
        "wait_duration_ms": wait_ms,
        "instance":         updated,
    }


# ── Complete / Fail (called by engine worker) ─────────────────────────────────

class CompleteRequest(BaseModel):
    outcome:        str
    pipeline_state: dict = Field(default_factory=dict)


@router.post("/v1/workflow/instances/{instance_id}/complete", status_code=410)
async def complete_workflow(
    instance_id: str,
    request:     Request,
) -> dict[str, Any]:
    """
    Arc 19 Fase D — deprecated.

    Workflow completion is now signalled via agent_done from the
    orchestrator-bridge, which closes the webhook session normally.
    """
    raise HTTPException(
        410,
        "Deprecated in Arc 19 Fase D. Workflow completion is handled by "
        "agent_done in the orchestrator-bridge.",
    )


class FailRequest(BaseModel):
    error: str


@router.post("/v1/workflow/instances/{instance_id}/fail", status_code=410)
async def fail_workflow(
    instance_id: str,
    request:     Request,
) -> dict[str, Any]:
    """
    Arc 19 Fase D — deprecated.

    Workflow failure is now propagated via session close with
    close_reason=system_error from the orchestrator-bridge.
    """
    raise HTTPException(
        410,
        "Deprecated in Arc 19 Fase D. Workflow failure is signalled via "
        "session close (close_reason=system_error) from the orchestrator-bridge.",
    )


# ── List / Detail ─────────────────────────────────────────────────────────────

@router.get("/v1/workflow/instances")
async def list_instances(
    tenant_id: str,
    status:    str | None = None,   # all | active | suspended | completed | failed | timed_out | cancelled
    flow_id:   str | None = None,
    pool_id:   str | None = None,
    from_dt:   str | None = None,   # ISO date string (inclusive)
    to_dt:     str | None = None,   # ISO date string (inclusive)
    limit:     int = 50,
    offset:    int = 0,
    pool=Depends(_pool),
) -> list[dict]:
    if limit > 200:
        limit = 200
    return await db_list_instances(
        pool, tenant_id, status, flow_id, pool_id, from_dt, to_dt, limit, offset
    )


@router.get("/v1/workflow/instances/{instance_id}/sessions")
async def list_instance_sessions(
    instance_id: str,
    pool=Depends(_pool),
) -> dict[str, Any]:
    """
    Returns all session_ids linked to a workflow instance.

    Includes:
    - origin_session_id: the customer session that triggered the workflow (type='origin')
    - responded_session_id: sessions created when collect steps were responded to (type='collect')

    Used by Analytics/Processes drill-down (Arc 18 B2).
    """
    instance = await db_get_instance(pool, instance_id)
    if not instance:
        raise HTTPException(404, "workflow instance not found")

    sessions = await db_get_instance_sessions(pool, instance_id)
    return {
        "instance_id":  instance_id,
        "session_ids":  [s["session_id"] for s in sessions],
        "sessions":     sessions,
    }


@router.get("/v1/workflow/instances/{instance_id}")
async def get_instance(
    instance_id: str,
    pool=Depends(_pool),
) -> dict[str, Any]:
    instance = await db_get_instance(pool, instance_id)
    if not instance:
        raise HTTPException(404, "workflow instance not found")
    return instance


# ── Cancel — ROTA REMOVIDA em 2026-08-07 (I5, lacuna 4b) ──────────────────────
#
# Era `POST /v1/workflow/instances/{id}/cancel` devolvendo 410 hard, com a
# mensagem *"cancel webhook sessions via the channel-gateway
# (DELETE /v1/channels/webhook/{session_id})"*.
#
# Dois motivos para apagar em vez de manter o 410:
#
#  1. **O substituto que ela nomeava nunca existiu.** O channel-gateway não tem
#     nenhuma rota DELETE: a superfície webhook é POST trigger/resume/pool/
#     collect/delegate + GET …/status. Um 410 que aponta caminho inexistente é
#     pior que ausência — tem cara de decisão tomada, e por isso ninguém foi
#     conferir. (Mesma forma do docstring de `_claim_lease_key`, que citava uma
#     segunda rede inexistente. Ver TODO § "Lacuna 2".)
#  2. **Havia quatro chamadores vivos**, não zero: ProcessosPage,
#     WorkflowsPage, WorkflowMonitorPage e MonitorTab, todas com
#     `catch { alert(String(e)) }` — o operador confirmava o cancelamento e
#     recebia `Error: HTTP 410`. Foram removidas na mesma mudança.
#
# **Não foi reapontada para `/api/force-complete`** porque a medição mostrou que
# não há endereço: esta tabela tem UM escritor (`:794`) e ele grava
# `session_id: None` hardcoded (`:799`) — cobertura 0% por construção. Sonda:
# `infra/test/probe_workflow_cancel_callers.sh`.
#
# Quem precisar encerrar execução parada usa `POST /api/force-complete/{sid}`
# no mcp-server (BFF), que é endereçado por SESSÃO — a unidade do Arc 19.


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
    channel:        str | None = None     # optional — channel-gateway selects by requires[] when absent (Arc 16)
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


@router.post("/v1/workflow/instances/{instance_id}/collect/persist", status_code=410)
async def persist_collect(
    instance_id: str,
    request:     Request,
) -> dict[str, Any]:
    """
    Arc 19 Fase D — deprecated.

    The collect step is now handled by the orchestrator-bridge / channel-gateway
    via the unified session model.  The step suspends the webhook session and
    creates a child contact session through the channel-gateway capability
    negotiation (Arc 16).
    """
    raise HTTPException(
        410,
        "Deprecated in Arc 19 Fase D. Collect steps are handled by the "
        "orchestrator-bridge and channel-gateway capability negotiation.",
    )


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


@router.post("/v1/workflow/collect/respond", status_code=410)
async def respond_collect(
    request: Request,
) -> dict[str, Any]:
    """
    Arc 19 Fase D — deprecated.

    Collect responses are now delivered to the webhook session via the
    channel-gateway WebhookAdapter resume endpoint, which re-allocates the
    skill-flow instance and injects the response into pipeline_state.
    """
    raise HTTPException(
        410,
        "Deprecated in Arc 19 Fase D. Collect responses are routed via "
        "POST /v1/channels/webhook/resume/{token} on the channel-gateway.",
    )


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


# ── Webhook helpers ────────────────────────────────────────────────────────────

def _require_admin(request: Request, x_admin_token: str = Header(default="")):
    """Dependency that validates the admin token for webhook management endpoints."""
    settings = _settings(request)
    if settings.admin_token and x_admin_token != settings.admin_token:
        raise HTTPException(401, "invalid or missing X-Admin-Token")


def _hash_payload(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


# ── Webhook CRUD (admin-protected) ────────────────────────────────────────────

class WebhookCreateRequest(BaseModel):
    tenant_id:        str
    flow_id:          str
    description:      str                      = ""
    context_override: dict                     = Field(default_factory=dict)


@router.post("/v1/workflow/webhooks", status_code=201)
async def create_webhook(
    body:    WebhookCreateRequest,
    request: Request,
    pool=Depends(_pool),
    _admin=Depends(_require_admin),
) -> dict[str, Any]:
    """
    Register a new webhook endpoint that triggers the given flow_id.
    Returns the webhook record including the plain token (shown once — never stored).
    """
    plain_token, token_hash, token_prefix = generate_token()

    webhook = await db_create_webhook(
        pool,
        tenant_id=body.tenant_id,
        flow_id=body.flow_id,
        description=body.description,
        token_hash=token_hash,
        token_prefix=token_prefix,
        context_override=body.context_override,
    )

    # Embed the plain token in the response (only opportunity to show it)
    return {**webhook, "token": plain_token}


@router.get("/v1/workflow/webhooks")
async def list_webhooks(
    tenant_id: str,
    active:    bool | None = None,
    limit:     int = 50,
    offset:    int = 0,
    pool=Depends(_pool),
    _admin=Depends(_require_admin),
) -> list[dict]:
    if limit > 200:
        limit = 200
    return await db_list_webhooks(pool, tenant_id, active, limit, offset)


@router.get("/v1/workflow/webhooks/{webhook_id}")
async def get_webhook(
    webhook_id: str,
    pool=Depends(_pool),
    _admin=Depends(_require_admin),
) -> dict[str, Any]:
    webhook = await db_get_webhook(pool, webhook_id)
    if not webhook:
        raise HTTPException(404, "webhook not found")
    return webhook


class WebhookPatchRequest(BaseModel):
    description:      str  | None = None
    active:           bool | None = None
    context_override: dict | None = None


@router.patch("/v1/workflow/webhooks/{webhook_id}", status_code=200)
async def patch_webhook(
    webhook_id: str,
    body:       WebhookPatchRequest,
    pool=Depends(_pool),
    _admin=Depends(_require_admin),
) -> dict[str, Any]:
    """Update description, active status, or context_override."""
    webhook = await db_get_webhook(pool, webhook_id)
    if not webhook:
        raise HTTPException(404, "webhook not found")

    updated = await db_update_webhook(
        pool, webhook_id,
        description=body.description,
        active=body.active,
        context_override=body.context_override,
    )
    return updated  # type: ignore[return-value]


@router.post("/v1/workflow/webhooks/{webhook_id}/rotate", status_code=200)
async def rotate_webhook_token(
    webhook_id: str,
    pool=Depends(_pool),
    _admin=Depends(_require_admin),
) -> dict[str, Any]:
    """
    Rotate the webhook secret.  Returns the new plain token (shown once).
    The old token is immediately invalidated.
    """
    webhook = await db_get_webhook(pool, webhook_id)
    if not webhook:
        raise HTTPException(404, "webhook not found")

    plain_token, token_hash, token_prefix = generate_token()
    updated = await db_rotate_webhook_token(pool, webhook_id, token_hash, token_prefix)
    return {**updated, "token": plain_token}  # type: ignore[operator]


@router.delete("/v1/workflow/webhooks/{webhook_id}", status_code=204)
async def delete_webhook(
    webhook_id: str,
    pool=Depends(_pool),
    _admin=Depends(_require_admin),
) -> None:
    deleted = await db_delete_webhook(pool, webhook_id)
    if not deleted:
        raise HTTPException(404, "webhook not found")


@router.get("/v1/workflow/webhooks/{webhook_id}/deliveries")
async def list_webhook_deliveries(
    webhook_id: str,
    limit:      int = 50,
    pool=Depends(_pool),
    _admin=Depends(_require_admin),
) -> list[dict]:
    """Last N delivery records for a webhook (most recent first)."""
    webhook = await db_get_webhook(pool, webhook_id)
    if not webhook:
        raise HTTPException(404, "webhook not found")
    if limit > 200:
        limit = 200
    return await db_list_deliveries(pool, webhook_id, limit)


# ── Webhook public trigger ─────────────────────────────────────────────────────

@router.post("/v1/workflow/webhook/{webhook_id}", status_code=202)
async def trigger_via_webhook(
    webhook_id:      str,
    request:         Request,
    pool=Depends(_pool),
    x_webhook_token: str = Header(default=""),
) -> dict[str, Any]:
    """
    Public trigger endpoint called by external systems (Salesforce, ERP, etc.).

    Authentication:
      Header X-Webhook-Token: <plain_token>

    The request body (any JSON) is merged with context_override and passed as
    pipeline_state.contact_context to the new WorkflowInstance.

    Returns 202 Accepted with { instance_id, flow_id, webhook_id }.
    Logs a delivery record regardless of outcome.
    """
    settings  = _settings(request)
    producer  = _producer(request)
    t0        = time.monotonic()

    # Read raw body once (for payload hash)
    raw_body  = await request.body()
    payload_hash = _hash_payload(raw_body)

    # ── Authenticate ──────────────────────────────────────────────────────────
    if not x_webhook_token:
        raise HTTPException(401, "X-Webhook-Token header is required")

    token_hash = hashlib.sha256(x_webhook_token.encode()).hexdigest()
    webhook    = await db_get_webhook_by_token_hash(pool, token_hash)

    if not webhook:
        # Log failed delivery (no webhook_id or tenant_id available — use placeholder)
        # We can't call db_record_delivery because we don't have a valid webhook_id UUID.
        # Just raise immediately.
        raise HTTPException(401, "invalid webhook token")

    if not verify_token(x_webhook_token, token_hash):
        # Extra constant-time guard (token_hash lookup already confirmed match,
        # but belt-and-suspenders against hash-lookup collisions).
        raise HTTPException(401, "invalid webhook token")

    if not webhook["active"]:
        latency = int((time.monotonic() - t0) * 1000)
        await db_record_delivery(
            pool,
            webhook_id=webhook["id"],
            tenant_id=webhook["tenant_id"],
            status_code=403,
            payload_hash=payload_hash,
            error="webhook is inactive",
            latency_ms=latency,
        )
        raise HTTPException(403, "webhook is inactive")

    # ── Parse body as JSON context (best-effort — empty dict on failure) ──────
    import json as _json
    try:
        body_json: dict = _json.loads(raw_body) if raw_body else {}
        if not isinstance(body_json, dict):
            body_json = {"payload": body_json}
    except Exception:
        body_json = {}

    # Merge context_override (webhook-level defaults) with inbound payload
    context = {**webhook["context_override"], **body_json}

    # ── Create workflow instance ──────────────────────────────────────────────
    instance = await db_create_instance(pool, {
        "installation_id":   settings.installation_id,
        "organization_id":   settings.organization_id,
        "tenant_id":         webhook["tenant_id"],
        "flow_id":           webhook["flow_id"],
        "session_id":        None,
        "origin_session_id": None,
        "pool_id":           None,
        "metadata":          {"webhook_id": webhook["id"], "webhook_trigger": True},
        "pipeline_state":    {"contact_context": context},
    })

    await emit_started(
        producer, settings.kafka_topic,
        installation_id=settings.installation_id,
        organization_id=settings.organization_id,
        tenant_id=webhook["tenant_id"],
        instance_id=instance["id"],
        flow_id=webhook["flow_id"],
        session_id=None,
        trigger_type="webhook",
    )

    latency = int((time.monotonic() - t0) * 1000)
    await db_record_delivery(
        pool,
        webhook_id=webhook["id"],
        tenant_id=webhook["tenant_id"],
        status_code=202,
        payload_hash=payload_hash,
        instance_id=instance["id"],
        latency_ms=latency,
    )

    logger.info(
        "webhook trigger: webhook_id=%s flow_id=%s instance_id=%s latency_ms=%d",
        webhook["id"], webhook["flow_id"], instance["id"], latency,
    )

    return {
        "instance_id": instance["id"],
        "flow_id":     webhook["flow_id"],
        "webhook_id":  webhook["id"],
        "status":      "accepted",
    }


# ── Admin: historical backfill ────────────────────────────────────────────────

@router.post("/admin/backfill-events")
async def backfill_events(
    request:        Request,
    tenant_id:      str = Query(..., description="Tenant to backfill"),
    limit_per_page: int = Query(1000, description="Instances per page"),
    _auth:          None = Depends(_require_admin),
    pool=Depends(_pool),
):
    """
    Re-emits synthetic workflow.* Kafka events for all historical instances
    of the given tenant that are in PostgreSQL but not yet in ClickHouse.

    Safe to run multiple times: ReplacingMergeTree deduplicates by
    (tenant_id, instance_id, timestamp).

    Returns { instances_processed, events_emitted }.
    """
    settings = _settings(request)
    producer = _producer(request)

    # Status → terminal Kafka event_type
    _TERMINAL = {
        "completed": "workflow.completed",
        "failed":    "workflow.failed",
        "timed_out": "workflow.timed_out",
        "cancelled": "workflow.cancelled",
    }

    instances_processed = 0
    events: list[dict] = []
    offset = 0

    while True:
        page = await db_list_instances(
            pool,
            tenant_id=tenant_id,
            limit=limit_per_page,
            offset=offset,
        )
        if not page:
            break

        for inst in page:
            iid        = inst["id"]
            fid        = inst["flow_id"]
            tid        = inst["tenant_id"]
            created_at = inst.get("created_at") or ""
            completed_at = inst.get("completed_at") or created_at
            status     = inst.get("status", "")

            # workflow.started — always emit
            events.append({
                "event_type":      "workflow.started",
                "timestamp":       created_at,
                "installation_id": inst.get("installation_id") or settings.installation_id,
                "organization_id": inst.get("organization_id") or settings.organization_id,
                "tenant_id":       tid,
                "instance_id":     iid,
                "flow_id":         fid,
                "session_id":      inst.get("session_id"),
                "trigger_type":    "backfill",
                "campaign_id":     inst.get("campaign_id"),
                "pool_id":         inst.get("pool_id"),
            })

            # terminal event
            terminal_type = _TERMINAL.get(status)
            if terminal_type:
                ts = completed_at or created_at
                ev: dict = {
                    "event_type":  terminal_type,
                    "timestamp":   ts,
                    "tenant_id":   tid,
                    "instance_id": iid,
                    "flow_id":     fid,
                }
                if inst.get("campaign_id"):
                    ev["campaign_id"] = inst["campaign_id"]

                if terminal_type == "workflow.completed":
                    try:
                        from datetime import datetime as _dt
                        t0 = _dt.fromisoformat(created_at.replace("Z", "+00:00"))
                        t1 = _dt.fromisoformat(ts.replace("Z", "+00:00"))
                        ev["duration_ms"] = int((t1 - t0).total_seconds() * 1000)
                    except Exception:
                        ev["duration_ms"] = 0
                    ev["outcome"] = inst.get("outcome") or "unknown"

                elif terminal_type == "workflow.failed":
                    ev["current_step"] = None
                    ev["error"]        = "backfill"

                elif terminal_type == "workflow.timed_out":
                    ev["current_step"] = None
                    ev["suspended_at"] = None
                    ev["next_open"]    = None

                elif terminal_type == "workflow.cancelled":
                    ev["cancelled_by"] = "backfill"
                    ev["reason"]       = None

                events.append(ev)

            instances_processed += 1

        offset += len(page)
        if len(page) < limit_per_page:
            break

    emitted = await emit_events_batch(producer, settings.kafka_topic, events)

    logger.info(
        "backfill-events: tenant=%s instances_processed=%d events_emitted=%d",
        tenant_id, instances_processed, emitted,
    )
    return {
        "instances_processed": instances_processed,
        "events_emitted":      emitted,
    }
