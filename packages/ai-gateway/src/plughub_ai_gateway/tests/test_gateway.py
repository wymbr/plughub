"""
test_gateway.py
AIGateway (legacy /v1/turn) tests.
Provider is mocked via the LLMProvider interface — no Anthropic SDK imported here.
"""

import pytest
from unittest.mock import AsyncMock

from ..gateway            import AIGateway
from ..providers.base     import LLMProvider, LLMResponse
from ..config             import ModelProfileConfig, FallbackConfig
from ..models             import TurnRequest, ConversationMessage


def _make_profiles() -> dict:
    return {
        "balanced": ModelProfileConfig(
            provider="anthropic",
            model_id="claude-sonnet-4-6",
            fallback=FallbackConfig(provider="anthropic", model_id="claude-haiku-4-5-20251001"),
        ),
    }


def _make_provider(content: str = "Entendo. Como posso ajudar?") -> AsyncMock:
    provider = AsyncMock(spec=LLMProvider)
    provider.call.return_value = LLMResponse(
        content=content,
        model_used="claude-sonnet-4-6",
        raw={"stop_reason": "end_turn", "usage": {"input_tokens": 100, "output_tokens": 50}},
        stop_reason="end_turn",
        tool_calls=[],
    )
    return provider


def make_turn_request(**kwargs) -> TurnRequest:
    defaults = {
        "session_id":    "session-001",
        "agent_id":      "agente_retencao_v1_inst_001",
        "tenant_id":     "tenant_telco",
        "system_prompt": "Você é um agente de retenção.",
        "messages": [
            ConversationMessage(role="customer", content="quero cancelar", timestamp=None)
        ],
    }
    defaults.update(kwargs)
    return TurnRequest(**defaults)


@pytest.mark.asyncio
async def test_process_turn_returns_valid_response():
    provider = _make_provider()
    gateway  = AIGateway(provider=provider, model_profiles=_make_profiles())

    result = await gateway.process_turn(make_turn_request())

    assert result.session_id    == "session-001"
    assert result.content       == "Entendo. Como posso ajudar?"
    assert result.stop_reason   == "end_turn"
    assert result.input_tokens  == 100
    assert result.output_tokens == 50
    assert result.latency_ms    >= 0
    assert provider.call.called


@pytest.mark.asyncio
async def test_extracts_churn_signal_flag_from_cancellation_message():
    provider = _make_provider("Posso oferecer um desconto.")
    gateway  = AIGateway(provider=provider, model_profiles=_make_profiles())

    result = await gateway.process_turn(make_turn_request())

    assert "churn_signal" in result.extracted_params.flags


@pytest.mark.asyncio
async def test_process_turn_with_tool_calls():
    provider = AsyncMock(spec=LLMProvider)
    provider.call.return_value = LLMResponse(
        content="",
        model_used="claude-sonnet-4-6",
        raw={"stop_reason": "tool_use", "usage": {"input_tokens": 120, "output_tokens": 20}},
        stop_reason="tool_use",
        tool_calls=[{"id": "tool_001", "name": "customer_get", "input": {"customer_id": "uuid-001"}}],
    )

    gateway = AIGateway(provider=provider, model_profiles=_make_profiles())
    result  = await gateway.process_turn(make_turn_request(
        tools=[{"name": "customer_get", "description": "Get customer", "input_schema": {}}]
    ))

    assert result.stop_reason   == "tool_use"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0]["name"] == "customer_get"
