"""
test_dry_run.py
Tests for the dry-run mechanism and session simulator.
Spec: PlugHub v24.0 section 3.2b
"""

import pytest
from datetime import datetime, timezone
from ..dry_run import DryRunEngine
from ..models import (
    Rule, Condition, EvaluationContext,
    DryRunRequest, SessionSimulatorRequest,
)


def make_rule(threshold: float = -0.4) -> Rule:
    now = datetime.now(timezone.utc).isoformat()
    return Rule(
        rule_id="rule_churn",
        tenant_id="tenant_test",
        name="Churn Escalation",
        status="dry_run",
        conditions=[Condition(parameter="sentiment_score", operator="lt", value=threshold)],
        logic="AND",
        target_pool="humano_retencao",
        created_at=now,
        updated_at=now,
    )


def make_session(session_id: str, sentiments: list[float]) -> list[EvaluationContext]:
    return [
        EvaluationContext(
            session_id=session_id,
            tenant_id="tenant_test",
            turn_count=i + 1,
            elapsed_ms=i * 30_000,
            sentiment_score=s,
            intent_confidence=0.80,
        )
        for i, s in enumerate(sentiments)
    ]


class TestDryRunEngine:
    engine = DryRunEngine()

    @pytest.mark.asyncio
    async def test_dry_run_counts_conversations_that_would_fire(self):
        sessions = [
            make_session("s1", [-0.1, -0.2, -0.5]),   # fires at turn 3
            make_session("s2", [-0.1, -0.2, -0.3]),   # does not fire
            make_session("s3", [-0.5, -0.6, -0.7]),   # fires at turn 1
        ]
        req    = DryRunRequest(rule=make_rule(-0.4), tenant_id="tenant_test")
        result = await self.engine.dry_run_historico(req, sessions)

        assert result.total_conversations  == 3
        assert result.would_trigger_count  == 2
        assert result.trigger_rate         == pytest.approx(2/3, rel=0.01)

    @pytest.mark.asyncio
    async def test_dry_run_no_triggers(self):
        sessions = [
            make_session("s1", [-0.1, -0.2, -0.3]),
            make_session("s2", [0.0, 0.1, 0.2]),
        ]
        req    = DryRunRequest(rule=make_rule(-0.4), tenant_id="tenant_test")
        result = await self.engine.dry_run_historico(req, sessions)

        assert result.would_trigger_count == 0
        assert result.trigger_rate        == 0.0

    @pytest.mark.asyncio
    async def test_dry_run_sample_triggers_capped(self):
        sessions = [make_session(f"s{i}", [-0.9]) for i in range(10)]
        req    = DryRunRequest(rule=make_rule(-0.4), tenant_id="tenant_test")
        result = await self.engine.dry_run_historico(req, sessions)

        # sample_triggers capped at 5
        assert len(result.sample_triggers) <= 5
        assert result.would_trigger_count == 10

    @pytest.mark.asyncio
    async def test_diff_rules_identifies_difference(self):
        sessions = [
            make_session("s1", [-0.5]),   # fires both (-0.4 and -0.3)
            make_session("s2", [-0.35]),  # fires only the more permissive (-0.3)
            make_session("s3", [-0.1]),   # fires neither
        ]
        rule_a = make_rule(-0.4)  # more restrictive
        rule_b = make_rule(-0.3)  # more permissive

        diff = await self.engine.diff_regras(rule_a, rule_b, sessions)

        assert diff["total_conversations"] == 3
        assert diff["only_rule_b"]         == 1   # s2 fires only B
        assert diff["both"]                == 1   # s1 fires both
        assert diff["neither"]             == 1   # s3 fires neither

    def test_session_simulator_fires_with_correct_params(self):
        req = SessionSimulatorRequest(
            tenant_id="tenant_test",
            rule=make_rule(-0.4),
            sentiment_score=-0.6,
            intent_confidence=0.80,
            turn_count=3,
        )
        result = self.engine.simulate_session(req)
        assert result.triggered    is True
        assert result.target_pool  == "humano_retencao"

    def test_session_simulator_does_not_fire_above_threshold(self):
        req = SessionSimulatorRequest(
            tenant_id="tenant_test",
            rule=make_rule(-0.4),
            sentiment_score=-0.2,
        )
        result = self.engine.simulate_session(req)
        assert result.triggered    is False
        assert result.target_pool  is None

    def test_simulator_returns_condition_results(self):
        req = SessionSimulatorRequest(
            tenant_id="tenant_test",
            rule=make_rule(-0.4),
            sentiment_score=-0.5,
        )
        result = self.engine.simulate_session(req)
        assert len(result.condition_results) == 1
        assert result.condition_results[0].matched is True
