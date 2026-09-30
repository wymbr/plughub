"""
api.py
FastAPI HTTP app for the Rules Engine.
Spec: PlugHub v24.0 section 3.2

AUT-65 (2026-09-30): toda rota menos `/health` exige credencial (`auth.py`) — capacidade
`config.rules`, tenant do token, `X-Service-Token` para chamador interno. E o dry-run deixou de
devolver ZEROS como se tivesse medido. Desde a RUL-05 ele simula de verdade, contra o contexto
que as regras VIRAM em cada turno (`rule_turn_contexts`, 90 dias), com o mesmo avaliador.
"""

from __future__ import annotations
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response

from .auth import authorize, require_credential
from .config import get_settings
from .lifecycle import EDITABLE_STATUSES, transitions
from .evaluator import RuleEvaluator
from .models import (
    DryRunApiRequest,
    DryRunApiResponse,
    DryRunRequest,
    DryRunUnsavedRequest,
    EscalationDecision,
    Rule,
    RuleCreateRequest,
    RuleStatusPatch,
    RuleUpdateRequest,
)
from .rule_registry import RuleLockedError, RuleRegistry
from .rule_store import RuleStore
from .session_reader import SessionParamsReader
from .dry_run import DryRunEngine
from .history_reader import History, HistoryReader, HistoryUnavailable

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


# Toda rota abaixo passa pela credencial ANTES do corpo (anônimo = 401, nunca 422).
router = APIRouter(dependencies=[Depends(require_credential)])

MAX_WINDOW_DAYS = 90   # = a retenção de `rule_turn_contexts` (RUL-05)


def get_history_reader() -> HistoryReader:
    return HistoryReader()


def _parse_instant(raw: str, campo: str) -> datetime:
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=422, detail=f"{campo} não é uma data ISO: {raw!r}")
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


async def _simulate(rule: Rule, tenant_id: str, start: datetime, end: datetime,
                    reader: HistoryReader) -> DryRunApiResponse:
    if end <= start:
        raise HTTPException(status_code=422, detail="a janela termina antes de começar")
    if end - start > timedelta(days=MAX_WINDOW_DAYS):
        raise HTTPException(status_code=422,
                            detail=f"janela de no máximo {MAX_WINDOW_DAYS} dias — é o que o histórico guarda")
    try:
        hist: History = await reader.load(tenant_id, start, end)
    except HistoryUnavailable as exc:
        logger.warning("dry-run tenant=%s rule=%s: histórico indisponível — %s", tenant_id, rule.rule_id, exc)
        raise HTTPException(status_code=503, detail=f"histórico de turnos indisponível: {exc}")
    days = max(1, min(MAX_WINDOW_DAYS, (end - start).days or 1))
    result = await DryRunEngine().dry_run_historico(
        DryRunRequest(tenant_id=tenant_id, rule=rule, history_window_days=days), hist.sessions)
    total = result.total_conversations
    return DryRunApiResponse(
        sessions_evaluated=   total,
        would_have_escalated= result.would_trigger_count,
        escalation_rate=      result.trigger_rate if total else None,
        sample_sessions=[{"session_id": s.session_id, "at_turn": s.at_turn,
                          "context": s.context_at_trigger.model_dump() if s.context_at_trigger else None}
                         for s in result.sample_triggers],
        window_start=  start.isoformat(),
        window_end=    end.isoformat(),
        turn_contexts= hist.turn_contexts,
        coverage_from= hist.coverage_from,
        truncated=     hist.truncated,
    )


@router.post("/evaluate", response_model=EscalationDecision)
async def evaluate(
    request: Request,
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
    tenant_id  = authorize(request, str(body.get("tenant_id", "") or ""), write=True)
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


@router.post("/rules", response_model=Rule, status_code=201)
async def create_rule(
    request:  Request,
    body:     RuleCreateRequest,
    registry: Annotated[RuleRegistry, Depends(get_registry)],
) -> Rule:
    """Creates a new rule with status=draft."""
    authorize(request, body.tenant_id, write=True)
    try:
        return await registry.create(body)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.patch("/rules/{rule_id}/status", response_model=Rule)
async def update_rule_status(
    request:   Request,
    rule_id:   str,
    body:      RuleStatusPatch,
    registry:  Annotated[RuleRegistry, Depends(get_registry)],
    tenant_id: Annotated[str, Query(...)],
) -> Rule:
    """Transitions rule lifecycle status. Ativar regra com target_pool tira contato da IA."""
    tenant_id = authorize(request, tenant_id, write=True)
    try:
        return await registry.update_status(tenant_id, rule_id, body.status)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.get("/lifecycle")
async def lifecycle(request: Request, tenant_id: Annotated[str, Query(...)]) -> dict:
    """RUL-03 — a máquina de estados e o que se edita, para a tela MOSTRAR sem copiar."""
    authorize(request, tenant_id, write=False)
    return {"transitions": transitions(), "editable_statuses": sorted(EDITABLE_STATUSES)}


@router.put("/rules/{rule_id}", response_model=Rule)
async def update_rule(
    request:   Request,
    rule_id:   str,
    body:      RuleUpdateRequest,
    registry:  Annotated[RuleRegistry, Depends(get_registry)],
    tenant_id: Annotated[str, Query(...)],
) -> Rule:
    """RUL-03 — edita condições, pool, aviso e prioridade. Só em draft/disabled (409 senão)."""
    tenant_id = authorize(request, tenant_id, write=True)
    try:
        return await registry.update(tenant_id, rule_id, body)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuleLockedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.delete("/rules/{rule_id}", status_code=204)
async def delete_rule(
    request:   Request,
    rule_id:   str,
    registry:  Annotated[RuleRegistry, Depends(get_registry)],
    tenant_id: Annotated[str, Query(...)],
) -> Response:
    """RUL-03 — apaga a regra em draft/disabled (409 se ela age ou mede)."""
    tenant_id = authorize(request, tenant_id, write=True)
    try:
        await registry.delete(tenant_id, rule_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except RuleLockedError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return Response(status_code=204)


@router.get("/rules/{rule_id}", response_model=Rule)
async def get_rule(
    request:   Request,
    rule_id:   str,
    registry:  Annotated[RuleRegistry, Depends(get_registry)],
    tenant_id: Annotated[str, Query(...)],
) -> Rule:
    tenant_id = authorize(request, tenant_id, write=False)
    rule = await registry.get(tenant_id, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail=f"Rule not found: {rule_id}")
    return rule


@router.get("/rules", response_model=list[Rule])
async def list_rules(
    request:   Request,
    registry:  Annotated[RuleRegistry, Depends(get_registry)],
    tenant_id: Annotated[str, Query(...)],
    status:    Annotated[str | None, Query()] = None,
) -> list[Rule]:
    tenant_id = authorize(request, tenant_id, write=False)
    return await registry.list_rules(tenant_id, status=status)


@router.post("/rules/dry-run", response_model=DryRunApiResponse)
async def dry_run_unsaved_rule(
    request: Request,
    body:    DryRunUnsavedRequest,
    reader:  Annotated[HistoryReader, Depends(get_history_reader)],
) -> DryRunApiResponse:
    """Dry-run de uma regra AINDA NÃO SALVA (a tool `rule_dry_run`): os últimos N dias.
    A regra passa pela mesma validação da edição (janela de média recusada, RUL-04)."""
    tenant_id = authorize(request, body.tenant_id, write=True)
    now = datetime.now(timezone.utc)
    rule = Rule(rule_id="unsaved", tenant_id=tenant_id, status="draft",
                created_at=now.isoformat(), updated_at=now.isoformat(), **body.rule.model_dump())
    return await _simulate(rule, tenant_id, now - timedelta(days=body.history_window_days), now, reader)


@router.post("/rules/{rule_id}/dry-run", response_model=DryRunApiResponse)
async def dry_run_rule(
    request:   Request,
    rule_id:   str,
    body:      DryRunApiRequest,
    registry:  Annotated[RuleRegistry, Depends(get_registry)],
    reader:    Annotated[HistoryReader, Depends(get_history_reader)],
) -> DryRunApiResponse:
    """Dry-run de uma regra salva contra a janela pedida (≤ 90 dias)."""
    tenant_id = authorize(request, body.tenant_id, write=True)
    rule = await registry.get(tenant_id, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail=f"Rule not found: {rule_id}")
    return await _simulate(rule, tenant_id, _parse_instant(body.start_date, "start_date"),
                           _parse_instant(body.end_date, "end_date"), reader)


@router.get("/rules/{rule_id}/report")
async def get_rule_report(
    request:   Request,
    rule_id:   str,
    registry:  Annotated[RuleRegistry, Depends(get_registry)],
    tenant_id: Annotated[str, Query(...)],
) -> dict:
    tenant_id = authorize(request, tenant_id, write=False)
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


app.include_router(router)
