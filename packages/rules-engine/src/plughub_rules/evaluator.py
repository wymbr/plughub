"""
evaluator.py
Evaluates rules against session parameters.
Spec: PlugHub v24.0 section 3.2

Stateless — no own state, no LLM.
Evaluates only declarative expressions on observable parameters.
"""

from __future__ import annotations
from datetime import datetime, timezone
import statistics

from .models import (
    Rule, EvaluationContext, EvaluationResult,
    Condition, ConditionResult,
)


class RuleEvaluator:

    def evaluate(
        self,
        rule:    Rule,
        context: EvaluationContext,
    ) -> EvaluationResult:
        """
        Evaluates a rule against the session context.
        Returns EvaluationResult with triggered=True if the rule fires.
        """
        condition_results = [
            self._evaluate_condition(cond, context)
            for cond in rule.conditions
        ]

        if rule.logic == "AND":
            triggered = all(r.matched for r in condition_results)
        else:  # OR
            triggered = any(r.matched for r in condition_results)

        return EvaluationResult(
            rule=rule,
            triggered=triggered,
            condition_results=condition_results,
            context=context,
            evaluated_at=datetime.now(timezone.utc).isoformat(),
        )

    def _evaluate_condition(
        self,
        cond:    Condition,
        context: EvaluationContext,
    ) -> ConditionResult:
        observed_value = self._get_observed_value(cond, context)
        matched        = self._apply_operator(observed_value, cond.operator, cond.value)

        return ConditionResult(
            condition=cond,
            matched=matched,
            observed_value=observed_value,
        )

    def _get_observed_value(
        self,
        cond:    Condition,
        context: EvaluationContext,
    ) -> float | str | None:
        match cond.parameter:
            case "sentiment_score":
                if cond.window_turns and len(context.sentiment_history) >= cond.window_turns:
                    window = context.sentiment_history[-cond.window_turns:]
                    return statistics.mean(window)
                return context.sentiment_score

            case "intent_confidence":
                return context.intent_confidence

            case "turn_count":
                return float(context.turn_count)

            case "elapsed_ms":
                return float(context.elapsed_ms)

            case "flag":
                if cond.flag_name:
                    return cond.flag_name if cond.flag_name in context.flags else None
                return None

            case _:
                return None

    def _apply_operator(
        self,
        observed: float | str | None,
        operator: str,
        expected: float | str,
    ) -> bool:
        """Applies the comparison operator."""
        if observed is None:
            return False

        match operator:
            case "lt":
                return isinstance(observed, (int, float)) and observed < float(expected)
            case "lte":
                return isinstance(observed, (int, float)) and observed <= float(expected)
            case "gt":
                return isinstance(observed, (int, float)) and observed > float(expected)
            case "gte":
                return isinstance(observed, (int, float)) and observed >= float(expected)
            case "eq":
                return str(observed) == str(expected)
            case "neq":
                return str(observed) != str(expected)
            case "contains":
                return str(expected) in str(observed)
            case _:
                return False
