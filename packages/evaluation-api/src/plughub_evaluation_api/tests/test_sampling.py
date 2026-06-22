"""
test_sampling.py
Unit tests for sampling engine — no I/O, all pure logic.
"""
from __future__ import annotations

import pytest
from ..sampling import (
    should_sample,
    _sample_percentage,
    compute_priority,
    _passes_filters,
    _quota_decide,
    quota_agent_key,
    quota_rate,
    should_sample_quota,
)


# ─── _sample_percentage ───────────────────────────────────────────────────────

class TestSamplePercentage:
    def test_rate_zero_never_samples(self):
        for i in range(100):
            assert _sample_percentage(f"sess_{i}", 0.0) is False

    def test_rate_one_always_samples(self):
        for i in range(100):
            assert _sample_percentage(f"sess_{i}", 1.0) is True

    def test_rate_half_deterministic(self):
        # With SHA-256 bucketing, the result is deterministic for a given session_id
        result1 = _sample_percentage("sess_abc", 0.5)
        result2 = _sample_percentage("sess_abc", 0.5)
        assert result1 == result2

    def test_rate_clamps_above_one(self):
        assert _sample_percentage("any", 1.5) is True

    def test_rate_clamps_below_zero(self):
        assert _sample_percentage("any", -0.1) is False

    def test_distribution_roughly_10_percent(self):
        sampled = sum(1 for i in range(1000) if _sample_percentage(f"sess_{i:04d}", 0.1))
        # Allow ±5% tolerance
        assert 50 <= sampled <= 150


# ─── should_sample ────────────────────────────────────────────────────────────

class TestShouldSample:
    def test_empty_rules_uses_default_10pct(self):
        # With empty rules the function falls back to 10% percentage sampling.
        # We just verify it returns a boolean without error.
        result = should_sample("sess_001", {}, {})
        assert isinstance(result, bool)

    def test_mode_all_always_true(self):
        rules = {"mode": "all"}
        assert should_sample("sess_001", {"duration_s": 1}, rules) is True

    def test_mode_fixed_every_5(self):
        rules = {"mode": "fixed", "every_n": 5}
        assert should_sample("sess_001", {}, rules, counter=5) is True
        assert should_sample("sess_002", {}, rules, counter=10) is True
        assert should_sample("sess_003", {}, rules, counter=3) is False
        assert should_sample("sess_004", {}, rules, counter=1) is False

    def test_mode_fixed_counter_zero_never_samples(self):
        rules = {"mode": "fixed", "every_n": 5}
        assert should_sample("sess_x", {}, rules, counter=0) is False

    def test_min_duration_filter(self):
        rules = {"mode": "all", "min_duration_s": 60}
        assert should_sample("s1", {"duration_s": 90}, rules) is True
        assert should_sample("s2", {"duration_s": 30}, rules) is False

    def test_agent_type_filter(self):
        rules = {"mode": "all", "agent_type_ids": ["agente_sac_v1"]}
        assert should_sample("s1", {"agent_type_id": "agente_sac_v1"}, rules) is True
        assert should_sample("s2", {"agent_type_id": "agente_retencao_v1"}, rules) is False

    def test_pool_filter(self):
        rules = {"mode": "all", "pool_ids": ["sac_ia"]}
        assert should_sample("s1", {"pool_id": "sac_ia"}, rules) is True
        assert should_sample("s2", {"pool_id": "retencao_humano"}, rules) is False

    def test_channel_filter(self):
        rules = {"mode": "all", "channels": ["whatsapp", "webchat"]}
        assert should_sample("s1", {"channel": "whatsapp"}, rules) is True
        assert should_sample("s2", {"channel": "voice"}, rules) is False

    def test_outcome_filter(self):
        rules = {"mode": "all", "outcome_filter": ["resolved"]}
        assert should_sample("s1", {"outcome": "resolved"}, rules) is True
        assert should_sample("s2", {"outcome": "escalated"}, rules) is False

    def test_empty_filter_lists_allow_any_value(self):
        rules = {"mode": "all", "agent_type_ids": [], "pool_ids": [], "channels": []}
        assert should_sample("s1", {"agent_type_id": "any", "pool_id": "any", "channel": "voice"}, rules) is True

    def test_multiple_filters_all_must_pass(self):
        rules = {
            "mode": "all",
            "min_duration_s": 30,
            "agent_type_ids": ["agente_sac_v1"],
        }
        # Passes both
        assert should_sample("s1", {"duration_s": 60, "agent_type_id": "agente_sac_v1"}, rules) is True
        # Fails duration
        assert should_sample("s2", {"duration_s": 10, "agent_type_id": "agente_sac_v1"}, rules) is False
        # Fails agent_type
        assert should_sample("s3", {"duration_s": 60, "agent_type_id": "other"}, rules) is False

    def test_percentage_mode_explicit(self):
        rules = {"mode": "percentage", "rate": 1.0}
        assert should_sample("sess_xyz", {}, rules) is True

    def test_default_mode_treated_as_percentage(self):
        rules = {"rate": 1.0}  # no mode key
        assert should_sample("sess_xyz", {}, rules) is True


# ─── compute_priority ────────────────────────────────────────────────────────

class TestComputePriority:
    def test_default_priority_5(self):
        assert compute_priority({}, {}) == 5

    def test_custom_default(self):
        assert compute_priority({}, {"default_priority": 3}) == 3

    def test_override_by_field(self):
        rules = {
            "priority_overrides": [
                {"field": "channel", "value": "whatsapp", "priority": 2},
            ]
        }
        assert compute_priority({"channel": "whatsapp"}, rules) == 2
        assert compute_priority({"channel": "webchat"}, rules) == 5

    def test_first_matching_override_wins(self):
        rules = {
            "priority_overrides": [
                {"field": "outcome", "value": "escalated", "priority": 1},
                {"field": "channel", "value": "whatsapp", "priority": 2},
            ]
        }
        meta = {"outcome": "escalated", "channel": "whatsapp"}
        assert compute_priority(meta, rules) == 1  # first override wins


# ─── R10 — quota mode (per-agent cumulative deficit) ──────────────────────────

class _FakeAsyncRedis:
    """Minimal in-memory async Redis stub for the quota counter.

    Mirrors redis.asyncio with decode_responses=True: hget returns str|None,
    hincrby/sadd return ints.
    """
    def __init__(self):
        self.hashes: dict[str, dict[str, int]] = {}
        self.sets: dict[str, set] = {}

    async def sadd(self, key, member):
        s = self.sets.setdefault(key, set())
        if member in s:
            return 0
        s.add(member)
        return 1

    async def expire(self, key, ttl):  # noqa: ARG002
        return True

    async def hincrby(self, key, field, amount=1):
        h = self.hashes.setdefault(key, {})
        h[field] = int(h.get(field, 0)) + amount
        return h[field]

    async def hget(self, key, field):
        v = self.hashes.get(key, {}).get(field)
        return None if v is None else str(v)


class TestQuotaDecide:
    """Pure deficit math — no I/O."""

    def test_first_eligible_is_floor(self):
        assert _quota_decide(total=1, sampled_before=0, rate=0.0) is True
        assert _quota_decide(total=1, sampled_before=0, rate=0.1) is True

    def test_rate_one_samples_all(self):
        assert _quota_decide(total=5, sampled_before=2, rate=1.0) is True

    def test_rate_zero_only_floor(self):
        assert _quota_decide(total=2, sampled_before=1, rate=0.0) is False

    def test_converges_to_10pct_trigger_at_11(self):
        # x=10%: with sampled=1, deficit fires exactly when 1/total < 0.1 → total=11.
        assert _quota_decide(total=10, sampled_before=1, rate=0.1) is False
        assert _quota_decide(total=11, sampled_before=1, rate=0.1) is True


class TestQuotaKeyAndRate:
    def test_human_key(self):
        key, is_human = quota_agent_key(
            evaluated_user_id="u1", pool_id="p", skill_id="s", deploy_version="v3")
        assert is_human is True
        assert key == "h:u1"

    def test_ai_key_with_version(self):
        key, is_human = quota_agent_key(
            evaluated_user_id=None, pool_id="sac_ia",
            skill_id="skill_sac_v1", deploy_version="v3")
        assert is_human is False
        assert key == "ai:sac_ia:skill_sac_v1:v3"

    def test_ai_key_version_fallback_bucket(self):
        # deploy_version unresolved collapses to the (campaign,pool,skill) bucket.
        key, _ = quota_agent_key(
            evaluated_user_id=None, pool_id="sac_ia",
            skill_id="skill_sac_v1", deploy_version=None)
        assert key == "ai:sac_ia:skill_sac_v1:_nover"

    def test_rate_human_ai_split(self):
        rules = {"quota_rate_human": 0.2, "quota_rate_ai": 0.05}
        assert quota_rate(rules, is_human=True) == 0.2
        assert quota_rate(rules, is_human=False) == 0.05

    def test_rate_falls_back_to_legacy_rate(self):
        assert quota_rate({"rate": 0.3}, is_human=True) == 0.3

    def test_rate_defaults_when_absent(self):
        assert quota_rate({}, is_human=True) == 0.10
        assert quota_rate({}, is_human=False) == 0.05


class TestShouldSampleQuota:
    async def _run(self, redis, *, target, rate_ai=0.1, meta=None,
                   uid=None, pool="sac_ia", skill="skill_sac_v1", ver="v3"):
        return await should_sample_quota(
            redis, tenant_id="tenant_demo", campaign_id="camp1",
            target_id=target, session_meta=(meta or {"pool_id": pool}),
            sampling_rules={"mode": "quota", "quota_rate_ai": rate_ai,
                            "quota_rate_human": rate_ai},
            evaluated_user_id=uid, pool_id=pool, skill_id=skill, deploy_version=ver,
        )

    async def test_deterministic_sequence_100_contacts_at_10pct(self):
        # One AI agent, 100 eligible contacts, x=10% → floor + deficit selects at
        # t=1,11,21,...,91 → exactly 10 sampled, coverage = 10%.
        redis = _FakeAsyncRedis()
        sampled = 0
        chosen = []
        for i in range(1, 101):
            keep = await self._run(redis, target=f"seg_{i:03d}", rate_ai=0.1)
            if keep:
                sampled += 1
                chosen.append(i)
        assert sampled == 10
        assert chosen == [1, 11, 21, 31, 41, 51, 61, 71, 81, 91]
        # Final counter state: total=100, sampled=10.
        h = redis.hashes["tenant_demo:eval:quota:camp1:ai:sac_ia:skill_sac_v1:v3"]
        assert h == {"total": 100, "sampled": 10}

    async def test_low_volume_floor_inflates_above_rate(self):
        # 3 contacts at 10%: only the floor (first) → coverage 1/3 > 10% by design.
        redis = _FakeAsyncRedis()
        results = [await self._run(redis, target=f"s{i}", rate_ai=0.1) for i in range(3)]
        assert results == [True, False, False]

    async def test_human_and_ai_counters_are_separate(self):
        redis = _FakeAsyncRedis()
        # AI agent's 1st contact (floor) and a human's 1st contact (floor) — both True,
        # independent buckets, neither steals the other's deficit.
        ai = await self._run(redis, target="seg_ai_1", rate_ai=0.1)
        hu = await self._run(redis, target="seg_hu_1", rate_ai=0.1, uid="user_42")
        assert ai is True and hu is True
        assert "tenant_demo:eval:quota:camp1:ai:sac_ia:skill_sac_v1:v3" in redis.hashes
        assert "tenant_demo:eval:quota:camp1:h:user_42" in redis.hashes

    async def test_version_fallback_shares_one_bucket(self):
        # Two version-less AI contacts share the _nover bucket → second sees total=2.
        redis = _FakeAsyncRedis()
        await self._run(redis, target="seg_a", rate_ai=0.1, ver=None)
        await self._run(redis, target="seg_b", rate_ai=0.1, ver=None)
        h = redis.hashes["tenant_demo:eval:quota:camp1:ai:sac_ia:skill_sac_v1:_nover"]
        assert h["total"] == 2

    async def test_filtered_contact_does_not_inflate_denominator(self):
        # A too-short contact is ineligible → must NOT touch the counter, so the next
        # eligible contact is still the floor (total=1).
        redis = _FakeAsyncRedis()
        rules = {"mode": "quota", "quota_rate_ai": 0.1, "min_duration_s": 30}
        short = await should_sample_quota(
            redis, tenant_id="tenant_demo", campaign_id="camp1", target_id="short",
            session_meta={"pool_id": "sac_ia", "duration_s": 5},
            sampling_rules=rules, pool_id="sac_ia", skill_id="skill_sac_v1",
            deploy_version="v3")
        assert short is False
        assert redis.hashes == {}  # denominator untouched
        ok = await should_sample_quota(
            redis, tenant_id="tenant_demo", campaign_id="camp1", target_id="long",
            session_meta={"pool_id": "sac_ia", "duration_s": 90},
            sampling_rules=rules, pool_id="sac_ia", skill_id="skill_sac_v1",
            deploy_version="v3")
        assert ok is True  # floor — proves the short one didn't count
        h = redis.hashes["tenant_demo:eval:quota:camp1:ai:sac_ia:skill_sac_v1:v3"]
        assert h["total"] == 1

    async def test_idempotent_redelivery_does_not_double_count(self):
        # Kafka at-least-once: the same target re-processed must not re-sample nor
        # inflate the denominator.
        redis = _FakeAsyncRedis()
        first = await self._run(redis, target="seg_dup", rate_ai=0.1)
        again = await self._run(redis, target="seg_dup", rate_ai=0.1)
        assert first is True
        assert again is False
        h = redis.hashes["tenant_demo:eval:quota:camp1:ai:sac_ia:skill_sac_v1:v3"]
        assert h["total"] == 1

    async def test_redis_none_falls_back_to_hash(self):
        # Best-effort: no Redis → deterministic percentage, never crashes.
        r = await should_sample_quota(
            None, tenant_id="t", campaign_id="c", target_id="seg_x",
            session_meta={"pool_id": "sac_ia"},
            sampling_rules={"mode": "quota", "quota_rate_ai": 1.0},
            pool_id="sac_ia", skill_id="s", deploy_version="v3")
        assert r is True  # rate 1.0 → always


class TestPassesFilters:
    def test_passes_when_all_match(self):
        rules = {"min_duration_s": 30, "pool_ids": ["sac_ia"], "outcome_filter": ["resolved"]}
        meta = {"duration_s": 60, "pool_id": "sac_ia", "outcome": "resolved"}
        assert _passes_filters(meta, rules) is True

    def test_fails_on_any_filter(self):
        rules = {"outcome_filter": ["resolved"]}
        assert _passes_filters({"outcome": "escalated"}, rules) is False
