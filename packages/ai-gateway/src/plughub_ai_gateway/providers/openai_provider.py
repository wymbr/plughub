"""
providers/openai_provider.py
OpenAI provider — optional fallback for the AI Gateway.

Converts messages/tools to OpenAI Chat Completions format and returns LLMResponse.
All OpenAI SDK knowledge is confined to this file.

Activation:
  Set PLUGHUB_OPENAI_API_KEY (or PLUGHUB_OPENAI_API_KEYS) and configure a
  fallback in model_profiles pointing to provider="openai".

Model IDs:
  gpt-4o            — OpenAI flagship (equivalent of claude-sonnet tier)
  gpt-4o-mini       — cheap/fast (equivalent of claude-haiku tier)
  gpt-4-turbo       — legacy high-intelligence
"""

from __future__ import annotations
from typing import Any

from .base import LLMProvider, LLMResponse, ProviderError

# Status codes that justify automatic fallback
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

_ROLE_MAP = {
    "customer": "user",
    "agent":    "assistant",
    "system":   "system",
    "user":     "user",
    "assistant": "assistant",
}


class OpenAIProvider(LLMProvider):
    """
    Provider using the OpenAI SDK (AsyncOpenAI).
    All OpenAI SDK access stays inside this class.
    Falls back gracefully with ProviderError if the openai package is absent.
    """

    def __init__(self, api_key: str) -> None:
        try:
            import openai
            self._client = openai.AsyncOpenAI(api_key=api_key or None)
        except ImportError:
            self._client = None
        self._api_key = api_key

    async def call(
        self,
        messages:   list[dict],
        tools:      list[dict] | None,
        model_id:   str,
        max_tokens: int,
    ) -> LLMResponse:
        """
        Calls the OpenAI Chat Completions API and returns a normalised LLMResponse.
        Never leaks openai.OpenAIError outside this method.
        """
        if self._client is None:
            raise ProviderError(
                provider="openai",
                error_code="sdk_not_installed",
                retryable=False,
                message=(
                    "openai package is not installed. "
                    "Install it with: pip install openai>=1.0.0"
                ),
            )

        try:
            import openai as _openai

            # Convert roles to OpenAI format.
            # System messages go as role=system (unlike Anthropic which uses system= param).
            openai_messages: list[dict[str, Any]] = []
            for m in messages:
                role = _ROLE_MAP.get(m.get("role", "user"), "user")
                openai_messages.append({
                    "role":    role,
                    "content": m.get("content", ""),
                })

            # Convert tools to OpenAI function-calling format
            openai_tools: list[dict[str, Any]] = []
            if tools:
                for t in tools:
                    openai_tools.append({
                        "type": "function",
                        "function": {
                            "name":        t.get("name", ""),
                            "description": t.get("description", ""),
                            "parameters":  t.get("input_schema", {"type": "object", "properties": {}}),
                        },
                    })

            kwargs: dict[str, Any] = {
                "model":      model_id,
                "max_tokens": max_tokens,
                "messages":   openai_messages,
            }
            if openai_tools:
                kwargs["tools"]       = openai_tools
                kwargs["tool_choice"] = "auto"

            response = await self._client.chat.completions.create(**kwargs)

            choice = response.choices[0]
            content_text  = choice.message.content or ""
            finish_reason = choice.finish_reason or "stop"

            # Map OpenAI stop reason → PlugHub stop_reason
            stop_reason = "end_turn"
            if finish_reason == "tool_calls":
                stop_reason = "tool_use"
            elif finish_reason in ("length", "max_tokens"):
                stop_reason = "max_tokens"

            # Extract tool calls
            tool_calls: list[dict[str, Any]] = []
            if choice.message.tool_calls:
                for tc in choice.message.tool_calls:
                    import json as _json
                    try:
                        input_args = _json.loads(tc.function.arguments or "{}")
                    except Exception:
                        input_args = {}
                    tool_calls.append({
                        "id":    tc.id or "",
                        "name":  tc.function.name or "",
                        "input": input_args,
                    })

            input_tokens  = response.usage.prompt_tokens     if response.usage else 0
            output_tokens = response.usage.completion_tokens if response.usage else 0

            raw: dict[str, Any] = {
                "id":           response.id or "",
                "stop_reason":  finish_reason,
                "usage":        {
                    "input_tokens":  input_tokens,
                    "output_tokens": output_tokens,
                },
            }

            return LLMResponse(
                content=content_text,
                model_used=model_id,
                raw=raw,
                stop_reason=stop_reason,
                tool_calls=tool_calls,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
            )

        except _openai.RateLimitError as e:
            raise ProviderError(
                provider="openai",
                error_code="rate_limit",
                retryable=True,
                message=str(e),
            )
        except _openai.APIConnectionError as e:
            raise ProviderError(
                provider="openai",
                error_code="connection_error",
                retryable=True,
                message=str(e),
            )
        except _openai.APIStatusError as e:
            retryable = e.status_code in _RETRYABLE_STATUS_CODES
            raise ProviderError(
                provider="openai",
                error_code=f"status_{e.status_code}",
                retryable=retryable,
                message=str(e),
            )
        except _openai.APIError as e:
            raise ProviderError(
                provider="openai",
                error_code="api_error",
                retryable=False,
                message=str(e),
            )
