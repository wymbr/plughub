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

import logging
from datetime import datetime, timezone
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import settings
from . import db as _db
from . import kafka_emitter as _kafka
from .sampling import should_sample, compute_expires_at, compute_priority

logger = logging.getLogger("plughub.evaluation.router")

router = APIRouter()


# ─── Auth helper ──────────────────────────────────────────────────────────────

def _require_admin(request: Request) -> None:
    if not settings.admin_token:
        return
    token = request.headers.get("x-admin-token", "")
    if token != settings.admin_token:
        raise HTTPException(status_code=401, detail="unauthorized")


# ─── DB pool accessor ─────────────────────────────────────────────────────────

def _pool(request: Request) -> asyncpg.Pool:
    return request.app.state.db_pool


def _kafka_producer(request: Request) -> Any:
    return request.app.state.kafka_producer


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
    tenant_id:       str
    name:            str
    description:     str = ""
    form_id:         str
    pool_id:         str
    sampling_rules:  dict = Field(default_factory=dict)
    reviewer_rules:  dict = Field(default_factory=dict)
    schedule:        dict = Field(default_factory=dict)
    created_by:      str = "operator"


class CampaignUpdate(BaseModel):
    name:           str | None = None
    description:    str | None = None
    status:         str | None = None
    sampling_rules: dict | None = None
    reviewer_rules: dict | None = None
    schedule:       dict | None = None


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
    row = await _db.create_campaign(pool, **body.model_dump())
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
    reviewer_agent_id: str
    reviewer_outcome:  str   # "approved" | "adjusted" | "rejected"
    reviewer_notes:    str = ""
    reviewer_score:    float | None = None


class LockBody(BaseModel):
    locked_by: str


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
async def get_result(result_id: str, tenant_id: str, request: Request) -> dict:
    pool = _pool(request)
    row = await _db.get_result(pool, result_id, tenant_id)
    if not row:
        raise HTTPException(404, detail="result not found")
    return row


@router.get("/v1/evaluation/results/{result_id}/criteria")
async def get_criteria(result_id: str, tenant_id: str, request: Request) -> dict:
    pool = _pool(request)
    rows = await _db.list_criterion_responses(pool, result_id, tenant_id)
    return {"result_id": result_id, "criterion_responses": rows, "count": len(rows)}


@router.post("/v1/evaluation/results/{result_id}/review")
async def review_result(result_id: str, tenant_id: str, body: ReviewBody, request: Request) -> dict:
    pool = _pool(request)
    allowed = {"approved", "adjusted", "rejected"}
    if body.reviewer_outcome not in allowed:
        raise HTTPException(400, detail=f"reviewer_outcome must be one of {allowed}")
    row = await _db.update_result(
        pool, result_id, tenant_id,
        eval_status="reviewed",
        reviewer_agent_id=body.reviewer_agent_id,
        reviewer_outcome=body.reviewer_outcome,
        reviewer_notes=body.reviewer_notes,
        reviewer_score=body.reviewer_score,
        reviewed_at=datetime.now(tz=timezone.utc),
    )
    if not row:
        raise HTTPException(404, detail="result not found")
    return row


@router.post("/v1/evaluation/results/{result_id}/lock")
async def lock_result(result_id: str, tenant_id: str, body: LockBody, request: Request) -> dict:
    _require_admin(request)
    pool = _pool(request)
    row = await _db.update_result(
        pool, result_id, tenant_id,
        eval_status="locked",
        locked_by=body.locked_by,
        locked_at=datetime.now(tz=timezone.utc),
    )
    if not row:
        raise HTTPException(404, detail="result not found")
    return row


# ─── Contestations ────────────────────────────────────────────────────────────

class ContestationCreate(BaseModel):
    tenant_id:            str
    result_id:            str
    instance_id:          str
    session_id:           str
    contested_by:         str
    contestation_reason:  str = ""


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
    pool = _pool(request)
    producer = _kafka_producer(request)

    # Validate result exists and isn't locked
    result = await _db.get_result(pool, body.result_id, body.tenant_id)
    if not result:
        raise HTTPException(404, detail="result not found")
    if result["eval_status"] == "locked":
        raise HTTPException(409, detail="result is locked, cannot contest")

    row = await _db.create_contestation(
        pool,
        tenant_id=body.tenant_id,
        result_id=body.result_id,
        instance_id=body.instance_id,
        session_id=body.session_id,
        contested_by=body.contested_by,
        contestation_reason=body.contestation_reason,
    )
    await _kafka.emit_contestation_opened(
        producer, settings.evaluation_topic,
        contestation_id=row["id"],
        result_id=body.result_id,
        instance_id=body.instance_id,
        tenant_id=body.tenant_id,
        session_id=body.session_id,
        contested_by=body.contested_by,
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
