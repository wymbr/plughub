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
import asyncio
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

import logging

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

# O ai-gateway nunca configurou logging. Sem handler, o Python cai no `lastResort`:
# escreve só a MENSAGEM em stderr, sem nível nem timestamp, e descarta tudo abaixo de
# WARNING. Efeito prático: `logger.error` e `logger.warning` saíam idênticos (a
# severidade do upstream_model_error era invisível) e todo `logger.info` sumia.
# Mesmo formato dos demais serviços Python (session-replayer/main.py:14).
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

from .account_selector      import AccountSelector, LLMAccount
from .cache                 import SemanticCache
from .config                import get_settings
from .inference              import InferenceEngine
from .llm_accounts_catalog  import load_llm_accounts_catalog
from .models     import (
    TurnRequest, TurnResponse,
    ReasonRequest, ReasonResponse,
    HealthResponse,
    InferenceRequest, InferenceResponse,
    CopilotAnalyzeRequest,
)
from .copilot_emitter import analyze_for_copilot
from .sentiment_analyzer import analyze_and_emit_sentiment
from .gateway    import AIGateway
from .providers  import AnthropicProvider, OpenAIProvider, ProviderError
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

async def _probe_credentials_on_boot(
    providers: dict,
    accounts:  list[LLMAccount],
    selector:  AccountSelector,
    settings,
) -> None:
    """
    Uma chamada mínima ao provedor por conta, gravando o desfecho.

    Nunca derruba o boot: um provedor fora do ar não deve impedir o serviço de
    subir (as demais rotas não dependem dele). Mas o resultado é sempre
    REGISTRADO — inclusive o timeout, que vira `connection_error` e não silêncio.
    """
    for acc in accounts:
        provider = providers.get(acc.provider_key)
        if provider is None:
            continue
        try:
            await asyncio.wait_for(
                provider.call(
                    messages=[{"role": "user", "content": "ping"}],
                    tools=None,
                    model_id=settings.model_for_profile("fast"),
                    max_tokens=1,
                ),
                timeout=settings.llm_boot_probe_timeout_s,
            )
            await selector.record_outcome(acc.provider_key, ok=True)
            logger.info(
                "boot probe: credencial OK provider=%s key_id=%s config_id=%s",
                acc.provider, acc.key_id, acc.config_id or "-",
            )
        except ProviderError as exc:
            await selector.record_outcome(
                acc.provider_key, ok=False,
                error_code=exc.error_code, message=exc.message,
            )
            logger.error(
                "boot probe: credencial RECUSADA provider=%s key_id=%s code=%s — %s",
                acc.provider, acc.key_id, exc.error_code, exc.message[:200],
            )
        except asyncio.TimeoutError:
            await selector.record_outcome(
                acc.provider_key, ok=False,
                error_code="connection_error",
                message=f"boot probe timeout após {settings.llm_boot_probe_timeout_s}s",
            )
            logger.warning(
                "boot probe: TIMEOUT provider=%s key_id=%s", acc.provider, acc.key_id,
            )
        except Exception as exc:
            # Exceção inesperada não pode virar `unknown` mudo: `unknown` significa
            # "não medimos", e aqui medimos e deu errado. Nomear é o mínimo.
            await selector.record_outcome(
                acc.provider_key, ok=False,
                error_code="probe_error", message=str(exc),
            )
            logger.error(
                "boot probe: erro inesperado provider=%s key_id=%s — %s",
                acc.provider, acc.key_id, exc,
            )


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

    anthropic_keys       = settings.get_anthropic_keys()
    anthropic_config_ids = settings.get_anthropic_config_ids()
    if not anthropic_keys:
        logger.warning("No Anthropic API keys configured (PLUGHUB_ANTHROPIC_API_KEY[S])")
    for idx, api_key in enumerate(anthropic_keys):
        acc = LLMAccount(
            provider="anthropic",
            api_key=api_key,
            rpm_limit=settings.anthropic_rpm_limit,
            tpm_limit=settings.anthropic_tpm_limit,
            config_id=anthropic_config_ids[idx] if idx < len(anthropic_config_ids) else "",
        )
        provider_instance = AnthropicProvider(api_key=api_key)
        providers[acc.provider_key] = provider_instance   # "anthropic:{key_id}"
        accounts.append(acc)

    # "anthropic" → first key  (used by /v1/turn + /v1/reason legacy paths)
    if anthropic_keys:
        first_key = accounts[0]
        providers["anthropic"] = providers[first_key.provider_key]

    # OpenAI — optional fallback provider (multi-key support)
    openai_keys       = settings.get_openai_keys()
    openai_config_ids = settings.get_openai_config_ids()
    for idx, api_key in enumerate(openai_keys):
        acc = LLMAccount(
            provider="openai",
            api_key=api_key,
            rpm_limit=settings.openai_rpm_limit,
            tpm_limit=settings.openai_tpm_limit,
            config_id=openai_config_ids[idx] if idx < len(openai_config_ids) else "",
        )
        provider_instance = OpenAIProvider(api_key=api_key)
        providers[acc.provider_key] = provider_instance   # "openai:{key_id}"
        accounts.append(acc)

    if openai_keys:
        providers["openai"] = providers[
            LLMAccount(provider="openai", api_key=openai_keys[0]).provider_key
        ]

    # LLM Accounts — Config API catalog (namespace `llm_accounts`) takes over when
    # non-empty: replaces the legacy positional PLUGHUB_ANTHROPIC_API_KEYS/CONFIG_IDS
    # mechanism above with explicit account ids resolved from PLUGHUB_LLM_ACCOUNT_
    # <ID>_API_KEY env vars. Graceful — catalog unreachable/empty keeps the legacy
    # accounts/providers built above untouched.
    try:
        catalog_accounts = await load_llm_accounts_catalog(
            settings.config_api_url, settings.tenant_id,
        )
    except Exception as exc:
        logger.warning("llm_accounts catalog load failed — using legacy env accounts: %s", exc)
        catalog_accounts = []

    if catalog_accounts:
        providers = {}
        accounts  = []
        for cat_acc in catalog_accounts:
            acc = LLMAccount(
                provider=cat_acc.provider,
                api_key=cat_acc.api_key,
                rpm_limit=cat_acc.rpm_limit,
                tpm_limit=cat_acc.tpm_limit,
                config_id=cat_acc.account_id,
            )
            provider_instance = (
                AnthropicProvider(api_key=cat_acc.api_key) if cat_acc.provider == "anthropic"
                else OpenAIProvider(api_key=cat_acc.api_key)
            )
            providers[acc.provider_key] = provider_instance
            accounts.append(acc)
        # "anthropic"/"openai" aliases → first catalog account of that provider
        # (backward compat for /v1/turn and the legacy ReasonEngine fallback).
        for alias in ("anthropic", "openai"):
            first = next((a for a in accounts if a.provider == alias), None)
            if first is not None:
                providers[alias] = providers[first.provider_key]
        logger.info(
            "llm_accounts catalog: %d account(s) loaded (%s) — legacy env mechanism overridden",
            len(accounts), ", ".join(a.config_id for a in accounts),
        )

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

    # Providers por NOME, publicados explicitamente. Existe porque a medição de
    # sentimento precisa de um provider fora do turno, e a versão anterior o
    # buscava em `inference_engine.providers` — atributo que a classe não tem (é
    # `_providers`, privado). `getattr(..., "providers", {})` devolvia `{}` sempre,
    # e a medição NUNCA rodou: o default do getattr transformou "o atributo não
    # existe" em "não há provider configurado", que é um fato diferente e plausível.
    # Alcançar o privado de outro objeto teria o mesmo defeito com outra grafia —
    # a dependência é declarada aqui, onde o dict é construído.
    app.state.llm_providers = providers

    # Legacy components (/v1/turn, /v1/reason) — use the "anthropic" alias provider.
    # Falls back to None when no Anthropic API key is configured (dev / test mode).
    _legacy_provider = providers.get("anthropic")
    app.state.redis          = redis
    app.state.kafka_producer = kafka_producer
    app.state.gateway        = AIGateway(
        provider=_legacy_provider,
        model_profiles=settings.model_profiles,
    )
    app.state.reason_eng  = ReasonEngine(
        provider=_legacy_provider,
        model_profiles=settings.model_profiles,
        max_tokens=settings.inference_max_tokens,
        # LLM Accounts — enables preferred_config_ids account selection for
        # reason steps (previously hardcoded to the single legacy alias above).
        providers=providers,
        account_selector=account_selector,
    )
    app.state.session_mgr = session_mgr

    # ── Sonda de credencial no boot ──────────────────────────────────────────
    # Uma chamada mínima por conta, uma vez por start do container. Existe porque o
    # registro passivo (funil de erro + record_usage) só aprende com TRÁFEGO: num
    # ambiente ocioso o estado nasceria `unknown` e ficaria assim — que é
    # exatamente o cenário em que o defeito de 08-22 se escondeu.
    #
    # Não é healthcheck ativo: o healthcheck do compose bate a cada 10 s e sondar
    # ali gastaria cota continuamente. Uma vez no boot é o instante em que a
    # pergunta "esta chave serve?" tem resposta útil e custo desprezível.
    #
    # Desligável (`PLUGHUB_LLM_BOOT_PROBE=false`) para teste offline — e desligá-la
    # NÃO produz `ok`: produz `unknown`, que o health publica como tal.
    if settings.llm_boot_probe and account_selector is not None:
        await _probe_credentials_on_boot(providers, accounts, account_selector, settings)
    elif account_selector is not None:
        logger.info("llm_boot_probe desligado — credencial fica `unknown` até haver tráfego")

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
    # O motivo vai no CORPO e no LOG. Antes ia só no corpo: o acesso via uvicorn
    # registrava `POST /v1/reason 502` e nada mais, então saldo zerado, chave
    # revogada, rate-limit e modelo inexistente eram indistinguíveis sem sondar
    # o endpoint à mão — e um 502 mudo derruba TODO agente de IA da plataforma
    # sem dizer por quê (mesma patologia do `except: pass`).
    #
    # `retryable=False` é o caso que merece atenção humana: nenhuma rotação de
    # conta ou tentativa posterior resolve (ex.: "credit balance is too low"),
    # então sobe a ERROR. O retryable degrada a WARNING — o AccountSelector
    # ainda pode contornar via outra conta/provedor.
    log = logger.error if not exc.retryable else logger.warning
    log(
        "upstream_model_error path=%s provider=%s code=%s retryable=%s detail=%s",
        request.url.path, exc.provider, exc.error_code, exc.retryable, exc.message,
    )

    # ── E o fato vira ESTADO, não só linha de log ────────────────────────────
    # Este é o funil ÚNICO por onde passa toda falha de provedor. Até 2026-08-23 ele
    # logava e esquecia: o `/v1/health` respondia `ok` durante 124 recusas seguidas
    # de credencial, e a única evidência morria no recreate do container. Registrar
    # aqui é o mínimo que faz o health poder ser honesto.
    selector: AccountSelector | None = getattr(request.app.state, "account_selector", None)
    if selector is not None and exc.account_key_id:
        await selector.record_outcome(
            f"{exc.provider}:{exc.account_key_id}",
            ok=False,
            error_code=exc.error_code,
            message=exc.message,
        )
    elif selector is not None:
        # Erro sem conta identificada não deveria existir (todo provider carimba o
        # seu key_id). Se aparecer, é um caminho de construção de provider fora do
        # registro de contas — barulho de propósito, não `pass`.
        logger.warning(
            "upstream_model_error SEM account_key_id provider=%s code=%s — "
            "desfecho não registrado, o health ficará `unknown` para esta conta",
            exc.provider, exc.error_code,
        )

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
    flags           = result.get("flags",           [])  if isinstance(result.get("flags"),           list)         else []
    elapsed_ms      = int((time.time() - start_time) * 1000)

    # `sentiment_score` deixou de cair para 0.0 quando o schema não o declara
    # (2026-08-23). Zero é NEUTRO — um ponto legítimo da escala —, então o default
    # antigo publicava "cliente neutro" em toda sessão da plataforma e ninguém
    # conseguia distinguir isso de medição real. `None` = não medido, e o
    # `update_partial_params` pula o pipeline em vez de propagar a mentira.
    #
    # Quando o schema DECLARA o campo, o valor é honrado por compatibilidade — mas
    # é auto-reportado pelo modelo que está atendendo, não medido. A medição de
    # verdade vem do `sentiment_analyzer` disparado abaixo.
    sentiment_score = (
        float(result["sentiment_score"])
        if isinstance(result.get("sentiment_score"), (int, float))
        else None
    )

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

    # ── Medição de sentimento, fora do turno ─────────────────────────────────
    # Só roda quando o step NOMEOU a fala do cliente (`customer_utterance`). O
    # `input` do reason é opaco por contrato — o gateway não tem como adivinhar
    # qual chave é fala de cliente e qual é `pipeline_state`, e chutar produziria
    # score sobre texto de máquina.
    if req.customer_utterance:
        task = asyncio.create_task(
            analyze_and_emit_sentiment(
                redis              = request.app.state.redis,
                provider           = sentiment_provider(request.app.state),
                producer           = request.app.state.kafka_producer,
                tenant_id          = req.tenant_id,
                session_id         = req.session_id,
                customer_utterance = req.customer_utterance,
                model_id           = get_settings().model_for_profile("fast"),
            )
        )
        # Task de background sem observador morre CALADA: a exceção fica presa no
        # objeto Task e nada a lê. O callback é o que impede o serviço de ficar
        # verde com um produtor a menos.
        task.add_done_callback(_log_task_exception)

    return response


def sentiment_provider(state: Any) -> Any | None:
    """
    Resolve o provider usado pela medição de sentimento (chamada fora do turno).

    Existe como função NOMEADA porque os dois modos de "não há provider" precisam
    sair por portas diferentes, e a versão inline os juntava:

      · `llm_providers` ausente em `app.state` → **defeito de fiação**. Foi o bug
        real: o handler lia `inference_engine.providers`, atributo que a classe não
        tem (é `_providers`, privado), e `getattr(obj, "providers", {})` devolvia
        `{}` — silenciosamente indistinguível de "ambiente sem chave". A medição
        nunca rodou, e o log dizia "sem provider LLM", que soa como ambiente.
      · alias `anthropic` ausente no dict → **ambiente sem chave Anthropic**, que é
        degradação legítima e já é registrada pelo próprio analisador.

    Devolve None nos dois casos (nunca levanta: sentimento jamais derruba o turno),
    mas só o primeiro é ERROR.
    """
    registry = getattr(state, "llm_providers", None)
    if registry is None:
        logger.error(
            "sentimento: app.state.llm_providers AUSENTE — defeito de fiação no "
            "startup, não ambiente sem chave. Nada será medido."
        )
        return None
    return registry.get("anthropic")


def _log_task_exception(task: asyncio.Task) -> None:
    """Observa uma task fire-and-forget para que a exceção não morra em silêncio."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("task de background falhou: %s", exc, exc_info=exc)


@app.post("/v1/copilot/analyze", status_code=202)
async def copilot_analyze(req: CopilotAnalyzeRequest, request: Request) -> dict:
    """
    Co-pilot Phase 2 — background analysis of a customer message.

    Accepts the request immediately (202 Accepted) and schedules fire-and-forget
    analysis via asyncio.create_task. The LLM call is isolated to the "fast"
    model profile (haiku) to avoid competing with realtime agent workloads.

    On completion, writes session.copilot.* to ContextStore and publishes
    copilot.updated to agent:events:{session_id} so the Agent Assist UI
    refreshes its Capacidades tab via WebSocket.
    """
    import asyncio

    redis    = request.app.state.redis
    settings = get_settings()

    # Pick provider — use "anthropic" alias (first key, backward-compat with fire-and-forget use)
    # Falls back to None when no Anthropic key is configured (dev / test mode).
    provider = getattr(request.app.state, "gateway", None)
    if provider is not None:
        provider = getattr(provider, "_provider", None) or getattr(provider, "provider", None)
    # Simpler: use the providers dict stored on the inference engine
    engine: InferenceEngine = request.app.state.inference_engine
    provider = getattr(engine, "providers", {}).get("anthropic")

    model_id = settings.model_fast  # haiku — isolated from realtime agents

    asyncio.create_task(
        analyze_for_copilot(
            redis            = redis,
            provider         = provider,
            session_id       = req.session_id,
            tenant_id        = req.tenant_id,
            customer_message = req.customer_message,
            model_id         = model_id,
        )
    )

    return {"status": "accepted", "session_id": req.session_id}


@app.get("/v1/health", response_model=HealthResponse)
async def health(request: Request, response: Response) -> HealthResponse:
    """
    Healthcheck honesto — reporta o que foi MEDIDO, nunca o que foi configurado.

    Tabela de veredicto (o código HTTP faz parte dela: `docker ps` só lê isso):

      redis inalcançável ......................... unhealthy / 503
      nenhuma conta configurada .................. degraded  / 200  (escolha declarada:
                                                   demo sem LLM é legítimo)
      ≥1 conta com credencial `ok` ............... ok        / 200  (contas inválidas
                                                   continuam listadas em `accounts`)
      nenhuma `ok`, ≥1 `invalid` ................. unhealthy / 503  ← o caso de 08-22
      nenhuma `ok`, nenhuma `invalid` ............ unknown   / 200  + nota dizendo
                                                   que este health NÃO julga

    O último ramo é o que separa este endpoint do anterior: ausência de evidência
    sai como `unknown` e se DECLARA, em vez de virar `ok` por omissão.
    """
    redis_status = "ok"
    try:
        await request.app.state.redis.ping()
    except Exception as exc:
        redis_status = "error"
        logger.error("health: redis inalcançável — %s", exc)

    selector: AccountSelector | None = getattr(request.app.state, "account_selector", None)
    accounts  = await selector.credential_summary() if selector is not None else []
    counters  = await selector.outcome_counters()   if selector is not None else {}
    notes: list[str] = []

    states = {a["credentials"] for a in accounts}

    if not accounts:
        anthropic_status = "not_configured"
        notes.append(
            "Nenhuma conta de LLM configurada — o health NÃO julga credencial. "
            "Todo step `reason` cairá no `on_failure`, que é ramo legítimo de fluxo "
            "e portanto não acende alarme em lugar nenhum."
        )
    elif "ok" in states:
        anthropic_status = "ok"
        if "invalid" in states:
            notes.append(
                "Há conta com credencial recusada, mas ao menos uma funciona — "
                "serviço de pé, capacidade reduzida. Ver `accounts`."
            )
        # Credencial e DISPONIBILIDADE são fatos diferentes. Com todas as contas
        # throttled o `pick()` devolve None e toda chamada cai no alias legado ou
        # no fallback de provedor — a credencial está boa e a capacidade é zero.
        # Reportar `ok` aqui seria verde sobre indisponibilidade.
        if all(a["throttled"] for a in accounts):
            anthropic_status = "degraded"
            notes.append(
                "TODAS as contas estão throttled: a credencial é válida, mas o "
                "AccountSelector não tem conta a escolher e as chamadas caem no "
                "alias legado ou no fallback de provedor."
            )
    elif "invalid" in states:
        anthropic_status = "error"
    elif "error" in states:
        # Medimos, e falhou por causa TRANSITÓRIA (rate limit, rede, 5xx). Não é
        # `unknown` — há evidência — e não é `error` de credencial: reprovar o
        # healthcheck por um soluço de rede faria o container piscar e o sinal
        # perderia o valor que ele acabou de ganhar.
        anthropic_status = "degraded"
        notes.append(
            "Última chamada falhou por causa transitória, e nenhuma bem-sucedida "
            "depois dela. Ver `last_error_code` em `accounts`."
        )
    else:
        anthropic_status = "unknown"
        notes.append(
            "Nenhum desfecho de provedor registrado. Isto NÃO é saúde: é ausência "
            "de evidência. Causas: sonda de boot desligada "
            "(PLUGHUB_LLM_BOOT_PROBE), ou o serviço subiu e ainda não chamou o LLM."
        )

    if redis_status == "error":
        overall, code = "unhealthy", 503
    elif anthropic_status == "error":
        overall, code = "unhealthy", 503
    elif anthropic_status in ("not_configured", "degraded"):
        overall, code = "degraded", 200
    elif anthropic_status == "unknown":
        overall, code = "unknown", 200
    else:
        overall, code = "ok", 200

    # `calls_ok` SUBCONTA no caminho do alias legado: tanto `/inference` (:287)
    # quanto `/v1/reason` só chamam `record_usage` quando o AccountSelector
    # escolheu uma CONTA específica. Se `pick()` devolveu None — todas throttled,
    # ou nenhum selector — a chamada usa o alias `"anthropic"` e, mesmo tendo
    # sucesso, não incrementa nada. Dizer isso é mais barato que alguém ler
    # `calls_ok=0` como "o LLM não respondeu nenhuma vez".
    #
    # (Correção 2026-08-23: esta nota afirmou por algumas horas que `/inference`
    # não chamava `record_usage`. Era FALSO — ele chama em :288, :324 e :363. A
    # afirmação nasceu de um grep truncado lido como ausência.)
    if counters.get("available") and counters.get("calls_ok", 0) == 0 and accounts:
        notes.append(
            "calls_ok=0 na janela. Atenção: o contador só é alimentado quando o "
            "AccountSelector escolhe uma conta específica — chamadas que caem no "
            "alias legado do provedor têm sucesso sem incrementá-lo."
        )

    response.status_code = code
    return HealthResponse(
        status=overall,          # type: ignore[arg-type]
        redis=redis_status,      # type: ignore[arg-type]
        anthropic=anthropic_status,  # type: ignore[arg-type]
        accounts=accounts,
        counters=counters,
        notes=notes,
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
