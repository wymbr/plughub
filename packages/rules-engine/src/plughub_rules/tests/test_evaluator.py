"""
test_evaluator.py
Rule evaluator tests.
Spec: PlugHub v24.0 section 3.2
"""

import pytest
from datetime import datetime, timezone
from ..evaluator import RuleEvaluator
from ..models import Rule, EvaluationContext, Condition


def make_rule(conditions: list[dict], logic: str = "AND", **kwargs) -> Rule:
    now = datetime.now(timezone.utc).isoformat()
    return Rule(
        rule_id="rule_test",
        tenant_id="tenant_test",
        name="Test Rule",
        status="active",
        conditions=[Condition(**c) for c in conditions],
        logic=logic,   # type: ignore[arg-type]
        target_pool=kwargs.get("target_pool", "humano_retencao"),
        created_at=now,
        updated_at=now,
    )


def make_ctx(**kwargs) -> EvaluationContext:
    defaults = {
        "session_id":       "s1",
        "tenant_id":        "t1",
        "turn_count":       5,
        "elapsed_ms":       120_000,
        "sentiment_score":  -0.20,
        "intent_confidence": 0.75,
        "flags":            [],
        "sentiment_history": [-0.10, -0.20, -0.30, -0.40, -0.50],
    }
    defaults.update(kwargs)
    return EvaluationContext(**defaults)


class TestRuleEvaluator:
    evaluator = RuleEvaluator()

    # ── AND logic ────────────────────────────

    def test_and_fires_when_all_conditions_met(self):
        rule = make_rule([
            {"parameter": "sentiment_score", "operator": "lt", "value": -0.4},
            {"parameter": "intent_confidence", "operator": "lt", "value": 0.6},
        ], logic="AND")
        ctx = make_ctx(sentiment_score=-0.5, intent_confidence=0.45)
        result = self.evaluator.evaluate(rule, ctx)
        assert result.triggered is True

    def test_and_does_not_fire_when_one_condition_fails(self):
        rule = make_rule([
            {"parameter": "sentiment_score", "operator": "lt", "value": -0.4},
            {"parameter": "intent_confidence", "operator": "lt", "value": 0.6},
        ], logic="AND")
        ctx = make_ctx(sentiment_score=-0.5, intent_confidence=0.80)
        result = self.evaluator.evaluate(rule, ctx)
        assert result.triggered is False

    # ── OR logic ─────────────────────────────

    def test_or_fires_when_any_condition_met(self):
        rule = make_rule([
            {"parameter": "sentiment_score", "operator": "lt", "value": -0.4},
            {"parameter": "intent_confidence", "operator": "lt", "value": 0.6},
        ], logic="OR")
        ctx = make_ctx(sentiment_score=-0.5, intent_confidence=0.80)
        result = self.evaluator.evaluate(rule, ctx)
        assert result.triggered is True

    # ── Operators ────────────────────────────

    def test_operator_gt(self):
        rule = make_rule([{"parameter": "turn_count", "operator": "gt", "value": 10}])
        ctx  = make_ctx(turn_count=15)
        assert self.evaluator.evaluate(rule, ctx).triggered is True

    def test_operator_gte_at_threshold(self):
        rule = make_rule([{"parameter": "turn_count", "operator": "gte", "value": 5}])
        ctx  = make_ctx(turn_count=5)
        assert self.evaluator.evaluate(rule, ctx).triggered is True

    def test_operator_eq(self):
        rule = make_rule([{"parameter": "intent_confidence", "operator": "eq", "value": 0.75}])
        ctx  = make_ctx(intent_confidence=0.75)
        assert self.evaluator.evaluate(rule, ctx).triggered is True

    # ── Flag ──────────────────────────────────

    def test_flag_fires_when_present(self):
        rule = make_rule([{
            "parameter": "flag", "operator": "eq",
            "value": "human_requested", "flag_name": "human_requested",
        }])
        ctx = make_ctx(flags=["human_requested", "churn_signal"])
        assert self.evaluator.evaluate(rule, ctx).triggered is True

    def test_flag_does_not_fire_when_absent(self):
        rule = make_rule([{
            "parameter": "flag", "operator": "eq",
            "value": "human_requested", "flag_name": "human_requested",
        }])
        ctx = make_ctx(flags=["churn_signal"])
        assert self.evaluator.evaluate(rule, ctx).triggered is False

    # ── Turn window (moving average) ────────

    def test_moving_average_last_3_turns(self):
        rule = make_rule([{
            "parameter": "sentiment_score", "operator": "lt",
            "value": -0.4, "window_turns": 3,
        }])
        # Last 3: -0.3, -0.4, -0.5 → average = -0.4 → lt -0.4 = False
        ctx = make_ctx(sentiment_history=[-0.10, -0.20, -0.30, -0.40, -0.50])
        result = self.evaluator.evaluate(rule, ctx)
        # average of last 3 = (-0.3 + -0.4 + -0.5) / 3 = -0.4 — not lt -0.4
        assert result.triggered is False

    def test_moving_average_fires_when_below_threshold(self):
        rule = make_rule([{
            "parameter": "sentiment_score", "operator": "lt",
            "value": -0.3, "window_turns": 3,
        }])
        ctx = make_ctx(sentiment_history=[-0.10, -0.20, -0.40, -0.50, -0.60])
        result = self.evaluator.evaluate(rule, ctx)
        # average of last 3 = (-0.4 + -0.5 + -0.6) / 3 ≈ -0.5 → lt -0.3 = True
        assert result.triggered is True

    # ── No target_pool ──────────────────────

    def test_fires_but_no_target_pool(self):
        rule = make_rule(
            [{"parameter": "sentiment_score", "operator": "lt", "value": 0.0}],
            target_pool=None,
        )
        ctx = make_ctx(sentiment_score=-0.5)
        result = self.evaluator.evaluate(rule, ctx)
        assert result.triggered is True
        assert result.rule.target_pool is None

    # ── Condition results ─────────────────────

    def test_condition_results_has_observed_value(self):
        rule = make_rule([
            {"parameter": "sentiment_score", "operator": "lt", "value": -0.4},
        ])
        ctx    = make_ctx(sentiment_score=-0.6)
        result = self.evaluator.evaluate(rule, ctx)
        assert len(result.condition_results) == 1
        assert result.condition_results[0].observed_value == pytest.approx(-0.6)
        assert result.condition_results[0].matched is True
