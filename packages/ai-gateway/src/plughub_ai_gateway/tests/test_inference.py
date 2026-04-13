"""
test_inference.py
InferenceEngine and POST /inference route tests.
Spec: PlugHub v24.0 section 2.2a

Covers:
  1. AnthropicProvider returns normalised LLMResponse (SDK mock)
  2. Intent/confidence extraction operates on LLMResponse.content (neutral format)
  3. Integration: parameters written to Redis before HTTP response returns
  4. Fallback: retryable ProviderError triggers alternative provider
  5. Rate limit: 11th call returns 429 with limit=10/min
  6. Semantic cache: second call with same prompt — cached=True, provider not called
  7. Interface isolation: FakeProvider works without any Anthropic SDK import
"""

from __future__ import annotations
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from ..providers.base             import LLMProvider, LLMResponse, ProviderError
from ..providers.anthropic_provider import AnthropicProvider
from ..context                    import extract_context_from_response
from ..cache                      import SemanticCache
from ..rate_limit                 import RateLimiter, RateLimitExceeded
from ..inference                  import InferenceEngine
from ..config                     import ModelProfileConfig, FallbackConfig
from ..models                     import InferenceRequest, InferenceMessage
from ..session                    import SessionManager


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _make_redis_mock() -> AsyncMock:
    """Redis mock with basic get/set/incr/expire behaviour."""
    redis = AsyncMock()
    redis.get.return_value    = None   # cache miss by default
    redis.set.return_value    = True
    redis.incr.return_value   = 1      # first call in the window
    redis.expire.return_value = True
    redis.scan.return_value   = (0, [])
    return redis


def _make_profiles() -> dict[str, ModelProfileConfig]:
    return {
        "fast": ModelProfileConfig(
            provider="primary",
            model_id="model-fast",
            fallback=FallbackConfig(provider="fallback", model_id="model-fallback"),
        ),
        "balanced": ModelProfileConfig(
            provider="primary",
            model_id="model-balanced",
            fallback=FallbackConfig(provider="fallback", model_id="model-fallback"),
        ),
    }


def _make_request(**kwargs) -> InferenceRequest:
    defaults = {
        "session_id":    "sess-001",
        "turn_id":       "turn-001",
        "tenant_id":     "tenant_telco",
        "agent_type_id": "agente_retencao_v1",
        "model_profile": "balanced",
        "messages":      [InferenceMessage(role="customer", content="quero cancelar minha linha")],
    }
    defaults.update(kwargs)
    return InferenceRequest(**defaults)


def _llm_response(content: str = "Entendo, vou verificar.") -> LLMResponse:
    return LLMResponse(
        content=content,
        model_used="model-balanced",
        raw={"stop_reason": "end_turn", "usage": {"input_tokens": 50, "output_tokens": 20}},
        stop_reason="end_turn",
    )


# ─────────────────────────────────────────────
# 1. AnthropicProvider returns normalised LLMResponse
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_anthropic_provider_returns_normalised_llm_response():
    """
    AnthropicProvider.call() must return LLMResponse with normalised fields.
    All Anthropic SDK knowledge is confined to the provider.
    """
    # Mock of the native Anthropic response
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = "Posso ajudar com a portabilidade."

    mock_response = MagicMock()
    mock_response.content     = [text_block]
    mock_response.stop_reason = "end_turn"
    mock_response.id          = "msg_abc123"
    mock_response.usage       = MagicMock(input_tokens=80, output_tokens=30)

    provider = AnthropicProvider(api_key="sk-test")

    with patch.object(provider._client.messages, "create", new=AsyncMock(return_value=mock_response)):
        result = await provider.call(
            messages=[{"role": "customer", "content": "quero portabilidade"}],
            tools=None,
            model_id="claude-sonnet-4-6",
            max_tokens=1024,
        )

    # Verify that the return is a normalised LLMResponse
    assert isinstance(result, LLMResponse)
    assert result.content    == "Posso ajudar com a portabilidade."
    assert result.model_used == "claude-sonnet-4-6"
    assert result.stop_reason == "end_turn"
    assert "usage" in result.raw
    assert result.raw["usage"]["input_tokens"] == 80


# ─────────────────────────────────────────────
# 2. Extraction operates on neutral format (not Anthropic format)
# ─────────────────────────────────────────────

def test_intent_confidence_extraction_operates_on_llm_response_content():
    """
    Intent and confidence extraction operates on LLMResponse.content
    (neutral text string) — never on the native Anthropic format.
    extract_context_from_response does not import anything from the Anthropic SDK.
    """
    # Build LLMResponse as if it came from ANY provider
    response = LLMResponse(
        content     = "quero portabilidade da minha linha para outra operadora",
        model_used  = "any-model",
        raw         = {},
        stop_reason = "end_turn",
    )

    # Extraction operates exclusively on the content field (string)
    ctx = extract_context_from_response(
        user_message       = response.content,
        assistant_response = "Posso ajudar com portabilidade.",
        call_type          = "intent_classification",
    )

    assert ctx.intent == "portability_check"
    assert ctx.confidence > 0.5
    # Confirm there is no reference to any Anthropic type
    assert isinstance(ctx.intent, str)
    assert isinstance(ctx.confidence, float)


# ─────────────────────────────────────────────
# 3. Integration: parameters in Redis before response returns
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_parameters_written_to_redis_before_returning():
    """
    After InferenceEngine.infer(), session parameters must be in Redis
    with the correct key BEFORE the method returns.
    Spec 2.2a: 'written to Redis immediately, without waiting for end of turn'.
    """
    redis = _make_redis_mock()

    # Record redis.set calls in order (without calling the original mock to avoid recursion)
    set_calls: list[str] = []

    async def recording_set(key: str, *args, **kwargs) -> bool:
        set_calls.append(key)
        return True

    redis.set.side_effect = recording_set

    # Fake provider that returns a response immediately
    fake_provider = AsyncMock(spec=LLMProvider)
    fake_provider.call.return_value = _llm_response()

    engine = InferenceEngine(
        providers      = {"primary": fake_provider},
        model_profiles = _make_profiles(),
        rate_limiter   = RateLimiter(redis, limit_per_minute=100),
        cache          = SemanticCache(redis),
        redis          = redis,
    )

    req = _make_request()
    response = await engine.infer(req)

    # Verify that set was called with the params key
    params_key = f"{req.tenant_id}:session:{req.session_id}:turn:{req.turn_id}:params"
    assert any(params_key in k for k in set_calls), (
        f"Params key not found in Redis. Calls: {set_calls}"
    )
    assert response is not None


# ─────────────────────────────────────────────
# 4. Automatic fallback when primary provider returns retryable ProviderError
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fallback_triggered_when_primary_provider_returns_retryable_error():
    """
    When the primary provider raises ProviderError(retryable=True),
    InferenceEngine must trigger the configured fallback transparently —
    the caller never knows which provider was used.
    """
    redis = _make_redis_mock()

    # Primary provider: fails with retryable error
    primary_provider = AsyncMock(spec=LLMProvider)
    primary_provider.call.side_effect = ProviderError(
        provider="primary",
        error_code="rate_limit",
        retryable=True,
        message="429 Too Many Requests",
    )

    # Fallback provider: responds normally
    fallback_response = _llm_response("Resposta do fallback.")
    fallback_provider = AsyncMock(spec=LLMProvider)
    fallback_provider.call.return_value = fallback_response

    engine = InferenceEngine(
        providers      = {"primary": primary_provider, "fallback": fallback_provider},
        model_profiles = _make_profiles(),
        rate_limiter   = RateLimiter(redis, limit_per_minute=100),
        cache          = SemanticCache(redis),
        redis          = redis,
    )

    result = await engine.infer(_make_request())

    # Fallback must have been called
    assert fallback_provider.call.called
    assert result.content == "Resposta do fallback."
    # model_used must include the fallback provider
    assert "fallback" in result.model_used or "model-fallback" in result.model_used


@pytest.mark.asyncio
async def test_does_not_trigger_fallback_when_provider_error_not_retryable():
    """ProviderError(retryable=False) does not trigger fallback — propagates the error."""
    redis = _make_redis_mock()

    primary_provider = AsyncMock(spec=LLMProvider)
    primary_provider.call.side_effect = ProviderError(
        provider="primary",
        error_code="invalid_request",
        retryable=False,
        message="Requisição inválida",
    )

    fallback_provider = AsyncMock(spec=LLMProvider)

    engine = InferenceEngine(
        providers      = {"primary": primary_provider, "fallback": fallback_provider},
        model_profiles = _make_profiles(),
        rate_limiter   = RateLimiter(redis, limit_per_minute=100),
        cache          = SemanticCache(redis),
        redis          = redis,
    )

    with pytest.raises(ProviderError) as exc_info:
        await engine.infer(_make_request())

    assert not fallback_provider.call.called
    assert exc_info.value.retryable is False


# ─────────────────────────────────────────────
# 5. Rate limit: 11th call returns 429 with limit=10/min
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_rate_limit_11th_call_exceeds_limit():
    """
    With limit set to 10/minute, the first 10 calls pass.
    The 11th must raise RateLimitExceeded (→ HTTP 429 at the route).
    """
    # Redis mock with simulated real counter
    counter: dict[str, int] = {}

    async def mock_incr(key: str) -> int:
        counter[key] = counter.get(key, 0) + 1
        return counter[key]

    async def mock_expire(key: str, ttl: int) -> bool:
        return True

    redis = AsyncMock()
    redis.incr.side_effect   = mock_incr
    redis.expire.side_effect = mock_expire

    limiter = RateLimiter(redis, limit_per_minute=10)

    # 10 calls must pass
    for i in range(10):
        await limiter.check_and_increment("tenant_telco", "agente_retencao_v1")

    # 11th must fail
    with pytest.raises(RateLimitExceeded) as exc_info:
        await limiter.check_and_increment("tenant_telco", "agente_retencao_v1")

    assert exc_info.value.limit == 10
    assert exc_info.value.tenant_id == "tenant_telco"


# ─────────────────────────────────────────────
# 6. Semantic cache: second call returns cached=True without calling provider
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_semantic_cache_second_call_returns_cached_true():
    """
    Second call with the same prompt must:
    - Return cached=True
    - NOT call any LLM provider
    """
    # Redis with cache that persists between calls
    cache_store: dict[str, str] = {}

    async def mock_get(key: str) -> str | None:
        return cache_store.get(key)

    async def mock_set(key: str, value: str, ex: int | None = None) -> bool:
        cache_store[key] = value
        return True

    async def mock_incr(key: str) -> int:
        return 1

    async def mock_expire(key: str, ttl: int) -> bool:
        return True

    redis = AsyncMock()
    redis.get.side_effect    = mock_get
    redis.set.side_effect    = mock_set
    redis.incr.side_effect   = mock_incr
    redis.expire.side_effect = mock_expire

    fake_provider = AsyncMock(spec=LLMProvider)
    fake_provider.call.return_value = _llm_response("Resposta para portabilidade.")

    engine = InferenceEngine(
        providers      = {"primary": fake_provider},
        model_profiles = _make_profiles(),
        rate_limiter   = RateLimiter(redis, limit_per_minute=100),
        cache          = SemanticCache(redis, ttl_seconds=300),
        redis          = redis,
    )

    req = _make_request()

    # First call — goes to provider
    first = await engine.infer(req)
    assert first.cached is False
    assert fake_provider.call.call_count == 1

    # Second call — same prompt → cache hit
    second = await engine.infer(req)
    assert second.cached is True
    # Provider must NOT have been called again
    assert fake_provider.call.call_count == 1, (
        "Provider was called on the second request — cache did not work"
    )
    assert second.content == first.content


# ─────────────────────────────────────────────
# 7. Interface isolation: FakeProvider without Anthropic SDK import
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_interface_isolation_fake_provider_without_anthropic_sdk():
    """
    Replacing AnthropicProvider with a FakeProvider that implements
    LLMProvider confirms that no line in InferenceEngine (outside of
    providers/) knows the Anthropic SDK directly.

    The engine must work correctly with any provider that respects
    the LLMProvider.call() → LLMResponse contract.
    """

    class FakeProvider(LLMProvider):
        """Fake provider — implements the contract without any external imports."""

        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def call(
            self,
            messages:   list[dict],
            tools:      list[dict] | None,
            model_id:   str,
            max_tokens: int,
        ) -> LLMResponse:
            self.calls.append({"messages": messages, "model_id": model_id})
            return LLMResponse(
                content     = "Resposta do FakeProvider sem SDK Anthropic.",
                model_used  = model_id,
                raw         = {"fake": True},
                stop_reason = "end_turn",
            )

    redis = _make_redis_mock()
    fake  = FakeProvider()

    # Engine configured with FakeProvider — without AnthropicProvider
    engine = InferenceEngine(
        providers = {
            "primary": fake,
        },
        model_profiles = {
            "balanced": ModelProfileConfig(
                provider="primary",
                model_id="fake-model-v1",
            ),
        },
        rate_limiter = RateLimiter(redis, limit_per_minute=100),
        cache        = SemanticCache(redis),
        redis        = redis,
    )

    result = await engine.infer(_make_request())

    # Engine worked without any access to the Anthropic SDK
    assert result.content == "Resposta do FakeProvider sem SDK Anthropic."
    assert result.model_used == "primary/fake-model-v1"
    assert len(fake.calls) == 1
    assert fake.calls[0]["model_id"] == "fake-model-v1"

    # Session parameters were extracted and the cached flag is present
    assert isinstance(result.cached, bool)
    assert isinstance(result.confidence, float)
    assert isinstance(result.risk_flag, bool)


# ─────────────────────────────────────────────
# 8. Rules Engine notification via session_manager
# ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_session_manager_update_called_after_inference():
    """
    When session_manager is injected, update_partial_params() must be called
    once per infer() call with the correct session_id, tenant_id and extracted
    parameters — so the Rules Engine receives the pub/sub notification.
    """
    redis = _make_redis_mock()

    fake_provider = AsyncMock(spec=LLMProvider)
    fake_provider.call.return_value = _llm_response()

    session_mgr = AsyncMock(spec=SessionManager)

    engine = InferenceEngine(
        providers=       {"primary": fake_provider},
        model_profiles=  _make_profiles(),
        rate_limiter=    RateLimiter(redis, limit_per_minute=100),
        cache=           SemanticCache(redis),
        redis=           redis,
        session_manager= session_mgr,
    )

    req = _make_request()
    await engine.infer(req)

    session_mgr.update_partial_params.assert_awaited_once()
    call_kwargs = session_mgr.update_partial_params.call_args.kwargs
    assert call_kwargs["session_id"] == req.session_id
    assert call_kwargs["tenant_id"]  == req.tenant_id
    assert isinstance(call_kwargs["elapsed_ms"],      int)
    assert isinstance(call_kwargs["confidence"],      float)
    assert isinstance(call_kwargs["sentiment_score"], float)
    assert isinstance(call_kwargs["flags"],           list)


@pytest.mark.asyncio
async def test_session_manager_none_does_not_raise():
    """
    When session_manager=None (default), infer() must complete normally
    without attempting a pub/sub publish — engine works without Rules Engine.
    """
    redis = _make_redis_mock()

    fake_provider = AsyncMock(spec=LLMProvider)
    fake_provider.call.return_value = _llm_response()

    engine = InferenceEngine(
        providers=      {"primary": fake_provider},
        model_profiles= _make_profiles(),
        rate_limiter=   RateLimiter(redis, limit_per_minute=100),
        cache=          SemanticCache(redis),
        redis=          redis,
        # session_manager intentionally omitted
    )

    result = await engine.infer(_make_request())
    assert result is not None
