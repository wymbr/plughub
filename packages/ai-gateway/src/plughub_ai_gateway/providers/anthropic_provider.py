"""
providers/anthropic_provider.py
Anthropic provider — default MVP implementation.
Spec: PlugHub v24.0 section 2.2a

Converts messages/tools to Anthropic format and returns LLMResponse.
All Anthropic SDK knowledge is confined to this file.
"""

from __future__ import annotations
from typing import Any

import anthropic

from .base import LLMProvider, LLMResponse, ProviderError

# Status codes that justify automatic fallback
_RETRYABLE_STATUS_CODES = {429, 529, 503, 502, 504}

_ROLE_MAP = {
    "customer": "user",
    "agent":    "assistant",
    "system":   "user",   # system injected via system= param, not messages
    "user":     "user",
    "assistant": "assistant",
}


class AnthropicProvider(LLMProvider):
    """
    Provider using the Anthropic SDK (AsyncAnthropic).
    All Anthropic SDK access stays inside this class.
    """

    def __init__(self, api_key: str) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key or None)

    async def call(
        self,
        messages:   list[dict],
        tools:      list[dict] | None,
        model_id:   str,
        max_tokens: int,
    ) -> LLMResponse:
        """
        Calls the Anthropic Messages API and returns a normalised LLMResponse.
        Never leaks anthropic.APIError outside this method.
        """
        try:
            # Convert roles to Anthropic format
            anthropic_messages = [
                {
                    "role":    _ROLE_MAP.get(m.get("role", "user"), "user"),
                    "content": m.get("content", ""),
                }
                for m in messages
                if m.get("role") != "system"  # system vai via system= param
            ]

            # Convert tools to Anthropic format
            anthropic_tools: list[Any] = []
            if tools:
                for t in tools:
                    anthropic_tools.append({
                        "name":         t.get("name", ""),
                        "description":  t.get("description", ""),
                        "input_schema": t.get("input_schema", {"type": "object", "properties": {}}),
                    })

            kwargs: dict[str, Any] = {
                "model":      model_id,
                "max_tokens": max_tokens,
                "messages":   anthropic_messages,
            }
            if anthropic_tools:
                kwargs["tools"] = anthropic_tools

            # System prompt extracted from messages with role=system
            system_msgs = [m.get("content", "") for m in messages if m.get("role") == "system"]
            if system_msgs:
                kwargs["system"] = " ".join(system_msgs)

            response = await self._client.messages.create(**kwargs)

            # Extract text and tool_use blocks from the response
            content_text = ""
            tool_calls: list[Any] = []
            for block in response.content:
                if hasattr(block, "type") and block.type == "text":
                    content_text = block.text
                elif hasattr(block, "type") and block.type == "tool_use":
                    tool_calls.append({
                        "id":    getattr(block, "id", ""),
                        "name":  getattr(block, "name", ""),
                        "input": getattr(block, "input", {}),
                    })

            raw: dict[str, Any] = {
                "id":           getattr(response, "id", ""),
                "stop_reason":  response.stop_reason,
                "usage":        {
                    "input_tokens":  response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                },
            }

            return LLMResponse(
                content=content_text,
                model_used=model_id,
                raw=raw,
                stop_reason=response.stop_reason or "end_turn",
                tool_calls=tool_calls,
            )

        except anthropic.RateLimitError as e:
            raise ProviderError(
                provider="anthropic",
                error_code="rate_limit",
                retryable=True,
                message=str(e),
            )
        except anthropic.APIConnectionError as e:
            raise ProviderError(
                provider="anthropic",
                error_code="connection_error",
                retryable=True,
                message=str(e),
            )
        except anthropic.APIStatusError as e:
            retryable = e.status_code in _RETRYABLE_STATUS_CODES
            raise ProviderError(
                provider="anthropic",
                error_code=f"status_{e.status_code}",
                retryable=retryable,
                message=str(e),
            )
        except anthropic.APIError as e:
            raise ProviderError(
                provider="anthropic",
                error_code="api_error",
                retryable=False,
                message=str(e),
            )
