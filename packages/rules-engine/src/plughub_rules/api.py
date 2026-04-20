"""
api.py
FastAPI HTTP app for the Rules Engine.
Spec: PlugHub v24.0 section 3.2
"""

from __future__ import annotations
import logging
from contextlib import asynccontextmanager
from typing import Annotated, Any

import redis.asyncio as aioredis
from fastapi import Depends, FastAPI, HTTPException, Query

from .config import get_settings
from .evaluator import RuleEvaluator
from .models import (
    DryRunApiRequest,
    DryRunApiResponse,
    EscalationDecision,
    Rule,
    RuleCreateRequest,
    RuleStatusPatch,
)
from .rule_registry import RuleRegistry
from .rule_store import RuleStore
from .session_reader import SessionParamsReader

logger = logging.getLogger("plughub.rules")

# ─────────────────────────────────────────────────────────────────────────────
# Application state (set at startup)
# ─────────────────────────────────────────────────────────────────────────────

_redis_client: aioredis.Redis | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[type-arg]
    global _redis_client
    settings = get_settings()
    _redis_client = await aioredis.from_url(
        settings.redis_url, decode_responses=False
    )
    logger.info("Rules Engine API connected to Redis")
    yield
    if _redis_client:
        await _redis_client.aclose()


app = FastAPI(
    title="PlugHub Rules Engine",
    version="1.0.0",
    lifespan=lifespan,
)


# ─────────────────────────────────────────────────────────────────────────────
# Dependencies
# ─────────────────────────────────────────────────────────────────────────────

def get_redis() -> Any:
    if _redis_client is None:
        raise RuntimeError("Redis not initialised")
    return _redis_client


def get_registry(redis: Annotated[Any, Depends(get_redis)]) -> RuleRegistry:
    return RuleRegistry(redis)


def get_reader(redis: Annotated[Any, Depends(get_redis)]) -> SessionParamsReader:
    return SessionParamsReader(redis)


def get_evaluator() -> RuleEvaluator:
    return RuleEvaluator()


def get_rule_store(redis: Annotated[Any, Depends(get_redis)]) -> RuleStore:
    return RuleStore(redis)


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "rules-engine", "version": "1.0.0"}


@app.post("/evaluate", response_model=EscalationDecision)
async def evaluate(
    body: dict,
    reader:     Annotated[SessionParamsReader, Depends(get_reader)],
    evaluator:  Annotated[RuleEvaluator,       Depends(get_evaluator)],
    rule_store: Annotated[RuleStore,           Depends(get_rule_store)],
) -> EscalationDecision:
    """
    Evaluates active rules for a given session turn.
    Reads turn params from Redis, runs rules in priority order.
    """
    session_id = body.get("session_id", "")
    tenant_id  = body.get("tenant_id",  "")
    turn_id    = body.get("turn_id",    "")

    ctx = await reader.build_evaluation_context(tenant_id, session_id, turn_id)
    if ctx is None:
        return EscalationDecision(should_escalate=False, reason="params_not_found")

    rules = await rule_store.get_active_rules(tenant_id)
    # Evaluate in priority order (highest first)
    sorted_rules = sorted(rules, key=lambda r: r.priority, reverse=True)

    for rule in sorted_rules:
        result = evaluator.evaluate(rule, ctx)
        if result.triggered:
            mode: Any = "shadow" if rule.status == "shadow" else "active"
            return EscalationDecision(
                should_escalate=True,
                rule_id=rule.rule_id,
                pool_target=rule.target_pool,
                reason=f"rule:{rule.rule_id}",
                mode=mode,
            )

    return EscalationDecision(should_escalate=False)


@app.post("/rules", response_model=Rule, status_code=201)
async def create_rule(
    body:     RuleCreateRequest,
    registry: Annotated[RuleRegistry, Depends(get_registry)],
) -> Rule:
    """Creates a new rule with status=draft."""
    try:
        return await registry.create(body)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.patch("/rules/{rule_id}/status", response_model=Rule)
async def update_rule_status(
    rule_id:   str,
    body:      RuleStatusPatch,
    registry:  Annotated[RuleRegistry, Depends(get_registry)],
    tenant_id: Annotated[str, Query(...)],
) -> Rule:
    """Transitions rule lifecycle status."""
    try:
        return await registry.update_status(tenant_id, rule_id, body.status)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get("/rules/{rule_id}", response_model=Rule)
async def get_rule(
    rule_id:   str,
    registry:  Annotated[RuleRegistry, Depends(get_registry)],
    tenant_id: Annotated[str, Query(...)],
) -> Rule:
    rule = await registry.get(tenant_id, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail=f"Rule not found: {rule_id}")
    return rule


@app.get("/rules", response_model=list[Rule])
async def list_rules(
    registry:  Annotated[RuleRegistry, Depends(get_registry)],
    tenant_id: Annotated[str, Query(...)],
    status:    Annotated[str | None, Query()] = None,
) -> list[Rule]:
    return await registry.list_rules(tenant_id, status=status)


@app.post("/rules/{rule_id}/dry-run", response_model=DryRunApiResponse)
async def dry_run_rule(
    rule_id:   str,
    body:      DryRunApiRequest,
    registry:  Annotated[RuleRegistry, Depends(get_registry)],
) -> DryRunApiResponse:
    """
    Simplified dry-run endpoint.
    Production version wires ClickHouse for historical session data.
    This implementation returns a placeholder response.
    """
    rule = await registry.get(body.tenant_id, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail=f"Rule not found: {rule_id}")

    # Production: query ClickHouse for sessions in [start_date, end_date]
    # and evaluate rule against each. For now return a simulation placeholder.
    return DryRunApiResponse(
        sessions_evaluated=0,
        would_have_escalated=0,
        escalation_rate=0.0,
        sample_sessions=[
            {
                "note": "dry_run simulation not yet available",
                "rule_id": rule_id,
                "tenant_id": body.tenant_id,
                "start_date": body.start_date,
                "end_date": body.end_date,
            }
        ],
    )


@app.get("/rules/{rule_id}/report")
async def get_rule_report(
    rule_id:   str,
    registry:  Annotated[RuleRegistry, Depends(get_registry)],
    tenant_id: Annotated[str, Query(...)],
) -> dict:
    rule = await registry.get(tenant_id, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail=f"Rule not found: {rule_id}")

    status = rule.status
    if status == "active":
        message = "Live escalation report — sourced from ClickHouse audit log."
    elif status == "shadow":
        message = "Shadow mode report — sourced from Kafka shadow events."
    elif status == "dry_run":
        message = "Dry-run report — simulated against historical sessions."
    else:
        message = f"Rule is in '{status}' state — no report available yet."

    return {
        "status":  status,
        "rule":    rule.model_dump(),
        "message": message,
    }
