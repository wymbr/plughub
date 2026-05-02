"""
test_sampling.py
Unit tests for sampling engine — no I/O, all pure logic.
"""
from __future__ import annotations

import pytest
from ..sampling import should_sample, _sample_percentage, compute_priority


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
