"""
router.py
FastAPI router for evaluation-api.

Endpoints:
  Forms:
    GET    /v1/evaluation/forms                   list forms
    POST   /v1/evaluation/forms                   create form
    GET    /v1/evaluation/forms/{id}              get form
    PUT    /v1/evaluation/forms/{id}              update form
    DELETE /v1/evaluation/forms/{id}              delete form (→ archived)

  Campaigns:
    GET    /v1/evaluation/campaigns               list campaigns
    POST   /v1/evaluation/campaigns               create campaign
    GET    /v1/evaluation/campaigns/{id}          get campaign
    PUT    /v1/evaluation/campaigns/{id}          update campaign
    POST   /v1/evaluation/campaigns/{id}/pause    pause
    POST   /v1/evaluation/campaigns/{id}/resume   resume

  Instances:
    GET    /v1/evaluation/instances               list instances
    POST   /v1/evaluation/instances               manual trigger
    GET    /v1/evaluation/instances/{id}          get instance
    POST   /v1/evaluation/instances/claim         evaluator claims next
    POST   /v1/evaluation/instances/{id}/expire   admin: force expire

  Results:
    GET    /v1/evaluation/results                 list results
    GET    /v1/evaluation/results/{id}            get result
    GET    /v1/evaluation/results/{id}/criteria   get criterion responses
    POST   /v1/evaluation/results/{id}/review     reviewer submits review
    POST   /v1/evaluation/results/{id}/lock       admin lock

  Contestations:
    GET    /v1/evaluation/contestations           list contestations
    POST   /v1/evaluation/contestations           file contestation
    GET    /v1/evaluation/contestations/{id}      get contestation
    POST   /v1/evaluation/contestations/{id}/adjudicate  supervisor adjudicates

  Sampling:
    POST   /v1/evaluation/sample                  check if session should be sampled

  Reports:
    GET    /v1/evaluation/reports/campaign/{id}   campaign report
    GET    /v1/evaluation/reports/agent           agent performance report

  Internal (called by evaluation_submit MCP tool):
    POST   /v1/evaluation/ingest                  ingest EvaluationResult from agent

  Health:
    GET    /health
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import asyncpg
import httpx
import jwt as pyjwt
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import settings
from . import db as _db
from . import kafka_emitter as _kafka
from .sampling import should_sample, compute_expires_at, compute_priority

logger = logging.getLogger("plughub.evaluation.router")

router = APIRouter()


# ─── Auth helpers ─────────────────────────────────────────────────────────────

def _require_admin(request: Request) -> None:
    if not settings.admin_token:
        return
    token = request.headers.get("x-admin-token", "")
    if token != settings.admin_token:
        raise HTTPException(status_code=401, detail="unauthorized")


def _decode_jwt(request: Request) -> dict[str, Any]:
    """Decode HS256 Bearer JWT; return payload with at least 'sub' and 'roles'."""
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing Bearer token")
    token = auth[7:]
    try:
        payload = pyjwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except pyjwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail=f"invalid token: {exc}")
    if "sub" not in payload:
        raise HTTPException(status_code=401, detail="token missing 'sub' claim")
    return payload


# ─── DB / infra accessors ─────────────────────────────────────────────────────

def _pool(request: Request) -> asyncpg.Pool:
    return request.app.state.db_pool


def _kafka_producer(request: Request) -> Any:
    return request.app.state.kafka_producer


def _redis(request: Request) -> Any:
    return request.app.state.redis


# ─── ContextStore / Workflow helpers ──────────────────────────────────────────

async def _write_ctx(redis_client: Any, tenant_id: str, session_id: str, fields: dict[str, Any]) -> None:
    """Fire-and-forget write to ContextStore hash {tenant}:ctx:{session_id}."""
    try:
        key = f"{tenant_id}:ctx:{session_id}"
        now = datetime.now(timezone.utc).isoformat()
        pipe = redis_client.pipeline()
        for tag, value in fields.items():
            entry = json.dumps({
                "value": value,
                "confidence": 1.0,
                "source": "evaluation-api",
                "visibility": "agents_only",
                "updated_at": now,
            })
            pipe.hset(key, tag, entry)
        pipe.expire(key, settings.workflow_context_ttl_s)
        await pipe.execute()
    except Exception as exc:
        logger.warning("ContextStore write failed (non-fatal): %s", exc)


async def _resume_workflow(resume_token: str, tenant_id: str) -> None:
    """POST /v1/workflow/resume to workflow-api (fire-and-forget)."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{settings.workflow_api_url}/v1/workflow/resume",
                json={"token": resume_token, "decision": "input", "tenant_id": tenant_id},
            )
            if resp.status_code >= 400:
                logger.warning("workflow resume returned %s: %s", resp.status_code, resp.text)
    except Exception as exc:
        logger.warning("workflow resume HTTP call failed (non-fatal): %s", exc)


async def _compute_available_actions(
    pool: asyncpg.Pool,
    result: dict[str, Any],
    caller_user_id: str | None,
    pool_id: str | None,
) -> list[str]:
    """Compute available_actions server-side — never trust the client."""
    if result.get("eval_status") == "locked":
        return []
    action_required = result.get("action_required")
    if not action_required or not caller_user_id:
        return []

    perms = await _db.resolve_permissions(
        pool,
        result["tenant_id"],
        caller_user_id,
        campaign_id=result.get("campaign_id"),
        pool_id=pool_id,
    )

    if action_required == "review" and "review" in perms:
        return ["review"]
    if action_required == "contestation" and "contest" in perms:
        return ["contest"]
    return []


# ─── Health ───────────────────────────────────────────────────────────────────

@router.get("/health")
async def health(request: Request) -> dict:
    return {"status": "ok", "service": "evaluation-api"}


# ─── Forms ────────────────────────────────────────────────────────────────────

class FormCreate(BaseModel):
    tenant_id:        str
    name:             str
    description:      str = ""
    dimensions:       list[dict] = Field(default_factory=list)
    total_weight:     float = 1.0
    passing_score:    float | None = None
    allow_na:         bool = True
    knowledge_domains: list[str] = Field(default_factory=list)
    created_by:       str = "operator"


class FormUpdate(BaseModel):
    name:             str | None = None
    description:      str | None = None
    dimensions:       list[dict] | None = None
    total_weight:     float | None = None
    passing_score:    float | None = None
    allow_na:         bool | None = None
    knowledge_domains: list[str] | None = None
    status:           str | None = None


@router.get("/v1/evaluation/forms")
async def list_forms(
    request: Request,
    tenant_id: str,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    pool = _pool(request)
    rows = await _db.list_forms(pool, tenant_id, status=status, limit=limit, offset=offset)
    return {"tenant_id": tenant_id, "forms": rows, "count": len(rows)}


@router.post("/v1/evaluation/forms", status_code=201)
async def create_form(body: FormCreate, request: Request) -> dict:
    pool = _pool(request)
    row = await _db.create_form(pool, **body.model_dump())
    return row


@router.get("/v1/evaluation/forms/{form_id}")
async def get_form(form_id: str, tenant_id: str, request: Request) -> dict:
    pool = _pool(request)
    row = await _db.get_form(pool, form_id, tenant_id)
    if not row:
        raise HTTPException(404, detail="form not found")
    return row


@router.put("/v1/evaluation/forms/{form_id}")
async def update_form(form_id: str, tenant_id: str, body: FormUpdate, request: Request) -> dict:
    pool = _pool(request)
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    row = await _db.update_form(pool, form_id, tenant_id, **updates)
    if not row:
        raise HTTPException(404, detail="form not found")
    return row


@router.delete("/v1/evaluation/forms/{form_id}", status_code=204)
async def delete_form(form_id: str, tenant_id: str, request: Request) -> None:
    pool = _pool(request)
    # Soft-delete via archive
    row = await _db.update_form(pool, form_id, tenant_id, status="archived")
    if not row:
        raise HTTPException(404, detail="form not found")


# ─── Campaigns ────────────────────────────────────────────────────────────────

class CampaignCreate(BaseModel):
    tenant_id:                  str
    name:                       str
    description:                str = ""
    form_id:                    str
    pool_id:                    str
    sampling_rules:             dict = Field(default_factory=dict)
    reviewer_rules:             dict = Field(default_factory=dict)
    schedule:                   dict = Field(default_factory=dict)
    # Arc 6 v2 — workflow motor for contestation/review cycle
    review_workflow_skill_id:   str | None = None   # e.g. "skill_revisao_treplica_v1"
    contestation_policy:        dict = Field(default_factory=dict)
    created_by:                 str = "operator"


class CampaignUpdate(BaseModel):
    name:                       str | None = None
    description:                str | None = None
    status:                     str | None = None
    sampling_rules:             dict | None = None
    reviewer_rules:             dict | None = None
    schedule:                   dict | None = None
    review_workflow_skill_id:   str | None = None
    contestation_policy:        dict | None = None


@router.get("/v1/evaluation/campaigns")
async def list_campaigns(
    request: Request,
    tenant_id: str,
    pool_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    pool = _pool(request)
    rows = await _db.list_campaigns(pool, tenant_id, pool_id=pool_id, status=status, limit=limit, offset=offset)
    return {"tenant_id": tenant_id, "campaigns": rows, "count": len(rows)}


@router.post("/v1/evaluation/campaigns", status_code=201)
async def create_campaign(body: CampaignCreate, request: Request) -> dict:
    pool = _pool(request)
    # Validate form exists
    form = await _db.get_form(pool, body.form_id, body.tenant_id)
    if not form:
        raise HTTPException(400, detail=f"form {body.form_id} not found for tenant")
    data = body.model_dump()
    # Persist new v2 fields separately (update_campaign handles jsonb fields)
    row = await _db.create_campaign(
        pool,
        tenant_id=data["tenant_id"],
        name=data["name"],
        description=data.get("description", ""),
        form_id=data["form_id"],
        pool_id=data["pool_id"],
        sampling_rules=data.get("sampling_rules"),
        reviewer_rules=data.get("reviewer_rules"),
        schedule=data.get("schedule"),
        created_by=data.get("created_by", "operator"),
    )
    # Patch in new v2 fields if provided
    v2_updates: dict[str, Any] = {}
    if data.get("review_workflow_skill_id"):
        v2_updates["review_workflow_skill_id"] = data["review_workflow_skill_id"]
    if data.get("contestation_policy"):
        v2_updates["contestation_policy"] = data["contestation_policy"]
    if v2_updates:
        row = await _db.update_campaign(pool, row["id"], body.tenant_id, **v2_updates) or row
    return row


@router.get("/v1/evaluation/campaigns/{campaign_id}")
async def get_campaign(campaign_id: str, tenant_id: str, request: Request) -> dict:
    pool = _pool(request)
    row = await _db.get_campaign(pool, campaign_id, tenant_id)
    if not row:
        raise HTTPException(404, detail="campaign not found")
    return row


@router.put("/v1/evaluation/campaigns/{campaign_id}")
async def update_campaign(campaign_id: str, tenant_id: str, body: CampaignUpdate, request: Request) -> dict:
    pool = _pool(request)
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    row = await _db.update_campaign(pool, campaign_id, tenant_id, **updates)
    if not row:
        raise HTTPException(404, detail="campaign not found")
    return row


@router.post("/v1/evaluation/campaigns/{campaign_id}/pause")
async def pause_campaign(campaign_id: str, tenant_id: str, request: Request) -> dict:
    pool = _pool(request)
    row = await _db.update_campaign(pool, campaign_id, tenant_id, status="paused")
    if not row:
        raise HTTPException(404, detail="campaign not found")
    return row


@router.post("/v1/evaluation/campaigns/{campaign_id}/resume")
async def resume_campaign(campaign_id: str, tenant_id: str, request: Request) -> dict:
    pool = _pool(request)
    row = await _db.update_campaign(pool, campaign_id, tenant_id, status="active")
    if not row:
        raise HTTPException(404, detail="campaign not found")
    return row


# ─── Instances ────────────────────────────────────────────────────────────────

class InstanceCreate(BaseModel):
    tenant_id:   str
    campaign_id: str
    session_id:  str
    segment_id:  str | None = None
    priority:    int = 5


class InstanceClaim(BaseModel):
    tenant_id:          str
    campaign_id:        str | None = None
    evaluator_agent_id: str | None = None


@router.get("/v1/evaluation/instances")
async def list_instances(
    request: Request,
    tenant_id: str,
    campaign_id: str | None = None,
    status: str | None = None,
    session_id: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    pool = _pool(request)
    rows = await _db.list_instances(pool, tenant_id, campaign_id=campaign_id,
                                     status=status, session_id=session_id,
                                     limit=limit, offset=offset)
    return {"tenant_id": tenant_id, "instances": rows, "count": len(rows)}


@router.post("/v1/evaluation/instances", status_code=201)
async def create_instance(body: InstanceCreate, request: Request) -> dict:
    pool = _pool(request)
    producer = _kafka_producer(request)

    campaign = await _db.get_campaign(pool, body.campaign_id, body.tenant_id)
    if not campaign:
        raise HTTPException(400, detail="campaign not found")
    if campaign["status"] not in ("active", "draft"):
        raise HTTPException(400, detail=f"campaign status={campaign['status']}, cannot create instances")

    expires_at = await compute_expires_at(campaign, settings.calendar_api_url,
                                           default_ttl_hours=settings.default_instance_ttl_hours)
    row = await _db.create_instance(
        pool,
        tenant_id=body.tenant_id,
        campaign_id=body.campaign_id,
        form_id=campaign["form_id"],
        session_id=body.session_id,
        segment_id=body.segment_id,
        priority=body.priority,
        expires_at=expires_at,
    )
    await _kafka.emit_instance_created(
        producer, settings.evaluation_topic,
        instance_id=row["id"],
        tenant_id=row["tenant_id"],
        session_id=row["session_id"],
        campaign_id=row["campaign_id"],
        form_id=row["form_id"],
        priority=row["priority"],
        expires_at=expires_at.isoformat() if expires_at else None,
    )
    return row


@router.get("/v1/evaluation/instances/{instance_id}")
async def get_instance(instance_id: str, tenant_id: str, request: Request) -> dict:
    pool = _pool(request)
    row = await _db.get_instance(pool, instance_id, tenant_id)
    if not row:
        raise HTTPException(404, detail="instance not found")
    return row


@router.post("/v1/evaluation/instances/claim")
async def claim_instance(body: InstanceClaim, request: Request) -> dict:
    pool = _pool(request)
    producer = _kafka_producer(request)
    row = await _db.claim_next_instance(
        pool,
        body.tenant_id,
        campaign_id=body.campaign_id,
        evaluator_agent_id=body.evaluator_agent_id,
    )
    if not row:
        raise HTTPException(404, detail="no schedulable instance available")
    await _kafka.emit_instance_assigned(
        producer, settings.evaluation_topic,
        instance_id=row["id"],
        tenant_id=row["tenant_id"],
        session_id=row["session_id"],
        evaluator_agent_id=row.get("evaluator_agent_id"),
    )
    return row


@router.post("/v1/evaluation/instances/{instance_id}/expire", status_code=204)
async def expire_instance(instance_id: str, tenant_id: str, request: Request) -> None:
    _require_admin(request)
    pool = _pool(request)
    producer = _kafka_producer(request)
    row = await _db.update_instance_status(pool, instance_id, tenant_id, "expired")
    if not row:
        raise HTTPException(404, detail="instance not found")
    await _kafka.emit_instance_expired(
        producer, settings.evaluation_topic,
        instance_id=row["id"],
        tenant_id=row["tenant_id"],
        session_id=row["session_id"],
        campaign_id=row["campaign_id"],
    )


# ─── Ingest (from evaluation_submit MCP tool) ─────────────────────────────────

class IngestBody(BaseModel):
    """Called by evaluation_submit when instance_id is present."""
    tenant_id:          str
    instance_id:        str
    session_id:         str
    campaign_id:        str
    form_id:            str
    evaluator_agent_id: str
    overall_score:      float | None = None
    max_score:          float | None = None
    normalized_score:   float | None = None
    passed:             bool | None = None
    eval_status:        str = "submitted"
    evaluator_notes:    str = ""
    comparison_mode:    bool = False
    comparison_report:  dict | None = None
    knowledge_snippets: list[dict] = Field(default_factory=list)
    criterion_responses: list[dict] = Field(default_factory=list)


@router.post("/v1/evaluation/ingest", status_code=201)
async def ingest_result(body: IngestBody, request: Request) -> dict:
    pool = _pool(request)
    producer = _kafka_producer(request)

    # Verify instance exists
    instance = await _db.get_instance(pool, body.instance_id, body.tenant_id)
    if not instance:
        raise HTTPException(404, detail=f"instance {body.instance_id} not found")

    # Create result
    result = await _db.create_result(
        pool,
        tenant_id=body.tenant_id,
        instance_id=body.instance_id,
        session_id=body.session_id,
        campaign_id=body.campaign_id,
        form_id=body.form_id,
        evaluator_agent_id=body.evaluator_agent_id,
        overall_score=body.overall_score,
        max_score=body.max_score,
        normalized_score=body.normalized_score,
        passed=body.passed,
        eval_status=body.eval_status,
        evaluator_notes=body.evaluator_notes,
        comparison_mode=body.comparison_mode,
        comparison_report=body.comparison_report,
        knowledge_snippets=body.knowledge_snippets,
    )

    # Create criterion responses
    criteria_rows: list[dict] = []
    if body.criterion_responses:
        criteria_rows = await _db.create_criterion_responses(
            pool,
            result["id"], body.instance_id, body.campaign_id, body.tenant_id,
            body.criterion_responses,
        )

    # Emit Kafka
    await _kafka.emit_instance_completed(
        producer, settings.evaluation_topic,
        instance_id=body.instance_id,
        result_id=result["id"],
        tenant_id=body.tenant_id,
        session_id=body.session_id,
        campaign_id=body.campaign_id,
        overall_score=body.overall_score,
        passed=body.passed,
        eval_status=body.eval_status,
    )

    return {
        "result_id":               result["id"],
        "instance_id":             body.instance_id,
        "criteria_rows_created":   len(criteria_rows),
        "eval_status":             body.eval_status,
    }


# ─── Results ──────────────────────────────────────────────────────────────────

class ReviewBody(BaseModel):
    decision:      str    # "approved" | "rejected"
    round:         int    # anti-replay: must equal result.current_round
    review_note:   str = ""


class LockBody(BaseModel):
    locked_by:   str = "operator"
    lock_reason: str = "manual"


@router.get("/v1/evaluation/results")
async def list_results(
    request: Request,
    tenant_id: str,
    campaign_id: str | None = None,
    session_id: str | None = None,
    eval_status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    pool = _pool(request)
    rows = await _db.list_results(pool, tenant_id, campaign_id=campaign_id,
                                   session_id=session_id, eval_status=eval_status,
                                   limit=limit, offset=offset)
    return {"tenant_id": tenant_id, "results": rows, "count": len(rows)}


@router.get("/v1/evaluation/results/{result_id}")
async def get_result(
    result_id: str,
    tenant_id: str,
    request: Request,
    caller_user_id: str | None = None,
) -> dict:
    """
    Returns the result with server-computed `available_actions`.
    Pass ?caller_user_id=<user_id> to get personalized button state.
    The UI should never compute permissions locally — use this field only.
    """
    pool = _pool(request)
    row = await _db.get_result(pool, result_id, tenant_id)
    if not row:
        raise HTTPException(404, detail="result not found")

    # Compute available_actions server-side
    pool_id: str | None = None
    if row.get("campaign_id"):
        campaign = await _db.get_campaign(pool, row["campaign_id"], tenant_id)
        pool_id = campaign.get("pool_id") if campaign else None

    available_actions = await _compute_available_actions(pool, row, caller_user_id, pool_id)

    result_with_actions = dict(row)
    result_with_actions["available_actions"] = available_actions
    if available_actions and row.get("deadline_at"):
        result_with_actions["action_context"] = {
            "deadline_at":     row["deadline_at"].isoformat() if hasattr(row["deadline_at"], "isoformat") else row["deadline_at"],
            "round":           row.get("current_round", 0),
        }
    return result_with_actions


@router.get("/v1/evaluation/results/{result_id}/criteria")
async def get_criteria(result_id: str, tenant_id: str, request: Request) -> dict:
    pool = _pool(request)
    rows = await _db.list_criterion_responses(pool, result_id, tenant_id)
    return {"result_id": result_id, "criterion_responses": rows, "count": len(rows)}


@router.post("/v1/evaluation/results/{result_id}/review")
async def review_result(result_id: str, tenant_id: str, body: ReviewBody, request: Request) -> dict:
    """
    Human reviewer approves or rejects an evaluation result.
    Requires: Bearer JWT with sub=user_id + can_review permission for campaign/pool.
    Anti-replay: body.round must equal result.current_round (409 on mismatch).
    """
    pool = _pool(request)
    redis_client = _redis(request)

    # Identity from JWT
    jwt_payload = _decode_jwt(request)
    caller_user_id: str = jwt_payload["sub"]

    allowed_decisions = {"approved", "rejected"}
    if body.decision not in allowed_decisions:
        raise HTTPException(400, detail=f"decision must be one of {allowed_decisions}")

    # Load result and guard
    result = await _db.get_result(pool, result_id, tenant_id)
    if not result:
        raise HTTPException(404, detail="result not found")
    if result["eval_status"] == "locked":
        raise HTTPException(409, detail="result is locked, no further actions allowed")

    # Anti-replay: round must match current workflow round
    if body.round != result.get("current_round", 0):
        raise HTTPException(
            409,
            detail=f"round mismatch: expected {result.get('current_round', 0)}, got {body.round}",
        )

    # Verify permission
    campaign = await _db.get_campaign(pool, result["campaign_id"], tenant_id) if result.get("campaign_id") else None
    pool_id = campaign.get("pool_id") if campaign else None
    perms = await _db.resolve_permissions(pool, tenant_id, caller_user_id,
                                          campaign_id=result.get("campaign_id"), pool_id=pool_id)
    if "review" not in perms:
        raise HTTPException(403, detail="caller lacks 'review' permission for this campaign/pool")

    # Persist decision
    row = await _db.update_result(
        pool, result_id, tenant_id,
        eval_status="reviewed",
        reviewer_agent_id=caller_user_id,
        reviewer_outcome=body.decision,
        reviewer_notes=body.review_note,
        reviewed_at=datetime.now(tz=timezone.utc),
    )
    if not row:
        raise HTTPException(404, detail="result not found")

    # Write to ContextStore so the suspended workflow YAML choice step can branch on it
    if result.get("session_id"):
        await _write_ctx(redis_client, tenant_id, result["session_id"], {
            "session.review_decision": body.decision,
            "session.reviewer_id":     caller_user_id,
            "session.round_echoed":    body.round,
        })

    # Resume workflow (fire-and-forget)
    if result.get("resume_token"):
        await _resume_workflow(result["resume_token"], tenant_id)

    return row


@router.post("/v1/evaluation/results/{result_id}/lock")
async def lock_result_endpoint(result_id: str, body: LockBody, request: Request) -> dict:
    """
    Permanently lock a result. Called by:
    - Admin operators (X-Admin-Token) for manual locks
    - evaluation_lock MCP tool (called from congelar_resultado workflow step, no admin token)
    Returns 409 if result is already locked (idempotent for workflow retries).
    """
    pool = _pool(request)
    # Allow workflow calls without admin token; admin token gates manual/admin locks
    # (no hard auth requirement — the endpoint is internal, firewall-protected in production)
    row = await _db.lock_result(
        pool, result_id,
        lock_reason=body.lock_reason,
        locked_by=body.locked_by,
    )
    if row is None:
        # lock_result's WHERE includes eval_status != 'locked', so None means
        # either result doesn't exist, or it's already locked — check which:
        existing = await _db.get_result_by_id(pool, result_id)
        if existing is None:
            raise HTTPException(404, detail="result not found")
        raise HTTPException(409, detail="result is already locked")
    return row


# ─── Contestations ────────────────────────────────────────────────────────────

class ContestationCreate(BaseModel):
    tenant_id:            str
    result_id:            str
    instance_id:          str
    session_id:           str
    contestation_reason:  str = ""
    round:                int = 0   # anti-replay: must equal result.current_round


class AdjudicateBody(BaseModel):
    adjudicated_by:    str
    status:            str   # "accepted" | "rejected" | "withdrawn"
    adjudication_notes: str = ""
    adjusted_score:    float | None = None


@router.get("/v1/evaluation/contestations")
async def list_contestations(
    request: Request,
    tenant_id: str,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    pool = _pool(request)
    rows = await _db.list_contestations(pool, tenant_id, status=status, limit=limit, offset=offset)
    return {"tenant_id": tenant_id, "contestations": rows, "count": len(rows)}


@router.post("/v1/evaluation/contestations", status_code=201)
async def create_contestation(body: ContestationCreate, request: Request) -> dict:
    """
    File a contestation on an evaluation result.
    Requires: Bearer JWT with sub=user_id + can_contest permission for campaign/pool.
    Anti-replay: body.round must equal result.current_round (409 on mismatch).
    """
    pool = _pool(request)
    producer = _kafka_producer(request)
    redis_client = _redis(request)

    # Identity from JWT
    jwt_payload = _decode_jwt(request)
    caller_user_id: str = jwt_payload["sub"]

    # Validate result
    result = await _db.get_result(pool, body.result_id, body.tenant_id)
    if not result:
        raise HTTPException(404, detail="result not found")
    if result["eval_status"] == "locked":
        raise HTTPException(409, detail="result is locked, cannot contest")

    # Anti-replay
    if body.round != result.get("current_round", 0):
        raise HTTPException(
            409,
            detail=f"round mismatch: expected {result.get('current_round', 0)}, got {body.round}",
        )

    # Permission check
    campaign = await _db.get_campaign(pool, result["campaign_id"], body.tenant_id) if result.get("campaign_id") else None
    pool_id = campaign.get("pool_id") if campaign else None
    perms = await _db.resolve_permissions(pool, body.tenant_id, caller_user_id,
                                          campaign_id=result.get("campaign_id"), pool_id=pool_id)
    if "contest" not in perms:
        raise HTTPException(403, detail="caller lacks 'contest' permission for this campaign/pool")

    row = await _db.create_contestation(
        pool,
        tenant_id=body.tenant_id,
        result_id=body.result_id,
        instance_id=body.instance_id,
        session_id=body.session_id,
        contested_by=caller_user_id,
        contestation_reason=body.contestation_reason,
    )

    # Write to ContextStore so workflow choice step can see "contested"
    if result.get("session_id"):
        await _write_ctx(redis_client, body.tenant_id, result["session_id"], {
            "session.review_decision": "contested",
            "session.reviewer_id":     caller_user_id,
            "session.round_echoed":    body.round,
        })

    # Resume workflow (fire-and-forget)
    if result.get("resume_token"):
        await _resume_workflow(result["resume_token"], body.tenant_id)

    await _kafka.emit_contestation_opened(
        producer, settings.evaluation_topic,
        contestation_id=row["id"],
        result_id=body.result_id,
        instance_id=body.instance_id,
        tenant_id=body.tenant_id,
        session_id=body.session_id,
        contested_by=caller_user_id,
    )
    return row


@router.get("/v1/evaluation/contestations/{contestation_id}")
async def get_contestation(contestation_id: str, tenant_id: str, request: Request) -> dict:
    pool = _pool(request)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM evaluation.contestations WHERE id=$1 AND tenant_id=$2",
            contestation_id, tenant_id,
        )
    if not row:
        raise HTTPException(404, detail="contestation not found")
    return dict(row)


@router.post("/v1/evaluation/contestations/{contestation_id}/adjudicate")
async def adjudicate(contestation_id: str, tenant_id: str, body: AdjudicateBody, request: Request) -> dict:
    pool = _pool(request)
    producer = _kafka_producer(request)
    allowed = {"accepted", "rejected", "withdrawn"}
    if body.status not in allowed:
        raise HTTPException(400, detail=f"status must be one of {allowed}")
    row = await _db.adjudicate_contestation(
        pool, contestation_id, tenant_id,
        status=body.status,
        adjudicated_by=body.adjudicated_by,
        adjudication_notes=body.adjudication_notes,
        adjusted_score=body.adjusted_score,
    )
    if not row:
        raise HTTPException(404, detail="contestation not found")
    await _kafka.emit_contestation_closed(
        producer, settings.evaluation_topic,
        contestation_id=contestation_id,
        result_id=row["result_id"],
        tenant_id=tenant_id,
        adjudicated_status=body.status,
        adjudicated_by=body.adjudicated_by,
    )
    return row


# ─── Permissions (2D: user × pool|campaign|global) ────────────────────────────

class PermissionCreate(BaseModel):
    tenant_id:    str
    user_id:      str
    scope_type:   str       # "pool" | "campaign" | "global"
    scope_id:     str | None = None
    can_contest:  bool = False
    can_review:   bool = False
    granted_by:   str = "operator"


class PermissionUpdate(BaseModel):
    can_contest:  bool | None = None
    can_review:   bool | None = None


@router.get("/v1/evaluation/permissions")
async def list_permissions(
    request: Request,
    tenant_id: str,
    user_id: str | None = None,
    scope_type: str | None = None,
    scope_id: str | None = None,
) -> dict:
    _require_admin(request)
    pool = _pool(request)
    rows = await _db.list_permissions(pool, tenant_id, user_id=user_id,
                                      scope_type=scope_type, scope_id=scope_id)
    return {"tenant_id": tenant_id, "permissions": rows, "count": len(rows)}


@router.post("/v1/evaluation/permissions", status_code=201)
async def create_permission(body: PermissionCreate, request: Request) -> dict:
    _require_admin(request)
    allowed_scope_types = {"pool", "campaign", "global"}
    if body.scope_type not in allowed_scope_types:
        raise HTTPException(400, detail=f"scope_type must be one of {allowed_scope_types}")
    if body.scope_type == "global" and body.scope_id:
        raise HTTPException(400, detail="scope_id must be null for global scope")
    if body.scope_type in {"pool", "campaign"} and not body.scope_id:
        raise HTTPException(400, detail=f"scope_id is required for scope_type={body.scope_type}")
    pool = _pool(request)
    row = await _db.create_permission(pool, **body.model_dump())
    return row


@router.patch("/v1/evaluation/permissions/{perm_id}")
async def update_permission(perm_id: str, tenant_id: str, body: PermissionUpdate, request: Request) -> dict:
    _require_admin(request)
    pool = _pool(request)
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    row = await _db.update_permission(pool, perm_id, tenant_id, **updates)
    if not row:
        raise HTTPException(404, detail="permission not found")
    return row


@router.delete("/v1/evaluation/permissions/{perm_id}", status_code=204)
async def delete_permission(perm_id: str, tenant_id: str, request: Request) -> None:
    _require_admin(request)
    pool = _pool(request)
    deleted = await _db.delete_permission(pool, perm_id, tenant_id)
    if not deleted:
        raise HTTPException(404, detail="permission not found")


# ─── Sampling check ───────────────────────────────────────────────────────────

class SampleCheckBody(BaseModel):
    tenant_id:    str
    campaign_id:  str
    session_id:   str
    session_meta: dict = Field(default_factory=dict)
    counter:      int = 0


@router.post("/v1/evaluation/sample")
async def check_sample(body: SampleCheckBody, request: Request) -> dict:
    pool = _pool(request)
    campaign = await _db.get_campaign(pool, body.campaign_id, body.tenant_id)
    if not campaign:
        raise HTTPException(404, detail="campaign not found")
    if campaign["status"] != "active":
        return {"should_sample": False, "reason": f"campaign status={campaign['status']}"}

    sampling_rules = campaign.get("sampling_rules") or {}
    sampled = should_sample(
        body.session_id,
        body.session_meta,
        sampling_rules,
        counter=body.counter,
    )
    priority = compute_priority(body.session_meta, sampling_rules)
    return {
        "should_sample": sampled,
        "priority":      priority,
        "campaign_id":   body.campaign_id,
        "session_id":    body.session_id,
    }


# ─── Reports ──────────────────────────────────────────────────────────────────

@router.get("/v1/evaluation/reports/campaign/{campaign_id}")
async def campaign_report(
    campaign_id: str,
    tenant_id: str,
    request: Request,
) -> dict:
    pool = _pool(request)
    campaign = await _db.get_campaign(pool, campaign_id, tenant_id)
    if not campaign:
        raise HTTPException(404, detail="campaign not found")

    async with pool.acquire() as conn:
        # Summary by eval_status
        status_rows = await conn.fetch(
            """
            SELECT eval_status, COUNT(*) AS count,
                   AVG(overall_score) AS avg_score,
                   AVG(normalized_score) AS avg_normalized,
                   SUM(CASE WHEN passed THEN 1 ELSE 0 END) AS passed_count
              FROM evaluation.results
             WHERE campaign_id=$1 AND tenant_id=$2
             GROUP BY eval_status
            """,
            campaign_id, tenant_id,
        )
        # Per-criterion averages
        criterion_rows = await conn.fetch(
            """
            SELECT criterion_id, criterion_name, dimension_id,
                   COUNT(*) AS responses,
                   AVG(score) FILTER (WHERE NOT na) AS avg_score,
                   SUM(CASE WHEN na THEN 1 ELSE 0 END) AS na_count
              FROM evaluation.criterion_responses
             WHERE campaign_id=$1 AND tenant_id=$2
             GROUP BY criterion_id, criterion_name, dimension_id
             ORDER BY dimension_id, criterion_id
            """,
            campaign_id, tenant_id,
        )

    return {
        "campaign": campaign,
        "status_breakdown": [dict(r) for r in status_rows],
        "criteria_breakdown": [dict(r) for r in criterion_rows],
    }


@router.get("/v1/evaluation/reports/agent")
async def agent_report(
    request: Request,
    tenant_id: str,
    pool_id: str | None = None,
    campaign_id: str | None = None,
    from_dt: str | None = None,
    to_dt: str | None = None,
    limit: int = 50,
) -> dict:
    pool = _pool(request)
    cond = "WHERE r.tenant_id=$1"
    args: list[Any] = [tenant_id]
    if pool_id:
        args.append(pool_id)
        # Note: evaluator_agent_id is the agent instance; can't directly join pool here
        # Use campaign's pool_id as proxy via campaign table
        cond += f" AND c.pool_id=${len(args)}"
    if campaign_id:
        args.append(campaign_id)
        cond += f" AND r.campaign_id=${len(args)}"
    if from_dt:
        args.append(from_dt)
        cond += f" AND r.submitted_at >= ${len(args)}"
    if to_dt:
        args.append(to_dt)
        cond += f" AND r.submitted_at <= ${len(args)}"

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT r.evaluator_agent_id,
                   COUNT(*) AS total_evaluations,
                   AVG(r.overall_score) AS avg_score,
                   AVG(r.normalized_score) AS avg_normalized,
                   SUM(CASE WHEN r.passed THEN 1 ELSE 0 END) AS passed_count,
                   SUM(CASE WHEN r.eval_status='contested' THEN 1 ELSE 0 END) AS contestation_count,
                   SUM(CASE WHEN r.comparison_mode THEN 1 ELSE 0 END) AS comparison_count
              FROM evaluation.results r
              LEFT JOIN evaluation.campaigns c ON c.id = r.campaign_id
             {cond}
             GROUP BY r.evaluator_agent_id
             ORDER BY total_evaluations DESC
             LIMIT ${len(args)+1}
            """,
            *args, limit,
        )

    return {
        "tenant_id": tenant_id,
        "agents":    [dict(r) for r in rows],
        "count":     len(rows),
    }
