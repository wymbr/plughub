"""
api.py
FastAPI HTTP app for the Rules Engine.
Spec: PlugHub v24.0 section 3.2

AUT-65 (2026-09-30): toda rota menos `/health` exige credencial (`auth.py`) — capacidade
`config.rules`, tenant do token, `X-Service-Token` para chamador interno. E o dry-run deixou de
devolver ZEROS: não há histórico por turno de onde simular (os parâmetros vivem só no Redis
da sessão), e "0 sessões, taxa 0.0" parecia medição. Agora recusa com 501 nomeando o que falta
(`RUL-05`).
"""

from __future__ import annotations
import logging
from contextlib import asynccontextmanager
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
    EscalationDecision,
    Rule,
    RuleCreateRequest,
    RuleStatusPatch,
    RuleUpdateRequest,
)
from .rule_registry import RuleLockedError, RuleRegistry
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


# Toda rota abaixo passa pela credencial ANTES do corpo (anônimo = 401, nunca 422).
router = APIRouter(dependencies=[Depends(require_credential)])

DRY_RUN_UNAVAILABLE = (
    "dry-run historico indisponivel: os parametros por turno (sentimento, confianca, flags, "
    "tempo) so existem no Redis da sessao e expiram — nao ha historico de onde simular (RUL-05). "
    "Use o modo shadow para medir a regra contra o trafego real."
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


@router.post("/rules/dry-run")
async def dry_run_unsaved_rule(request: Request, body: dict) -> dict:
    """Dry-run de uma regra AINDA NÃO SALVA (a tool `rule_dry_run`). Recusa, dita — ver o topo.
    Corpo livre de propósito: a tool manda a regra no formato DELA, e validar o `Rule` aqui só
    trocaria o 501 honesto por um 422 sobre um formato que não vai ser usado."""
    authorize(request, str(body.get("tenant_id", "") or ""), write=True)
    raise HTTPException(status_code=501, detail=DRY_RUN_UNAVAILABLE)


@router.post("/rules/{rule_id}/dry-run", response_model=DryRunApiResponse)
async def dry_run_rule(
    request:   Request,
    rule_id:   str,
    body:      DryRunApiRequest,
    registry:  Annotated[RuleRegistry, Depends(get_registry)],
) -> DryRunApiResponse:
    """Dry-run de uma regra salva. Devolvia ZEROS como se tivesse medido; agora recusa, dita."""
    tenant_id = authorize(request, body.tenant_id, write=True)
    if await registry.get(tenant_id, rule_id) is None:
        raise HTTPException(status_code=404, detail=f"Rule not found: {rule_id}")
    raise HTTPException(status_code=501, detail=DRY_RUN_UNAVAILABLE)


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
