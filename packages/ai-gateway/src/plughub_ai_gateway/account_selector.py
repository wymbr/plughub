"""
account_selector.py
Selects the least-loaded, non-throttled LLM account for a given provider.
Health state persisted in Redis (< 1ms per pick — 3 MGET calls).

Redis keys:
  ai_gw:{provider}:{key_id}:throttled  → "1" with TTL = retry_after_seconds
  ai_gw:{provider}:{key_id}:rpm        → counter, INCR + EXPIRE 60s (rolling window)
  ai_gw:{provider}:{key_id}:tpm        → counter, INCR + EXPIRE 60s (rolling window)

key_id is the first 16 chars of SHA-256(api_key) — never stores the actual key.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger("plughub.ai_gateway.account_selector")


@dataclass
class LLMAccount:
    """Configuration for a single LLM provider account."""
    provider:   str
    api_key:    str
    weight:     int = 1
    rpm_limit:  int = 60          # requests per minute
    tpm_limit:  int = 100_000     # tokens per minute

    @property
    def key_id(self) -> str:
        """Short hash of the API key — safe to store in Redis keys."""
        return hashlib.sha256(self.api_key.encode()).hexdigest()[:16]

    @property
    def provider_key(self) -> str:
        """Registry key used in the providers dict: {provider}:{key_id}"""
        return f"{self.provider}:{self.key_id}"


class AccountSelector:
    """
    Stateless selector — all state lives in Redis.
    Thread-safe: each pick() is an atomic read of Redis keys.

    Usage:
        selector = AccountSelector(redis, accounts)
        provider_key = await selector.pick("anthropic")
        if provider_key is None:
            # all accounts throttled — caller should try fallback provider
            ...
        provider = providers[provider_key]
        try:
            result = await provider.call(...)
            await selector.record_usage(provider_key, tokens=result.input_tokens + result.output_tokens)
        except ProviderError as e:
            if e.error_code == "rate_limit":
                await selector.mark_throttled(provider_key, retry_after_seconds=60)
    """

    def __init__(self, redis, accounts: list[LLMAccount]) -> None:
        self._redis = redis
        # Group accounts by provider
        self._accounts: dict[str, list[LLMAccount]] = {}
        for account in accounts:
            self._accounts.setdefault(account.provider, []).append(account)

    async def pick(self, provider: str) -> Optional[str]:
        """
        Returns the provider_key of the best available account for the given provider.
        Returns None if all accounts are throttled or none registered.
        """
        accounts = self._accounts.get(provider, [])
        if not accounts:
            return None

        # Fast path — single account (most common)
        if len(accounts) == 1:
            acc = accounts[0]
            if await self._is_available(acc):
                return acc.provider_key
            logger.warning("AccountSelector: single account throttled for provider=%s", provider)
            return None

        # Multi-account — find least-loaded non-throttled account
        best_key: Optional[str] = None
        best_util = float("inf")

        for acc in accounts:
            if not await self._is_available(acc):
                continue
            util = await self._utilization(acc)
            if util < best_util:
                best_util = util
                best_key = acc.provider_key

        if best_key is None:
            logger.warning(
                "AccountSelector: all %d accounts throttled for provider=%s",
                len(accounts), provider,
            )
        return best_key

    async def mark_throttled(
        self,
        provider_key: str,
        retry_after_seconds: int = 60,
    ) -> None:
        """
        Mark account as throttled for retry_after_seconds.
        Called when provider returns 429/529.
        """
        provider, key_id = provider_key.split(":", 1)
        redis_key = f"ai_gw:{provider}:{key_id}:throttled"
        await self._redis.set(redis_key, "1", ex=retry_after_seconds)
        logger.warning(
            "Account throttled: provider_key=%s for %ds",
            provider_key, retry_after_seconds,
        )

    async def record_usage(self, provider_key: str, tokens: int = 0) -> None:
        """
        Increment RPM counter (always) and TPM counter (when tokens > 0).
        Uses pipelined INCR + EXPIRE for atomicity and speed.
        """
        provider, key_id = provider_key.split(":", 1)
        rpm_key = f"ai_gw:{provider}:{key_id}:rpm"
        tpm_key = f"ai_gw:{provider}:{key_id}:tpm"

        pipe = self._redis.pipeline(transaction=False)
        pipe.incr(rpm_key)
        pipe.expire(rpm_key, 60)
        if tokens > 0:
            pipe.incrby(tpm_key, tokens)
            pipe.expire(tpm_key, 60)
        await pipe.execute()

    def providers_for(self, provider: str) -> list[str]:
        """Returns all registered provider_keys for a provider (for diagnostics)."""
        return [acc.provider_key for acc in self._accounts.get(provider, [])]

    async def health_summary(self) -> dict[str, list[dict]]:
        """Returns health state of all registered accounts (for /v1/health endpoint)."""
        summary: dict[str, list[dict]] = {}
        for provider, accounts in self._accounts.items():
            summary[provider] = []
            for acc in accounts:
                throttled_key = f"ai_gw:{provider}:{acc.key_id}:throttled"
                rpm_key = f"ai_gw:{provider}:{acc.key_id}:rpm"
                tpm_key = f"ai_gw:{provider}:{acc.key_id}:tpm"
                results = await self._redis.mget(throttled_key, rpm_key, tpm_key)
                throttled, rpm_raw, tpm_raw = results
                summary[provider].append({
                    "key_id":       acc.key_id,
                    "provider_key": acc.provider_key,
                    "throttled":    bool(throttled),
                    "rpm_current":  int(rpm_raw or 0),
                    "rpm_limit":    acc.rpm_limit,
                    "tpm_current":  int(tpm_raw or 0),
                    "tpm_limit":    acc.tpm_limit,
                })
        return summary

    # ─── Private helpers ───────────────────────────────────────────────────────

    async def _is_available(self, acc: LLMAccount) -> bool:
        """Returns True if account is not throttled and within rate limits."""
        provider, key_id = acc.provider, acc.key_id
        throttled_key = f"ai_gw:{provider}:{key_id}:throttled"
        rpm_key       = f"ai_gw:{provider}:{key_id}:rpm"
        tpm_key       = f"ai_gw:{provider}:{key_id}:tpm"

        results = await self._redis.mget(throttled_key, rpm_key, tpm_key)
        throttled, rpm_raw, tpm_raw = results

        if throttled:
            return False
        if int(rpm_raw or 0) >= acc.rpm_limit:
            return False
        if int(tpm_raw or 0) >= acc.tpm_limit:
            return False
        return True

    async def _utilization(self, acc: LLMAccount) -> float:
        """Returns utilization ratio for load balancing. Lower = better."""
        provider, key_id = acc.provider, acc.key_id
        rpm_key = f"ai_gw:{provider}:{key_id}:rpm"
        tpm_key = f"ai_gw:{provider}:{key_id}:tpm"
        results = await self._redis.mget(rpm_key, tpm_key)
        rpm_raw, tpm_raw = results
        rpm_util = int(rpm_raw or 0) / max(acc.rpm_limit, 1)
        tpm_util = int(tpm_raw or 0) / max(acc.tpm_limit, 1)
        # RPM weighted 70% — more commonly the binding constraint for short calls
        return rpm_util * 0.7 + tpm_util * 0.3
