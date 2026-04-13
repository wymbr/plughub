"""
dry_run.py
Sandbox mechanisms for the Rules Engine.
Spec: PlugHub v24.0 section 3.2b

Four mechanisms:
1. dry_run_historico — simulates rule against ClickHouse history
2. shadow_mode       — evaluates and records, does not trigger (managed in evaluator)
3. diff_regra        — compares two rules against the same history
4. simulador_sessao  — tests rule with manually supplied parameters
"""

from __future__ import annotations
from datetime import datetime, timezone

from .models import (
    Rule, EvaluationContext,
    DryRunRequest, DryRunResult, DryRunConversationResult,
    SessionSimulatorRequest, SessionSimulatorResult,
)
from .evaluator import RuleEvaluator


class DryRunEngine:
    def __init__(self) -> None:
        self._evaluator = RuleEvaluator()

    # ─────────────────────────────────────────────
    # 1. Historical dry-run (spec 3.2b)
    # ─────────────────────────────────────────────

    async def dry_run_historico(
        self,
        request:            DryRunRequest,
        historical_sessions: list[list[EvaluationContext]],
    ) -> DryRunResult:
        """
        Simulates the rule against a conversation history.
        historical_sessions: list of sessions, each with a list of contexts per turn.

        In production: contexts are loaded from ClickHouse by the engine.
        The signature accepts pre-loaded data to allow unit testing.
        """
        trigger_count = 0
        sample_triggers: list[DryRunConversationResult] = []
        settings_sample = 5  # maximum sample entries in the result

        for session_contexts in historical_sessions:
            triggered_at_turn: int | None = None
            trigger_context: EvaluationContext | None = None

            for turn_idx, ctx in enumerate(session_contexts):
                result = self._evaluator.evaluate(request.rule, ctx)
                if result.triggered:
                    triggered_at_turn = turn_idx + 1
                    trigger_context   = ctx
                    break

            would_trigger = triggered_at_turn is not None
            if would_trigger:
                trigger_count += 1

            if len(sample_triggers) < settings_sample:
                sample_triggers.append(DryRunConversationResult(
                    session_id=session_contexts[0].session_id if session_contexts else "unknown",
                    would_trigger=would_trigger,
                    at_turn=triggered_at_turn,
                    context_at_trigger=trigger_context,
                ))

        total = len(historical_sessions)
        return DryRunResult(
            rule_id=request.rule.rule_id,
            tenant_id=request.tenant_id,
            history_window_days=request.history_window_days,
            total_conversations=total,
            would_trigger_count=trigger_count,
            trigger_rate=round(trigger_count / max(total, 1), 4),
            sample_triggers=sample_triggers,
            simulated_at=datetime.now(timezone.utc).isoformat(),
        )

    # ─────────────────────────────────────────────
    # 3. Diff de regra (spec 3.2b)
    # ─────────────────────────────────────────────

    async def diff_regras(
        self,
        rule_a:              Rule,
        rule_b:              Rule,
        historical_sessions: list[list[EvaluationContext]],
    ) -> dict:
        """
        Compares the behaviour of two rules against the same historical dataset.
        Returns: conversations that would fire A but not B, B but not A, and both.
        """
        only_a, only_b, both, neither = 0, 0, 0, 0

        for session_contexts in historical_sessions:
            triggered_a = any(
                self._evaluator.evaluate(rule_a, ctx).triggered
                for ctx in session_contexts
            )
            triggered_b = any(
                self._evaluator.evaluate(rule_b, ctx).triggered
                for ctx in session_contexts
            )

            if triggered_a and triggered_b:
                both += 1
            elif triggered_a:
                only_a += 1
            elif triggered_b:
                only_b += 1
            else:
                neither += 1

        total = len(historical_sessions)
        return {
            "total_conversations": total,
            "only_rule_a":  only_a,
            "only_rule_b":  only_b,
            "both":         both,
            "neither":      neither,
            "rate_a":       round((only_a + both) / max(total, 1), 4),
            "rate_b":       round((only_b + both) / max(total, 1), 4),
            "diff_at":      datetime.now(timezone.utc).isoformat(),
        }

    # ─────────────────────────────────────────────
    # 4. Session simulator (spec 3.2b)
    # ─────────────────────────────────────────────

    def simulate_session(
        self, request: SessionSimulatorRequest
    ) -> SessionSimulatorResult:
        """
        Tests a rule with manually supplied parameters.
        Useful for debugging an individual rule without needing historical data.
        """
        ctx = EvaluationContext(
            session_id=        "simulator",
            tenant_id=         request.tenant_id,
            turn_count=        request.turn_count,
            elapsed_ms=        request.elapsed_ms,
            sentiment_score=   request.sentiment_score,
            intent_confidence= request.intent_confidence,
            flags=             request.flags,
        )

        result = self._evaluator.evaluate(request.rule, ctx)

        return SessionSimulatorResult(
            triggered=         result.triggered,
            condition_results= result.condition_results,
            target_pool=       request.rule.target_pool if result.triggered else None,
        )
