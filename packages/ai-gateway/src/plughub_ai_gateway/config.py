"""
config.py
AI Gateway settings loaded from environment variables.
"""

from __future__ import annotations
from dataclasses import dataclass
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass
class FallbackConfig:
    provider: str
    model_id: str


@dataclass
class ModelProfileConfig:
    provider: str
    model_id: str
    fallback: FallbackConfig | None = None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PLUGHUB_", case_sensitive=False)

    # Anthropic — single key (backward compat) or comma-separated list
    anthropic_api_key:  str = ""
    anthropic_api_keys: str = ""   # comma-separated; overrides anthropic_api_key when set
    anthropic_rpm_limit: int = 60
    anthropic_tpm_limit: int = 100_000

    # OpenAI — optional fallback provider
    openai_api_key:  str = ""
    openai_api_keys: str = ""      # comma-separated; overrides openai_api_key when set
    openai_rpm_limit: int = 60
    openai_tpm_limit: int = 100_000

    # model_profile → model mapping (kept for backward compat with /v1/turn)
    model_fast:      str = "claude-haiku-4-5-20251001"
    model_balanced:  str = "claude-sonnet-4-6"
    model_powerful:  str = "claude-opus-4-6"

    # Evaluation model profile — isolated from realtime agents
    model_evaluation: str = "claude-haiku-4-5-20251001"

    # Redis
    redis_url: str = "redis://localhost:6379"

    # Server
    host:    str = "0.0.0.0"
    port:    int = 3200
    workers: int = 4

    # Redis session
    session_ttl_seconds:   int = 86_400          # 24h
    redis_session_channel: str = "session:updates"

    # Rate limiting (per tenant, per minute)
    rate_limit_rpm: int = 600

    # Semantic cache TTL
    cache_ttl_seconds: int = 300  # 5 minutes

    # Inference max_tokens default
    inference_max_tokens: int = 1024

    # Kafka — metering (usage.events)
    kafka_brokers: str = "kafka:9092"
    gateway_id:    str = "ai-gateway"

    def get_anthropic_keys(self) -> list[str]:
        """Returns list of Anthropic API keys. Prefers anthropic_api_keys (comma-separated)."""
        raw = self.anthropic_api_keys.strip() or self.anthropic_api_key.strip()
        keys = [k.strip() for k in raw.split(",") if k.strip()]
        return keys

    def get_openai_keys(self) -> list[str]:
        """Returns list of OpenAI API keys. Prefers openai_api_keys (comma-separated)."""
        raw = self.openai_api_keys.strip() or self.openai_api_key.strip()
        keys = [k.strip() for k in raw.split(",") if k.strip()]
        return keys

    def model_for_profile(self, profile: str) -> str:
        """Backward compat with /v1/turn and /v1/reason."""
        return {
            "fast":     self.model_fast,
            "balanced": self.model_balanced,
            "powerful": self.model_powerful,
        }.get(profile, self.model_balanced)

    @property
    def model_profiles(self) -> dict[str, ModelProfileConfig]:
        """
        Maps model_profile → (provider, model_id, fallback).
        Changing provider or model_id is a config change, not a code change.
        """
        return {
            "fast": ModelProfileConfig(
                provider="anthropic",
                model_id=self.model_fast,
                fallback=FallbackConfig(
                    provider="anthropic",
                    model_id=self.model_balanced,
                ),
            ),
            "balanced": ModelProfileConfig(
                provider="anthropic",
                model_id=self.model_balanced,
                fallback=FallbackConfig(
                    provider="anthropic",
                    model_id=self.model_fast,
                ),
            ),
            "powerful": ModelProfileConfig(
                provider="anthropic",
                model_id=self.model_powerful,
                fallback=FallbackConfig(
                    provider="anthropic",
                    model_id=self.model_balanced,
                ),
            ),
            # Evaluation profile — isolated workload, uses cheaper model by default.
            # Callers pass model_profile="evaluation" to avoid competing with realtime agents.
            "evaluation": ModelProfileConfig(
                provider="anthropic",
                model_id=self.model_evaluation,
                fallback=FallbackConfig(
                    provider="anthropic",
                    model_id=self.model_balanced,
                ),
            ),
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
