"""
contestation_router.py
Arc 13 Fase A — endpoints for contestation threads, curation reviews,
calibration notes, and curation sampling rules.

Endpoints:
  ContestationThreads:
    GET  /v1/evaluation/instances/{id}/threads        list threads for instance
    POST /v1/evaluation/instances/{id}/contest        human_agent files contestation
    POST /v1/evaluation/instances/{id}/review         reviewer submits decision
    POST /v1/evaluation/instances/{id}/pre-review     pre-publication AI reviewer submits

  CurationReviews (curator queue):
    GET  /v1/evaluation/curations                     list curation queue
    POST /v1/evaluation/curations/{id}/resolve        curator resolves a review

  CalibrationNotes:
    GET  /v1/evaluation/calibration-notes             list calibration notes
    POST /v1/evaluation/calibration-notes/{id}/publish  mark published to KB

  CurationSamplingRules:
    GET  /v1/evaluation/campaigns/{id}/sampling-rules   list rules for campaign
    POST /v1/evaluation/campaigns/{id}/sampling-rules   create rule
    PUT  /v1/evaluation/campaigns/{id}/sampling-rules/{rid}  update rule
    DELETE /v1/evaluation/campaigns/{id}/sampling-rules/{rid}  delete rule
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .config import settings
from . import db as _db
from . import kafka_emitter as _kafka

logger = logging.getLogger("plughub.evaluation.contestation")

contestation_router = APIRouter()


# ─── Pydantic request models ──────────────────────────────────────────────────

class EvidenceEntryBody(BaseModel):
    stream_entry_id: str
    excerpt: str
    relevance_note: str


class ContestBody(BaseModel):
    """Filed by human_agent contesting a result."""
    dimension_id: str
    text: str = Field(..., min_length=1)
    evidence_entries: list[EvidenceEntryBody] = []


class ReviewBody(BaseModel):
    """Filed by reviewer_ai or human_reviewer after contestation."""
    dimension_id: str
    decision: str  # "upheld" | "revised"
    text: str = Field(..., min_length=1)
    score_override: float | None = None
    evidence_entries: list[EvidenceEntryBody] = []


class PreReviewBody(BaseModel):
    """Filed by pre_reviewer_ai before result publication."""
    dimension_id: str
    action: str  # "approve" | "adjust"
    text: str = Field(..., min_length=1)
    score_override: float | None = None
    evidence_entries: list[EvidenceEntryBody] = []
    calibration_signal: dict | None = None  # CalibrationSignal | null


class CurationResolveBody(BaseModel):
    status: str  # "approved" | "recalibrated" | "bias_flagged"
    curator_notes: str | None = None
    # Fields for creating a CalibrationNote (required when status != "approved")
    calibration_note_text: str | None = None
    dimension_id: str | None = None
    evaluator_id: str | None = None
    skill_version: str | None = None
    severity: str = "low"  # "low" | "medium" | "high"


class SamplingRuleBody(BaseModel):
    rule_type: str
    params: dict = {}
    enabled: bool = True
    priority: int = 10


class SamplingRuleUpdateBody(BaseModel):
    rule_type: str | None = None
    params: dict | None = None
    enabled: bool | None = None
    priority: int | None = None


# ─── Auth helpers (reuse from router.py pattern) ──────────────────────────────

def _get_tenant(request: Request) -> str:
    """Extract tenant_id from X-Tenant-ID header (same pattern as main router)."""
    tenant_id = request.headers.get("X-Tenant-ID") or request.headers.get("x-tenant-id")
    if not tenant_id:
        raise HTTPException(status_code=400, detail="X-Tenant-ID header required")
    return tenant_id


def _get_user(request: Request) -> str:
    """Extract user identity from X-User-ID or JWT subject."""
    user_id = request.headers.get("X-User-ID") or request.headers.get("x-user-id")
    if not user_id:
        raise HTTPException(status_code=401, detail="X-User-ID header required")
    return user_id


def _require_admin(request: Request) -> None:
    token = request.headers.get("X-Admin-Token") or request.headers.get("x-admin-token")
    from .config import settings
    if token != settings.admin_token:
        raise HTTPException(status_code=403, detail="admin token required")


# ─── ContestationThread endpoints ────────────────────────────────────────────

@contestation_router.get("/v1/evaluation/instances/{instance_id}/threads")
async def list_threads(
    instance_id: str,
    request: Request,
    dimension_id: str | None = None,
) -> dict:
    """
    List all ContestationThread entries for an evaluation instance.
    Returns threads grouped by dimension, ordered by round ASC.
    """
    tenant_id = _get_tenant(request)
    threads = await _db.list_contestation_threads(
        request.app.state.db_pool,
        instance_id,
        tenant_id,
        dimension_id=dimension_id,
    )
    return {"threads": threads, "count": len(threads)}


@contestation_router.post("/v1/evaluation/instances/{instance_id}/contest")
async def file_contestation(
    instance_id: str,
    body: ContestBody,
    request: Request,
) -> dict:
    """
    Human agent contests a specific dimension of their evaluation result.
    Creates a ContestationThread with author_type=human_agent.
    Updates result contestation_state → 'under_review'.
    """
    tenant_id = _get_tenant(request)
    user_id = _get_user(request)

    # Verify instance exists and is in contestation_open state
    instance = await _db.get_instance(request.app.state.db_pool, instance_id, tenant_id)
    if not instance:
        raise HTTPException(status_code=404, detail="instance not found")

    result = await _db.get_result_by_instance(request.app.state.db_pool, instance_id, tenant_id)
    if not result:
        raise HTTPException(status_code=404, detail="result not found")

    if result.get("contestation_state") not in ("contestation_open", None):
        raise HTTPException(
            status_code=409,
            detail=f"contestation not allowed in state: {result.get('contestation_state')}",
        )

    # Determine current round (human agent files round=2 on first contest, +2 per cycle)
    current_round = (result.get("current_round") or 1) + 1

    thread = await _db.create_contestation_thread(
        request.app.state.db_pool,
        tenant_id=tenant_id,
        evaluation_instance_id=instance_id,
        dimension_id=body.dimension_id,
        round=current_round,
        author_type="human_agent",
        author_id=user_id,
        text=body.text,
        evidence_entries=[e.model_dump() for e in body.evidence_entries],
    )

    # Advance state machine — persist updated current_round
    await _db.set_contestation_state(
        request.app.state.db_pool,
        result["id"],
        "under_review",
        action_required="review",
        current_round=current_round,
    )

    logger.info("contestation filed: instance=%s dimension=%s round=%s by=%s",
                instance_id, body.dimension_id, current_round, user_id)
    return {"thread": thread, "contestation_state": "under_review"}


@contestation_router.post("/v1/evaluation/instances/{instance_id}/review")
async def submit_review(
    instance_id: str,
    body: ReviewBody,
    request: Request,
) -> dict:
    """
    Reviewer (AI or human) submits decision on a contested dimension.
    author_type determined by X-Author-Type header (reviewer_ai | human_reviewer).
    Creates ContestationThread with decision and optional score_override.
    """
    tenant_id = _get_tenant(request)
    author_id = _get_user(request)
    author_type = request.headers.get("X-Author-Type", "reviewer_ai")

    if author_type not in ("reviewer_ai", "human_reviewer"):
        raise HTTPException(status_code=400, detail="X-Author-Type must be reviewer_ai or human_reviewer")
    if body.decision not in ("upheld", "revised"):
        raise HTTPException(status_code=400, detail="decision must be upheld or revised")
    if body.decision == "revised" and body.score_override is None:
        raise HTTPException(status_code=400, detail="score_override required when decision=revised")

    result = await _db.get_result_by_instance(request.app.state.db_pool, instance_id, tenant_id)
    if not result:
        raise HTTPException(status_code=404, detail="result not found")

    if result.get("contestation_state") != "under_review":
        raise HTTPException(status_code=409, detail="result not under_review")

    current_round = result.get("current_round") or 2
    review_round = current_round + 1  # round 3 = first review, 5 = second, etc.

    thread = await _db.create_contestation_thread(
        request.app.state.db_pool,
        tenant_id=tenant_id,
        evaluation_instance_id=instance_id,
        dimension_id=body.dimension_id,
        round=review_round,
        author_type=author_type,
        author_id=author_id,
        text=body.text,
        decision=body.decision,
        score_override=body.score_override,
        evidence_entries=[e.model_dump() for e in body.evidence_entries],
    )

    # Determine next state: check max_rounds before allowing another cycle
    # review_round 3 = 1 cycle, 5 = 2 cycles, 7 = 3 cycles, etc.
    # cycles_completed = (review_round - 1) // 2
    max_rounds = 3  # default
    try:
        campaign = await _db.get_campaign(
            request.app.state.db_pool, result.get("campaign_id", ""), tenant_id
        )
        if campaign:
            policy = campaign.get("contestation_policy") or {}
            max_rounds = int(policy.get("max_rounds", 3))
    except Exception:
        pass  # non-fatal — default to 3

    cycles_completed = (review_round - 1) // 2
    max_rounds_reached = cycles_completed >= max_rounds

    if body.decision == "upheld" or max_rounds_reached:
        next_state = "closed_upheld" if body.decision == "upheld" else "closed_max_rounds"
    else:
        next_state = "contestation_open"

    await _db.set_contestation_state(
        request.app.state.db_pool,
        result["id"],
        next_state,
        action_required=None,
        current_round=review_round,
    )

    logger.info(
        "review submitted: instance=%s dimension=%s decision=%s round=%s cycles=%s max=%s next_state=%s by=%s",
        instance_id, body.dimension_id, body.decision, review_round, cycles_completed, max_rounds, next_state, author_id,
    )
    return {"thread": thread, "contestation_state": next_state}


@contestation_router.post("/v1/evaluation/instances/{instance_id}/pre-review")
async def submit_pre_review(
    instance_id: str,
    body: PreReviewBody,
    request: Request,
) -> dict:
    """
    Pre-publication AI reviewer submits quality gate review.
    author_type=pre_reviewer_ai, round=1 (same round as evaluator).
    Optionally includes calibration_signal → triggers CurationReview creation.
    """
    tenant_id = _get_tenant(request)
    agent_id = _get_user(request)

    instance = await _db.get_instance(request.app.state.db_pool, instance_id, tenant_id)
    if not instance:
        raise HTTPException(status_code=404, detail="instance not found")

    result = await _db.get_result_by_instance(request.app.state.db_pool, instance_id, tenant_id)
    if not result:
        raise HTTPException(status_code=404, detail="result not found")

    if result.get("contestation_state") not in ("pre_review_pending", None):
        raise HTTPException(status_code=409, detail="pre-review already completed or not applicable")

    thread = await _db.create_contestation_thread(
        request.app.state.db_pool,
        tenant_id=tenant_id,
        evaluation_instance_id=instance_id,
        dimension_id=body.dimension_id,
        round=1,  # stored as round=1, author_type distinguishes from evaluator
        author_type="pre_reviewer_ai",
        author_id=agent_id,
        text=body.text,
        score_override=body.score_override if body.action == "adjust" else None,
        evidence_entries=[e.model_dump() for e in body.evidence_entries],
        calibration_signal=body.calibration_signal,
    )

    # If calibration_signal present → create CurationReview (async, non-blocking)
    curation_review = None
    if body.calibration_signal:
        curation_review = await _db.create_curation_review(
            request.app.state.db_pool,
            tenant_id=tenant_id,
            evaluation_instance_id=instance_id,
            trigger="reviewer_signal",
        )
        logger.info("curation review triggered from calibration_signal: instance=%s", instance_id)

    # Advance to contestation_open (pre-review complete → publish result to evaluated agent)
    await _db.set_contestation_state(
        request.app.state.db_pool,
        result["id"],
        "contestation_open",
        action_required=None,
    )
    # Mark pre_review_complete on result
    async with request.app.state.db_pool.acquire() as conn:
        await conn.execute(
            "UPDATE evaluation.results SET pre_review_complete=TRUE, updated_at=now() WHERE id=$1",
            result["id"],
        )

    logger.info("pre-review submitted: instance=%s dimension=%s action=%s by=%s",
                instance_id, body.dimension_id, body.action, agent_id)
    return {
        "thread": thread,
        "contestation_state": "contestation_open",
        "curation_review_created": curation_review is not None,
    }


# ─── CurationReview (curator queue) ──────────────────────────────────────────

@contestation_router.get("/v1/evaluation/curations")
async def list_curations(
    request: Request,
    campaign_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """List curation reviews — the curator queue."""
    tenant_id = _get_tenant(request)
    reviews = await _db.list_curation_reviews(
        request.app.state.db_pool,
        tenant_id,
        campaign_id=campaign_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return {"reviews": reviews, "count": len(reviews)}


@contestation_router.post("/v1/evaluation/curations/{review_id}/resolve")
async def resolve_curation(
    review_id: str,
    body: CurationResolveBody,
    request: Request,
) -> dict:
    """
    Curator resolves a curation review.
    - approved      → mark as approved, no CalibrationNote created
    - recalibrated  → create CalibrationNote, publish to KB (async)
    - bias_flagged  → create CalibrationNote with severity=high, publish to KB
    """
    tenant_id = _get_tenant(request)
    curator_id = _get_user(request)

    if body.status not in ("approved", "recalibrated", "bias_flagged"):
        raise HTTPException(status_code=400, detail="status must be approved, recalibrated, or bias_flagged")

    # Always fetch campaign_id and evaluation_instance_id (needed for Kafka event regardless of status)
    async with request.app.state.db_pool.acquire() as conn:
        _cr_row = await conn.fetchrow(
            """
            SELECT i.campaign_id, cr.evaluation_instance_id
            FROM evaluation.curation_reviews cr
            JOIN evaluation.instances i ON i.id = cr.evaluation_instance_id
            WHERE cr.id=$1 AND cr.tenant_id=$2
            """,
            review_id, tenant_id,
        )
    if not _cr_row:
        raise HTTPException(status_code=404, detail="curation review not found")
    _campaign_id = _cr_row["campaign_id"] or ""
    _evaluation_instance_id = _cr_row["evaluation_instance_id"] or ""

    calibration_note = None
    if body.status in ("recalibrated", "bias_flagged"):
        if not all([body.calibration_note_text, body.dimension_id, body.evaluator_id, body.skill_version]):
            raise HTTPException(
                status_code=400,
                detail="calibration_note_text, dimension_id, evaluator_id, skill_version required for recalibrated/bias_flagged",
            )

        calibration_note = await _db.create_calibration_note(
            request.app.state.db_pool,
            tenant_id=tenant_id,
            campaign_id=row["campaign_id"],
            dimension_id=body.dimension_id,
            evaluator_id=body.evaluator_id,
            skill_version=body.skill_version,
            text=body.calibration_note_text,
            severity="high" if body.status == "bias_flagged" else body.severity,
        )

    review = await _db.resolve_curation_review(
        request.app.state.db_pool,
        review_id,
        tenant_id,
        status=body.status,
        curator_id=curator_id,
        curator_notes=body.curator_notes,
        calibration_note_id=calibration_note["id"] if calibration_note else None,
    )
    if not review:
        raise HTTPException(status_code=404, detail="curation review not found")

    # Publish CalibrationNote to mcp-server-knowledge knowledge namespace
    kb_published = False
    if calibration_note:
        note_severity = "high" if body.status == "bias_flagged" else body.severity
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                kb_resp = await client.post(
                    f"{settings.knowledge_api_url}/v1/knowledge/snippets",
                    json={
                        "tenant_id":  tenant_id,
                        "namespace":  f"evaluation:calibration:{calibration_note.get('campaign_id', '')}",
                        "content":    calibration_note.get("text", ""),
                        "source_ref": f"calibration_note:{calibration_note['id']}",
                        "metadata": {
                            "dimension_id":  calibration_note.get("dimension_id", ""),
                            "evaluator_id":  calibration_note.get("evaluator_id", ""),
                            "skill_version": calibration_note.get("skill_version", ""),
                            "severity":      note_severity,
                            "note_id":       calibration_note["id"],
                        },
                    },
                )
            if kb_resp.status_code in (200, 201):
                kb_published = True
                await _db.mark_calibration_note_published(
                    request.app.state.db_pool,
                    calibration_note["id"],
                    tenant_id,
                )
                calibration_note["published_to_kb"] = True
                logger.info(
                    "calibration note published to KB: note=%s campaign=%s",
                    calibration_note["id"], calibration_note.get("campaign_id"),
                )
            else:
                logger.warning(
                    "KB publish failed (HTTP %s): note=%s",
                    kb_resp.status_code, calibration_note["id"],
                )
        except Exception as exc:
            logger.error("KB publish error (non-blocking): note=%s err=%s",
                         calibration_note["id"], exc)

    # Emit calibration.events Kafka event — always, regardless of status (approved/recalibrated/bias_flagged)
    try:
        await _kafka.emit_calibration_reviewed(
            request.app.state.kafka_producer,
            review_id=review_id,
            campaign_id=_campaign_id,
            evaluation_instance_id=_evaluation_instance_id,
            tenant_id=tenant_id,
            evaluator_id=body.evaluator_id or "",
            skill_version=body.skill_version or "",
            decision=body.status,
            calibration_note_id=calibration_note["id"] if calibration_note else None,
        )
    except Exception as exc:
        logger.error("failed to emit calibration_reviewed event: %s", exc)

    # Emit calibration_note_published to evaluation.events (only if KB succeeded)
    if calibration_note and kb_published:
        try:
            await _kafka.emit_calibration_note_published(
                request.app.state.kafka_producer,
                note_id=calibration_note["id"],
                campaign_id=calibration_note.get("campaign_id", ""),
                evaluator_id=body.evaluator_id,
                severity=note_severity,
                tenant_id=tenant_id,
            )
        except Exception as exc:
            logger.error("failed to emit calibration_note_published event: %s", exc)

    logger.info("curation resolved: review=%s status=%s kb_published=%s by=%s",
                review_id, body.status, kb_published, curator_id)
    return {
        "review": review,
        "calibration_note": calibration_note,
        "kb_published": kb_published,
    }


# ─── CalibrationNotes ─────────────────────────────────────────────────────────

@contestation_router.get("/v1/evaluation/calibration-notes")
async def list_calibration_notes(
    request: Request,
    campaign_id: str | None = None,
    evaluator_id: str | None = None,
    published_to_kb: bool | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """List calibration notes — the evaluator calibration history."""
    tenant_id = _get_tenant(request)
    notes = await _db.list_calibration_notes(
        request.app.state.db_pool,
        tenant_id,
        campaign_id=campaign_id,
        evaluator_id=evaluator_id,
        published_to_kb=published_to_kb,
        limit=limit,
        offset=offset,
    )
    return {"notes": notes, "count": len(notes)}


@contestation_router.post("/v1/evaluation/calibration-notes/{note_id}/publish")
async def publish_calibration_note(
    note_id: str,
    request: Request,
) -> dict:
    """
    Mark a CalibrationNote as published to the knowledge namespace.
    Called after successful ingest into mcp-server-knowledge.
    Emits calibration_note_published Kafka event.
    """
    tenant_id = _get_tenant(request)
    note = await _db.mark_calibration_note_published(
        request.app.state.db_pool,
        note_id,
        tenant_id,
    )
    if not note:
        raise HTTPException(status_code=404, detail="calibration note not found")

    try:
        await _kafka.emit_calibration_note_published(
            request.app.state.kafka_producer,
            note_id=note_id,
            campaign_id=note.get("campaign_id", ""),
            evaluator_id=note.get("evaluator_id", ""),
            severity=note.get("severity", "low"),
            tenant_id=tenant_id,
        )
    except Exception as exc:
        logger.error("failed to emit calibration_note_published event: %s", exc)

    return {"note": note}


# ─── CurationSamplingRules CRUD ───────────────────────────────────────────────

@contestation_router.get("/v1/evaluation/campaigns/{campaign_id}/sampling-rules")
async def list_sampling_rules(
    campaign_id: str,
    request: Request,
) -> dict:
    """List curation sampling rules for a campaign."""
    tenant_id = _get_tenant(request)
    rules = await _db.list_sampling_rules(
        request.app.state.db_pool,
        tenant_id,
        campaign_id,
    )
    return {"rules": rules, "count": len(rules)}


@contestation_router.post("/v1/evaluation/campaigns/{campaign_id}/sampling-rules")
async def create_sampling_rule(
    campaign_id: str,
    body: SamplingRuleBody,
    request: Request,
) -> dict:
    """Create a curation sampling rule for a campaign."""
    tenant_id = _get_tenant(request)
    rule = await _db.create_sampling_rule(
        request.app.state.db_pool,
        tenant_id=tenant_id,
        campaign_id=campaign_id,
        rule_type=body.rule_type,
        params=body.params,
        enabled=body.enabled,
        priority=body.priority,
    )
    return {"rule": rule}


@contestation_router.put("/v1/evaluation/campaigns/{campaign_id}/sampling-rules/{rule_id}")
async def update_sampling_rule(
    campaign_id: str,
    rule_id: str,
    body: SamplingRuleUpdateBody,
    request: Request,
) -> dict:
    """Update a curation sampling rule."""
    tenant_id = _get_tenant(request)
    updates = body.model_dump(exclude_none=True)
    rule = await _db.update_sampling_rule(
        request.app.state.db_pool,
        rule_id,
        tenant_id,
        **updates,
    )
    if not rule:
        raise HTTPException(status_code=404, detail="sampling rule not found")
    return {"rule": rule}


@contestation_router.delete("/v1/evaluation/campaigns/{campaign_id}/sampling-rules/{rule_id}")
async def delete_sampling_rule(
    campaign_id: str,
    rule_id: str,
    request: Request,
) -> dict:
    """Delete a curation sampling rule."""
    tenant_id = _get_tenant(request)
    deleted = await _db.delete_sampling_rule(
        request.app.state.db_pool,
        rule_id,
        tenant_id,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="sampling rule not found")
    return {"deleted": True}
