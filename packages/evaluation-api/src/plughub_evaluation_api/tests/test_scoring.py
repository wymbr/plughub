"""
test_scoring.py — #23 — aggregate_scores honouring aggregation/scoring_method,
aligned to the shared @plughub/schemas scoring.ts `composeScore` primitive.

Two layers:
  • _compose — the 0..10 kernel; mirrors composeScore's math (weighted_mean / min /
    Σw==0 → 0 / empty → None). Parity: on 0..10-normalized items these produce the
    same numbers composeScore does (see packages/schemas/src/scoring.test.ts).
  • aggregate_scores — form-driven bottom-up: criteria→dimension (weighted_average |
    min_score) and dimension→composite (weighted_average | simple_average). Includes a
    regression lock on the default (weighted/weighted) path = the pre-#23 behaviour.
"""
from __future__ import annotations

from ..scoring import aggregate_scores, _compose


# ── helpers ──────────────────────────────────────────────────────────────────

def _crit(cid, type="score", weight=1, max_score=10, **extra):
    return {"criterion_id": cid, "type": type, "weight": weight, "max_score": max_score, **extra}


def _dim(did, criteria, weight=1, aggregation="weighted_average"):
    return {"dimension_id": did, "weight": weight, "aggregation": aggregation, "criteria": criteria}


def _form(dimensions, scoring_method="weighted_average"):
    return {"dimensions": dimensions, "scoring_method": scoring_method}


# ── _compose kernel (composeScore parity on the 0..10 scale) ─────────────────

class TestCompose:
    def test_weighted_mean_uniform(self):
        assert _compose([(8, 1), (6, 1), (4, 1)], "weighted_mean") == 6.0

    def test_weighted_mean_weighted(self):
        assert _compose([(10, 3), (2, 1)], "weighted_mean") == 8.0

    def test_min_ignores_weight(self):
        assert _compose([(10, 5), (4, 1)], "min") == 4.0

    def test_zero_weight_sum_is_zero(self):
        # matches composeScore: wsum==0 → 0 (not None)
        assert _compose([(5, 0)], "weighted_mean") == 0.0

    def test_empty_is_none(self):
        assert _compose([], "weighted_mean") is None
        assert _compose([], "min") is None


# ── aggregate_scores ─────────────────────────────────────────────────────────

class TestAggregateScores:
    def test_default_weighted_average_regression(self):
        # Locks the pre-#23 behaviour: criteria weighted mean, one dimension.
        form = _form([_dim("d1", [_crit("c1"), _crit("c2")])])
        resp = [{"criterion_id": "c1", "score": 8}, {"criterion_id": "c2", "score": 6}]
        overall, by_dim = aggregate_scores(form, resp)
        assert overall == 7.0
        assert by_dim == [{"dimension_id": "d1", "score": 7.0}]

    def test_criterion_weights(self):
        form = _form([_dim("d1", [_crit("c1", weight=3), _crit("c2", weight=1)])])
        resp = [{"criterion_id": "c1", "score": 10}, {"criterion_id": "c2", "score": 2}]
        overall, _bd = aggregate_scores(form, resp)
        assert overall == 8.0  # (10*3 + 2*1) / 4

    def test_dimension_min_score(self):
        # aggregation=min_score → worst criterion, not the mean.
        form = _form([_dim("d1", [_crit("c1"), _crit("c2")], aggregation="min_score")])
        resp = [{"criterion_id": "c1", "score": 9}, {"criterion_id": "c2", "score": 4}]
        overall, by_dim = aggregate_scores(form, resp)
        assert overall == 4.0
        assert by_dim == [{"dimension_id": "d1", "score": 4.0}]

    def test_scoring_method_weighted_vs_simple(self):
        dims = [_dim("d1", [_crit("c1")], weight=3), _dim("d2", [_crit("c2")], weight=1)]
        resp = [{"criterion_id": "c1", "score": 9}, {"criterion_id": "c2", "score": 3}]
        # weighted: (9*3 + 3*1) / 4 = 7.5
        overall_w, _ = aggregate_scores(_form(dims, "weighted_average"), resp)
        assert overall_w == 7.5
        # simple: (9 + 3) / 2 = 6.0 (dimension weights ignored)
        overall_s, _ = aggregate_scores(_form(dims, "simple_average"), resp)
        assert overall_s == 6.0

    def test_na_dropped_and_renormalized(self):
        form = _form([_dim("d1", [_crit("c1", na_allowed=True), _crit("c2"), _crit("c3")])])
        resp = [
            {"criterion_id": "c1", "na": True},
            {"criterion_id": "c2", "score": 8},
            {"criterion_id": "c3", "score": 8},
        ]
        overall, _bd = aggregate_scores(form, resp)
        assert overall == 8.0  # NA must not pull the mean down

    def test_all_text_or_na_returns_none(self):
        form = _form([_dim("d1", [_crit("c1", type="text"), _crit("c2", na_allowed=True)])])
        resp = [{"criterion_id": "c2", "na": True}]
        overall, by_dim = aggregate_scores(form, resp)
        assert overall is None
        assert by_dim == []

    def test_heterogeneous_scales_normalized_before_min(self):
        # c1 (max 5, score 5) → 10; c2 (max 10, score 5) → 5; min → 5.
        form = _form([_dim(
            "d1", [_crit("c1", max_score=5), _crit("c2", max_score=10)], aggregation="min_score",
        )])
        resp = [{"criterion_id": "c1", "score": 5}, {"criterion_id": "c2", "score": 5}]
        overall, _bd = aggregate_scores(form, resp)
        assert overall == 5.0

    def test_choice_and_boolean_mapping(self):
        form = _form([_dim("d1", [
            _crit("c1", type="choice", choice_scores={"good": 10, "bad": 0}),
            _crit("c2", type="boolean", true_score=10, false_score=0),
        ])])
        resp = [
            {"criterion_id": "c1", "choice_value": "good"},
            {"criterion_id": "c2", "boolean_value": True},
        ]
        overall, _bd = aggregate_scores(form, resp)
        assert overall == 10.0
