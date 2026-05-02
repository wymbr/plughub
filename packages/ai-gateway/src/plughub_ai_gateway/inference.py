"""
inference.py
InferenceEngine — orchestrates provider, rate limit, cache and parameter extraction.
Spec: PlugHub v24.0 section 2.2a

Flow:
  1. Rate limit check
  2. Cache check → returns cached response on hit
  3. Resolve (provider, model_id, fallback) from model_profile
  4. Call provider.call() → fallback if ProviderError retryable
  5. Extract parameters (intent, confidence, sentiment, risk_flag)
  6. Write to Redis BEFORE returning
  7. Write to cache
  8. Return InferenceResponse
"""

from __future__ import annotations
import json
import logging
import time
from typing import Any

from .account_selector import AccountSelector
from .cache          import SemanticCache
from .context        import extract_context_from_response
from .models         import InferenceRequest, InferenceResponse
from .providers      import LLMProvider, LLMResponse, ProviderError
from .rate_limit     import RateLimiter, RateLimitExceeded
from .session        import SessionManager
from .usage_emitter  import emit_llm_tokens

logger = logging.getLogger("plughub.ai_gateway.inference")

# Flags that indicate risk — risk_flag=True when any of these is present
_RISK_FLAGS = {"high_frustration", "escalation_hint", "urgency"}


class InferenceEngine:
    """
    AI Gateway inference engine.
    Stateless — no in-memory state between calls.
    """

    def __init__(
        self,
        providers:        dict[str, LLMProvider],
        model_profiles:   dict[str, Any],     # ModelProfileConfig
        rate_limiter:     RateLimiter,
        cache:            SemanticCache,
        redis:            Any,
        session_ttl:      int = 86_400,
        max_tokens:       int = 1024,
        session_manager:  SessionManager | None = None,
        kafka_producer:   Any | None = None,   # aiokafka.AIOKafkaProducer ou duck-type
        gateway_id:       str = "ai-gateway",
        account_selector: AccountSelector | None = None,
    ) -> None:
        self._providers        = providers
        self._model_profiles   = model_profiles
        self._rate_limiter     = rate_limiter
        self._cache            = cache
        self._redis            = redis
        self._session_ttl      = session_ttl
        self._max_tokens       = max_tokens
        self._session_manager  = session_manager
        self._kafka_producer   = kafka_producer
        self._gateway_id       = gateway_id
        self._account_selector = account_selector

    async def infer(self, req: InferenceRequest) -> InferenceResponse:
        """
        Processes a complete inference call.
        Writes session parameters to Redis BEFORE returning.
        """
        start_time = time.time()

        # 1. Rate limit — raises RateLimitExceeded if exceeded
        await self._rate_limiter.check_and_increment(req.tenant_id, req.agent_type_id)

        # 2. Cache check
        messages_list = [m.model_dump() for m in req.messages]
        cached_data = await self._cache.get(req.tenant_id, messages_list)
        if cached_data is not None:
            return InferenceResponse(**{**cached_data, "cached": True})

        # 3. Resolve profile → (provider, model_id, fallback)
        profile = self._model_profiles.get(req.model_profile)
        if profile is None:
            raise ValueError(f"unknown model_profile: {req.model_profile}")

        tools_list = [t.model_dump() if hasattr(t, "model_dump") else t for t in (req.tools or [])]

        # Filter tools to only those permitted by the session token.
        # req.permissions comes from JWT permissions[] — populated by the proxy sidecar
        # or PlugHubAdapter before the inference call.
        # Empty permissions list → no filtering (backward compatible).
        if req.permissions:
            tools_list = [
                t for t in tools_list
                if t.get("name") in req.permissions
            ]
            logger.debug(
                "Tool permission filter applied: session=%s kept=%d/%d tools=%s",
                req.session_id,
                len(tools_list),
                len(req.tools or []),
                [t.get("name") for t in tools_list],
            )

        # 4. Call provider with automatic fallback
        llm_response, effective_provider = await self._call_with_fallback(
            profile=profile,
            messages=messages_list,
            tools=tools_list or None,
        )

        # 4b. Metering — publica tokens consumidos em usage.events (fire-and-forget)
        if self._kafka_producer is not None and not (llm_response.input_tokens == 0 and llm_response.output_tokens == 0):
            import asyncio
            asyncio.ensure_future(emit_llm_tokens(
                producer=       self._kafka_producer,
                tenant_id=      req.tenant_id,
                session_id=     req.session_id,
                model_id=       llm_response.model_used,
                agent_type_id=  req.agent_type_id,
                input_tokens=   llm_response.input_tokens,
                output_tokens=  llm_response.output_tokens,
                gateway_id=     self._gateway_id,
            ))

        # 5. Extract session parameters
        last_user_content = ""
        for m in reversed(req.messages):
            if m.role in ("customer", "user"):
                last_user_content = m.content
                break

        ctx = extract_context_from_response(
            user_message=last_user_content,
            assistant_response=llm_response.content,
            call_type="response_generation",
        )

        risk_flag = any(f in _RISK_FLAGS for f in ctx.flags)
        model_used = f"{effective_provider}/{llm_response.model_used}"

        # 6. Write parameters to Redis BEFORE returning
        await self._write_session_params(
            tenant_id=req.tenant_id,
            session_id=req.session_id,
            turn_id=req.turn_id,
            intent=ctx.intent,
            confidence=ctx.confidence,
            sentiment_score=ctx.sentiment_score,
            risk_flag=risk_flag,
            flags=ctx.flags,
        )

        # Notify Rules Engine via pub/sub (fire-and-forget).
        # A Rules Engine outage must never block the inference response path.
        if self._session_manager is not None:
            elapsed_ms = int((time.time() - start_time) * 1000)
            try:
                await self._session_manager.update_partial_params(
                    session_id=      req.session_id,
                    tenant_id=       req.tenant_id,
                    elapsed_ms=      elapsed_ms,
                    intent=          ctx.intent,
                    confidence=      ctx.confidence,
                    sentiment_score= ctx.sentiment_score,
                    flags=           ctx.flags,
                )
            except Exception as exc:
                logger.warning(
                    "Failed to notify Rules Engine for session %s: %s",
                    req.session_id, exc,
                )

        response_data: dict[str, Any] = {
            "content":         llm_response.content,
            "intent":          ctx.intent,
            "confidence":      ctx.confidence,
            "sentiment_score": ctx.sentiment_score,
            "risk_flag":       risk_flag,
            "model_used":      model_used,
            "cached":          False,
        }

        # 7. Write to cache
        await self._cache.set(req.tenant_id, messages_list, response_data)

        return InferenceResponse(**response_data)

    async def _call_with_fallback(
        self,
        profile:  Any,
        messages: list[dict],
        tools:    list[dict] | None,
    ) -> tuple[LLMResponse, str]:
        """
        Tries primary provider with AccountSelector-based account rotation;
        triggers fallback if ProviderError is retryable.
        Returns (LLMResponse, effective_provider_name).

        Account selection flow:
          1. If AccountSelector is configured, pick the least-loaded account
             for profile.provider → use that specific provider instance.
          2. On 429/529 (rate-limit): mark the picked account as throttled,
             then re-pick a different account (same provider) and retry once.
          3. If still failing (retryable): fall through to profile.fallback.
          4. Record RPM+TPM usage on every successful call.
        """
        primary_provider_name = profile.provider

        # ── Step 1: pick specific account via AccountSelector ──────────────
        provider_key: str | None = None
        if self._account_selector is not None:
            provider_key = await self._account_selector.pick(primary_provider_name)
            if provider_key is None:
                logger.warning(
                    "_call_with_fallback: all accounts throttled for provider=%s — jumping to fallback",
                    primary_provider_name,
                )
            else:
                provider = self._providers.get(provider_key)
                if provider is None:
                    logger.error(
                        "_call_with_fallback: AccountSelector picked %s but no matching provider instance",
                        provider_key,
                    )
                    provider_key = None  # fall through to generic lookup

        if provider_key is None:
            # No selector, or selector returned None — use generic provider alias
            provider = self._providers.get(primary_provider_name)
            if provider is None:
                raise ValueError(f"provider not registered: {primary_provider_name}")
            provider_key = primary_provider_name

        # ── Step 2: call primary account ───────────────────────────────────
        try:
            response = await provider.call(  # type: ignore[union-attr]
                messages=messages,
                tools=tools,
                model_id=profile.model_id,
                max_tokens=self._max_tokens,
            )
            # Record usage for rate-limit tracking
            if self._account_selector is not None and provider_key != primary_provider_name:
                await self._account_selector.record_usage(
                    provider_key,
                    tokens=response.input_tokens + response.output_tokens,
                )
            return response, primary_provider_name

        except ProviderError as e:
            if not e.retryable:
                raise

            # Mark this account throttled so AccountSelector avoids it
            if (
                self._account_selector is not None
                and provider_key != primary_provider_name
                and e.error_code in ("rate_limit", "status_429", "status_529")
            ):
                await self._account_selector.mark_throttled(provider_key, retry_after_seconds=60)
                logger.warning(
                    "_call_with_fallback: account %s throttled — retrying with another account",
                    provider_key,
                )
                # Retry once with the next available account
                retry_key = await self._account_selector.pick(primary_provider_name)
                if retry_key is not None and retry_key != provider_key:
                    retry_provider = self._providers.get(retry_key)
                    if retry_provider is not None:
                        try:
                            response = await retry_provider.call(
                                messages=messages,
                                tools=tools,
                                model_id=profile.model_id,
                                max_tokens=self._max_tokens,
                            )
                            await self._account_selector.record_usage(
                                retry_key,
                                tokens=response.input_tokens + response.output_tokens,
                            )
                            return response, primary_provider_name
                        except ProviderError:
                            pass  # fall through to model-level fallback

            if profile.fallback is None:
                raise

            # ── Step 3: model-level fallback ───────────────────────────────
            fallback_provider_name = profile.fallback.provider
            fallback_key: str | None = None

            if self._account_selector is not None:
                fallback_key = await self._account_selector.pick(fallback_provider_name)

            if fallback_key is None:
                fallback_provider = self._providers.get(fallback_provider_name)
                if fallback_provider is None:
                    raise ValueError(
                        f"fallback provider not registered: {fallback_provider_name}"
                    ) from e
                fallback_key = fallback_provider_name
            else:
                fallback_provider = self._providers.get(fallback_key)
                if fallback_provider is None:
                    raise ValueError(
                        f"fallback provider_key {fallback_key} not in providers dict"
                    ) from e

            response = await fallback_provider.call(
                messages=messages,
                tools=tools,
                model_id=profile.fallback.model_id,
                max_tokens=self._max_tokens,
            )
            if self._account_selector is not None and fallback_key != fallback_provider_name:
                await self._account_selector.record_usage(
                    fallback_key,
                    tokens=response.input_tokens + response.output_tokens,
                )
            return response, fallback_provider_name

    async def _write_session_params(
        self,
        tenant_id:       str,
        session_id:      str,
        turn_id:         str,
        intent:          str | None,
        confidence:      float,
        sentiment_score: float,
        risk_flag:       bool,
        flags:           list[str],
    ) -> None:
        """
        Writes extracted parameters to Redis immediately.
        Key: {tenant_id}:session:{session_id}:turn:{turn_id}:params
        TTL: session_ttl_seconds (renewed each turn).
        """
        key = f"{tenant_id}:session:{session_id}:turn:{turn_id}:params"
        data = {
            "intent":          intent,
            "confidence":      confidence,
            "sentiment_score": sentiment_score,
            "risk_flag":       risk_flag,
            "flags":           flags,
            "recorded_at":     int(time.time()),
        }
        await self._redis.set(key, json.dumps(data), ex=self._session_ttl)
