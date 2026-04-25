"""
providers/base.py
LLM provider protocol (ABC) — vendor abstraction.
Spec: PlugHub v24.0 section 2.2a

Any concrete provider implements LLMProvider.call() and returns LLMResponse.
Never leaks native SDK exceptions — converts them to ProviderError.
"""

from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class LLMResponse:
    """Normalised response from any LLM provider."""
    content:       str
    model_used:    str        # actual model id returned by the provider
    raw:           dict       # original payload for observability
    stop_reason:   str
    tool_calls:    list[dict] = None  # populated when stop_reason == "tool_use"
    input_tokens:  int = 0    # tokens in the prompt — populated by provider when available
    output_tokens: int = 0    # tokens in the response — populated by provider when available

    def __post_init__(self) -> None:
        if self.tool_calls is None:
            self.tool_calls = []


class ProviderError(Exception):
    """
    LLM provider error — never leaks native SDK exceptions.
    retryable=True indicates that triggering the configured fallback is appropriate.
    """
    def __init__(
        self,
        provider:   str,
        error_code: str,
        retryable:  bool,
        message:    str = "",
    ) -> None:
        super().__init__(message or f"{provider}/{error_code}")
        self.provider   = provider
        self.error_code = error_code
        self.retryable  = retryable
        self.message    = message


class LLMProvider(ABC):
    """
    Unified interface for all LLM providers.
    Switching providers is a config change, not a code change.
    """

    @abstractmethod
    async def call(
        self,
        messages:   list[dict],
        tools:      list[dict] | None,
        model_id:   str,
        max_tokens: int,
    ) -> LLMResponse:
        """
        Calls the model and returns a normalised LLMResponse.
        Converts any SDK exception to ProviderError.
        """
        ...
