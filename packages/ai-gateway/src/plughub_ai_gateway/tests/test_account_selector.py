"""
test_account_selector.py
Unit tests for AccountSelector — multi-account load balancing and throttle tracking.
All Redis I/O is mocked; no real Redis required.
"""

from __future__ import annotations

import hashlib
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from plughub_ai_gateway.account_selector import AccountSelector, LLMAccount


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _make_account(provider: str = "anthropic", api_key: str = "sk-test-key") -> LLMAccount:
    return LLMAccount(provider=provider, api_key=api_key, rpm_limit=60, tpm_limit=100_000)


def _key_id(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()[:16]


def _make_redis(
    mget_return: list | None = None,
    throttled: bool = False,
    rpm: int = 0,
    tpm: int = 0,
) -> MagicMock:
    """Build a minimal mock Redis client.

    pipeline() is a synchronous call that returns an object with sync
    chainable methods (incr, expire, incrby) and one async method (execute).
    """
    redis = MagicMock()
    if mget_return is None:
        mget_return = [
            b"1" if throttled else None,
            str(rpm).encode() if rpm else None,
            str(tpm).encode() if tpm else None,
        ]
    # mget / set / ping are async
    redis.mget = AsyncMock(return_value=mget_return)
    redis.set = AsyncMock()

    # pipeline() is sync and returns a sync-chainable object with async execute
    pipe = MagicMock()
    pipe.incr = MagicMock(return_value=pipe)
    pipe.expire = MagicMock(return_value=pipe)
    pipe.incrby = MagicMock(return_value=pipe)
    pipe.execute = AsyncMock(return_value=[1, True, 1, True])
    redis.pipeline.return_value = pipe
    return redis


# ─── LLMAccount ───────────────────────────────────────────────────────────────

class TestLLMAccount:
    def test_key_id_is_sha256_prefix(self) -> None:
        acc = _make_account(api_key="sk-abc123")
        expected = hashlib.sha256(b"sk-abc123").hexdigest()[:16]
        assert acc.key_id == expected

    def test_provider_key_format(self) -> None:
        acc = _make_account(provider="anthropic", api_key="sk-abc")
        assert acc.provider_key == f"anthropic:{acc.key_id}"

    def test_different_keys_have_different_key_ids(self) -> None:
        a = _make_account(api_key="sk-aaa")
        b = _make_account(api_key="sk-bbb")
        assert a.key_id != b.key_id

    def test_default_limits(self) -> None:
        acc = LLMAccount(provider="anthropic", api_key="sk-test")
        assert acc.rpm_limit == 60
        assert acc.tpm_limit == 100_000

    def test_weight_default(self) -> None:
        acc = LLMAccount(provider="anthropic", api_key="sk-test")
        assert acc.weight == 1


# ─── AccountSelector — pick() ─────────────────────────────────────────────────

class TestAccountSelectorPick:
    @pytest.mark.asyncio
    async def test_pick_returns_none_for_unknown_provider(self) -> None:
        redis = _make_redis()
        selector = AccountSelector(redis, [_make_account(provider="anthropic")])
        result = await selector.pick("openai")
        assert result is None

    @pytest.mark.asyncio
    async def test_pick_returns_none_when_no_accounts(self) -> None:
        redis = _make_redis()
        selector = AccountSelector(redis, [])
        result = await selector.pick("anthropic")
        assert result is None

    @pytest.mark.asyncio
    async def test_single_account_available_returns_provider_key(self) -> None:
        acc = _make_account()
        redis = _make_redis(throttled=False, rpm=0, tpm=0)
        selector = AccountSelector(redis, [acc])
        result = await selector.pick("anthropic")
        assert result == acc.provider_key

    @pytest.mark.asyncio
    async def test_single_account_throttled_returns_none(self) -> None:
        acc = _make_account()
        redis = _make_redis(throttled=True)
        selector = AccountSelector(redis, [acc])
        result = await selector.pick("anthropic")
        assert result is None

    @pytest.mark.asyncio
    async def test_single_account_rpm_at_limit_returns_none(self) -> None:
        acc = _make_account()
        redis = _make_redis(throttled=False, rpm=60, tpm=0)
        selector = AccountSelector(redis, [acc])
        result = await selector.pick("anthropic")
        assert result is None

    @pytest.mark.asyncio
    async def test_single_account_tpm_at_limit_returns_none(self) -> None:
        acc = _make_account()
        redis = _make_redis(throttled=False, rpm=0, tpm=100_000)
        selector = AccountSelector(redis, [acc])
        result = await selector.pick("anthropic")
        assert result is None

    @pytest.mark.asyncio
    async def test_multi_account_returns_least_loaded(self) -> None:
        acc_a = LLMAccount(provider="anthropic", api_key="sk-key-a", rpm_limit=60, tpm_limit=100_000)
        acc_b = LLMAccount(provider="anthropic", api_key="sk-key-b", rpm_limit=60, tpm_limit=100_000)

        redis = AsyncMock()
        # acc_a: rpm=30 (50% util), tpm=0 → util = 0.7*0.5 + 0.3*0 = 0.35
        # acc_b: rpm=10 (17% util), tpm=0 → util = 0.7*0.167 + 0.3*0 = 0.117  ← lower
        def mget_side_effect(*keys):
            k = keys[0] if isinstance(keys[0], str) else keys[0].decode()
            key_a_id = acc_a.key_id
            key_b_id = acc_b.key_id
            if key_a_id in str(keys):
                return [None, b"30", b"0"]   # not throttled, rpm=30
            else:
                return [None, b"10", b"0"]   # not throttled, rpm=10

        # Two calls to _is_available (one per account) + two to _utilization
        rpm_a_calls = 0
        async def mget_mock(*args):
            keys = args
            if len(keys) == 3:
                key = str(keys[0])
                if acc_a.key_id in key:
                    return [None, b"30", b"0"]
                else:
                    return [None, b"10", b"0"]
            if len(keys) == 2:
                key = str(keys[0])
                if acc_a.key_id in key:
                    return [b"30", b"0"]
                else:
                    return [b"10", b"0"]
            return [None, None, None]

        redis.mget = mget_mock
        selector = AccountSelector(redis, [acc_a, acc_b])
        result = await selector.pick("anthropic")
        assert result == acc_b.provider_key   # lower utilization

    @pytest.mark.asyncio
    async def test_multi_account_all_throttled_returns_none(self) -> None:
        acc_a = LLMAccount(provider="anthropic", api_key="sk-key-a")
        acc_b = LLMAccount(provider="anthropic", api_key="sk-key-b")

        redis = AsyncMock()
        redis.mget = AsyncMock(return_value=[b"1", None, None])  # throttled
        selector = AccountSelector(redis, [acc_a, acc_b])
        result = await selector.pick("anthropic")
        assert result is None


# ─── AccountSelector — mark_throttled() ───────────────────────────────────────

class TestMarkThrottled:
    @pytest.mark.asyncio
    async def test_sets_redis_key_with_ttl(self) -> None:
        acc = _make_account()
        redis = _make_redis()
        selector = AccountSelector(redis, [acc])
        await selector.mark_throttled(acc.provider_key, retry_after_seconds=120)
        expected_key = f"ai_gw:anthropic:{acc.key_id}:throttled"
        redis.set.assert_called_once_with(expected_key, "1", ex=120)

    @pytest.mark.asyncio
    async def test_default_retry_after(self) -> None:
        acc = _make_account()
        redis = _make_redis()
        selector = AccountSelector(redis, [acc])
        await selector.mark_throttled(acc.provider_key)
        redis.set.assert_called_once_with(
            f"ai_gw:anthropic:{acc.key_id}:throttled", "1", ex=60
        )


# ─── AccountSelector — record_usage() ────────────────────────────────────────

class TestRecordUsage:
    @pytest.mark.asyncio
    async def test_increments_rpm_always(self) -> None:
        acc = _make_account()
        redis = _make_redis()
        pipe = redis.pipeline.return_value
        selector = AccountSelector(redis, [acc])
        await selector.record_usage(acc.provider_key, tokens=0)
        pipe.incr.assert_called_once()
        pipe.expire.assert_called()

    @pytest.mark.asyncio
    async def test_increments_tpm_when_tokens_positive(self) -> None:
        acc = _make_account()
        redis = _make_redis()
        pipe = redis.pipeline.return_value
        selector = AccountSelector(redis, [acc])
        await selector.record_usage(acc.provider_key, tokens=500)
        pipe.incrby.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_tpm_when_tokens_zero(self) -> None:
        acc = _make_account()
        redis = _make_redis()
        pipe = redis.pipeline.return_value
        selector = AccountSelector(redis, [acc])
        await selector.record_usage(acc.provider_key, tokens=0)
        pipe.incrby.assert_not_called()


# ─── AccountSelector — health_summary() ──────────────────────────────────────

class TestHealthSummary:
    @pytest.mark.asyncio
    async def test_returns_account_health_info(self) -> None:
        acc = _make_account()
        redis = _make_redis(throttled=False, rpm=15, tpm=5000)
        selector = AccountSelector(redis, [acc])
        summary = await selector.health_summary()
        assert "anthropic" in summary
        entries = summary["anthropic"]
        assert len(entries) == 1
        entry = entries[0]
        assert entry["key_id"] == acc.key_id
        assert entry["throttled"] is False
        assert entry["rpm_current"] == 15
        assert entry["rpm_limit"] == 60
        assert entry["tpm_current"] == 5000
        assert entry["tpm_limit"] == 100_000

    @pytest.mark.asyncio
    async def test_throttled_account_shows_true(self) -> None:
        acc = _make_account()
        redis = _make_redis(throttled=True)
        selector = AccountSelector(redis, [acc])
        summary = await selector.health_summary()
        assert summary["anthropic"][0]["throttled"] is True

    @pytest.mark.asyncio
    async def test_empty_accounts_returns_empty_summary(self) -> None:
        redis = _make_redis()
        selector = AccountSelector(redis, [])
        summary = await selector.health_summary()
        assert summary == {}


# ─── AccountSelector — providers_for() ───────────────────────────────────────

class TestProvidersFor:
    def test_returns_all_provider_keys_for_provider(self) -> None:
        acc_a = LLMAccount(provider="anthropic", api_key="sk-key-a")
        acc_b = LLMAccount(provider="anthropic", api_key="sk-key-b")
        redis = _make_redis()
        selector = AccountSelector(redis, [acc_a, acc_b])
        keys = selector.providers_for("anthropic")
        assert len(keys) == 2
        assert acc_a.provider_key in keys
        assert acc_b.provider_key in keys

    def test_returns_empty_for_unknown_provider(self) -> None:
        redis = _make_redis()
        selector = AccountSelector(redis, [_make_account()])
        assert selector.providers_for("openai") == []


# ─── Settings helpers ─────────────────────────────────────────────────────────

class TestSettingsKeyParsing:
    def test_single_key(self) -> None:
        from plughub_ai_gateway.config import Settings
        s = Settings(anthropic_api_key="sk-single")
        assert s.get_anthropic_keys() == ["sk-single"]

    def test_comma_separated_keys_override_single(self) -> None:
        from plughub_ai_gateway.config import Settings
        s = Settings(anthropic_api_key="sk-old", anthropic_api_keys="sk-a,sk-b,sk-c")
        assert s.get_anthropic_keys() == ["sk-a", "sk-b", "sk-c"]

    def test_empty_key_returns_empty_list(self) -> None:
        from plughub_ai_gateway.config import Settings
        s = Settings(anthropic_api_key="", anthropic_api_keys="")
        assert s.get_anthropic_keys() == []

    def test_whitespace_stripped(self) -> None:
        from plughub_ai_gateway.config import Settings
        s = Settings(anthropic_api_keys="  sk-a , sk-b  ")
        assert s.get_anthropic_keys() == ["sk-a", "sk-b"]

    def test_openai_keys_parsing(self) -> None:
        from plughub_ai_gateway.config import Settings
        s = Settings(openai_api_keys="sk-oa,sk-ob")
        assert s.get_openai_keys() == ["sk-oa", "sk-ob"]

    def test_evaluation_profile_in_model_profiles(self) -> None:
        from plughub_ai_gateway.config import Settings
        s = Settings(anthropic_api_key="sk-test")
        profiles = s.model_profiles
        assert "evaluation" in profiles
        assert profiles["evaluation"].provider == "anthropic"
