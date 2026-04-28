"""
main.py
AI Gateway FastAPI application.
Spec: PlugHub v24.0 section 2.2a

Routes:
  POST /inference  — single inference entry point (spec 2.2a)
  POST /v1/turn    — agent reasoning loop (legacy)
  POST /v1/reason  — structured output (Skill Flow reason step)
  GET  /v1/health  — healthcheck
"""

from __future__ import annotations
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

from .account_selector import AccountSelector, LLMAccount
from .cache      import SemanticCache
from .config     import get_settings
from .inference  import InferenceEngine
from .models     import (
    TurnRequest, TurnResponse,
    ReasonRequest, ReasonResponse,
    HealthResponse,
    InferenceRequest, InferenceResponse,
)
from .gateway    import AIGateway
from .providers  import AnthropicProvider, ProviderError
from .rate_limit import RateLimiter, RateLimitExceeded
from .reason     import ReasonEngine
from .session    import SessionManager, get_redis

try:
    from aiokafka import AIOKafkaProducer  # type: ignore[import-untyped]
    _AIOKAFKA_AVAILABLE = True
except ImportError:
    _AIOKAFKA_AVAILABLE = False


# ─────────────────────────────────────────────
# Lifespan — startup and teardown
# ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    settings = get_settings()

    # Shared infrastructure
    redis = await get_redis()

    # Kafka producer — optional, graceful degradation if broker unavailable
    kafka_producer = None
    kafka_brokers  = settings.kafka_brokers if hasattr(settings, "kafka_brokers") else "kafka:9092"
    if _AIOKAFKA_AVAILABLE:
        try:
            kafka_producer = AIOKafkaProducer(bootstrap_servers=kafka_brokers)
            await kafka_producer.start()
            logger.info("Kafka producer connected to %s", kafka_brokers)
        except Exception as exc:
            logger.warning("Kafka producer unavailable — metering disabled: %s", exc)
            kafka_producer = None

    # Provider registry — one provider instance per API key.
    # Keys registered as "anthropic:{key_id}" for AccountSelector, plus
    # "anthropic" alias pointing to the first key (backward compat for /v1/turn).
    providers: dict = {}
    accounts:  list[LLMAccount] = []

    anthropic_keys = settings.get_anthropic_keys()
    if not anthropic_keys:
        logger.warning("No Anthropic API keys configured (PLUGHUB_ANTHROPIC_API_KEY[S])")
    for api_key in anthropic_keys:
        acc = LLMAccount(
            provider="anthropic",
            api_key=api_key,
            rpm_limit=settings.anthropic_rpm_limit,
            tpm_limit=settings.anthropic_tpm_limit,
        )
        provider_instance = AnthropicProvider(api_key=api_key)
        providers[acc.provider_key] = provider_instance   # "anthropic:{key_id}"
        accounts.append(acc)

    # "anthropic" → first key  (used by /v1/turn + /v1/reason legacy paths)
    if anthropic_keys:
        first_key = accounts[0]
        providers["anthropic"] = providers[first_key.provider_key]

    # AccountSelector — load balances across all registered keys.
    # None when no accounts are configured (unit test / local dev without keys).
    account_selector = AccountSelector(redis, accounts) if accounts else None

    # Shared session manager — used by both /inference and /v1/turn
    session_mgr = SessionManager(redis, kafka_producer=kafka_producer)

    # InferenceEngine — orchestrates /inference
    app.state.inference_engine = InferenceEngine(
        providers=         providers,
        model_profiles=    settings.model_profiles,
        rate_limiter=      RateLimiter(redis, limit_per_minute=settings.rate_limit_rpm),
        cache=             SemanticCache(redis, ttl_seconds=settings.cache_ttl_seconds),
        redis=             redis,
        session_ttl=       settings.session_ttl_seconds,
        max_tokens=        settings.inference_max_tokens,
        session_manager=   session_mgr,
        kafka_producer=    kafka_producer,
        gateway_id=        getattr(settings, "gateway_id", "ai-gateway"),
        account_selector=  account_selector,
    )
    app.state.account_selector = account_selector

    # Legacy components (/v1/turn, /v1/reason) — share the same provider
    app.state.redis          = redis
    app.state.kafka_producer = kafka_producer
    app.state.gateway        = AIGateway(
        provider=anthropic_provider,
        model_profiles=settings.model_profiles,
    )
    app.state.reason_eng  = ReasonEngine(
        provider=anthropic_provider,
        model_profiles=settings.model_profiles,
    )
    app.state.session_mgr = session_mgr

    yield

    if kafka_producer is not None:
        await kafka_producer.stop()
    await redis.aclose()


app = FastAPI(
    title="PlugHub AI Gateway",
    version="1.0.0",
    description="Single LLM access point for the PlugHub Platform",
    lifespan=lifespan,
)


# ─────────────────────────────────────────────
# Error handlers
# ─────────────────────────────────────────────

@app.exception_handler(ProviderError)
async def provider_error_handler(request: Request, exc: ProviderError) -> JSONResponse:
    return JSONResponse(
        status_code=502,
        content={
            "error":     "upstream_model_error",
            "provider":  exc.provider,
            "code":      exc.error_code,
            "retryable": exc.retryable,
            "detail":    exc.message,
        },
    )

@app.exception_handler(RequestValidationError)
async def request_validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    print(f"[ai-gateway] RequestValidationError: {exc.errors()}", flush=True)
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()},
    )

@app.exception_handler(ValueError)
async def validation_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    import traceback
    print(f"[ai-gateway] ValueError in /v1/reason: {exc}", flush=True)
    traceback.print_exc()
    return JSONResponse(
        status_code=422,
        content={"error": "validation_error", "detail": str(exc)},
    )


# ─────────────────────────────────────────────
# POST /inference  — single inference entry point
# ─────────────────────────────────────────────

@app.post("/inference", response_model=InferenceResponse)
async def inference(req: InferenceRequest, request: Request) -> InferenceResponse:
    """
    Single LLM access point.
    Extracts session parameters and persists to Redis before returning.
    Spec 2.2a.
    """
    engine: InferenceEngine = request.app.state.inference_engine
    try:
        return await engine.infer(req)
    except RateLimitExceeded as e:
        raise HTTPException(status_code=429, detail=str(e))


# ─────────────────────────────────────────────
# Legacy routes
# ─────────────────────────────────────────────

@app.post("/v1/turn", response_model=TurnResponse)
async def turn(req: TurnRequest, request: Request) -> TurnResponse:
    """Agent reasoning loop (legacy — use /inference for new integrations)."""
    gateway     = request.app.state.gateway
    session_mgr = request.app.state.session_mgr

    response = await gateway.process_turn(req)

    await session_mgr.update_partial_params(
        session_id=      req.session_id,
        tenant_id=       req.tenant_id,
        elapsed_ms=      response.latency_ms,
        intent=          response.extracted_params.intent,
        confidence=      response.extracted_params.confidence,
        sentiment_score= response.extracted_params.sentiment_score,
        flags=           response.extracted_params.flags,
    )

    return response


@app.post("/v1/reason", response_model=ReasonResponse)
async def reason(req: ReasonRequest, request: Request) -> ReasonResponse:
    """
    Structured output for the Skill Flow reason step.

    After generating the structured result, updates session:{session_id}:ai
    so the Agent Assist supervisor dashboard (EstadoTab) stays fresh.
    Fields extracted opportunistically: if the operator's output_schema includes
    'intent', 'confidence', 'sentiment_score', or 'flags', their values are used;
    otherwise the fields default to neutral (None / 0.0 / []).
    This ensures every reason step call is visible in the supervisor state,
    even for flows that don't explicitly model these parameters.
    """
    engine      = request.app.state.reason_eng
    session_mgr = request.app.state.session_mgr
    start_time  = time.time()

    try:
        response = await engine.process(req)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Extract session parameters from the structured result — opportunistic:
    # use whatever the schema produced, fall back to neutral values otherwise.
    result          = response.result
    intent          = result.get("intent")          if isinstance(result.get("intent"),          str)          else None
    confidence      = float(result.get("confidence",      0.0)) if isinstance(result.get("confidence"),      (int, float)) else 0.0
    sentiment_score = float(result.get("sentiment_score", 0.0)) if isinstance(result.get("sentiment_score"), (int, float)) else 0.0
    flags           = result.get("flags",           [])  if isinstance(result.get("flags"),           list)         else []
    elapsed_ms      = int((time.time() - start_time) * 1000)

    try:
        await session_mgr.update_partial_params(
            session_id=      req.session_id,
            tenant_id=       req.tenant_id,
            elapsed_ms=      elapsed_ms,
            intent=          intent,
            confidence=      confidence,
            sentiment_score= sentiment_score,
            flags=           flags,
        )
    except Exception as exc:
        # Non-fatal — supervisor state is best-effort; the reason response
        # must always be returned to the skill flow engine regardless.
        logger.warning(
            "Failed to update session params after reason step: session=%s — %s",
            req.session_id, exc,
        )

    return response


@app.get("/v1/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """Healthcheck — verifies Redis and Anthropic connectivity."""
    settings = get_settings()

    redis_status = "ok"
    try:
        await request.app.state.redis.ping()
    except Exception:
        redis_status = "error"

    # Anthropic: ok if at least one key configured and not all accounts throttled
    anthropic_keys = settings.get_anthropic_keys()
    if not anthropic_keys:
        anthropic_status = "error"
    else:
        selector: AccountSelector | None = getattr(request.app.state, "account_selector", None)
        if selector is not None:
            best = await selector.pick("anthropic")
            anthropic_status = "ok" if best is not None else "degraded"
        else:
            anthropic_status = "ok"

    overall = "ok" if redis_status == "ok" and anthropic_status == "ok" else "degraded"

    return HealthResponse(
        status=overall,
        redis=redis_status,           # type: ignore[arg-type]
        anthropic=anthropic_status,   # type: ignore[arg-type]
    )


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────


if __name__ == "__main__":
    import uvicorn
    settings = get_settings()
    uvicorn.run(
        "plughub_ai_gateway.main:app",
        host=settings.host,
        port=settings.port,
        workers=settings.workers,
        reload=False,
    )
