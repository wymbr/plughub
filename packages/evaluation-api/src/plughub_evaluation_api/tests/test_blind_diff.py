"""
test_blind_diff.py
R8c — pure unit tests for scoring.compute_dimension_diffs (no I/O).
"""
from __future__ import annotations

from ..scoring import compute_dimension_diffs, blind_resolution_status


def _dim(did, score):
    return {"dimension_id": did, "score": score}


class TestComputeDimensionDiffs:
    def test_both_present_within_threshold_no_disagree(self):
        ai = [_dim("d1", 8.0)]
        hu = [_dim("d1", 7.0)]  # diff 0.1 normalized
        out = compute_dimension_diffs(ai, hu, severity_min=0.2)
        assert out == [{
            "dimension_id": "d1", "ai_score": 8.0, "human_score": 7.0,
            "diff": 0.1, "disagree": False,
        }]

    def test_both_present_beyond_threshold_disagree(self):
        ai = [_dim("d1", 9.0)]
        hu = [_dim("d1", 5.0)]  # diff 0.4 > 0.2
        out = compute_dimension_diffs(ai, hu, severity_min=0.2)
        assert out[0]["diff"] == 0.4
        assert out[0]["disagree"] is True

    def test_threshold_is_strict_gt(self):
        # diff exactly == severity_min → NOT a disagreement (strict >).
        ai = [_dim("d1", 8.0)]
        hu = [_dim("d1", 6.0)]  # diff 0.2
        out = compute_dimension_diffs(ai, hu, severity_min=0.2)
        assert out[0]["diff"] == 0.2
        assert out[0]["disagree"] is False

    def test_missing_human_side_is_na_not_disagreement(self):
        ai = [_dim("d1", 9.0)]
        hu: list[dict] = []
        out = compute_dimension_diffs(ai, hu, severity_min=0.2)
        assert out[0]["ai_score"] == 9.0
        assert out[0]["human_score"] is None
        assert out[0]["diff"] is None
        assert out[0]["disagree"] is False

    def test_missing_ai_side_is_na(self):
        ai: list[dict] = []
        hu = [_dim("d2", 4.0)]
        out = compute_dimension_diffs(ai, hu, severity_min=0.2)
        assert out[0]["dimension_id"] == "d2"
        assert out[0]["ai_score"] is None
        assert out[0]["human_score"] == 4.0
        assert out[0]["disagree"] is False

    def test_union_order_ai_first_then_human_extras(self):
        ai = [_dim("d1", 8.0), _dim("d2", 7.0)]
        hu = [_dim("d2", 7.0), _dim("d3", 5.0)]
        out = compute_dimension_diffs(ai, hu, severity_min=0.2)
        assert [d["dimension_id"] for d in out] == ["d1", "d2", "d3"]

    def test_multiple_disagreements_counted(self):
        ai = [_dim("d1", 10.0), _dim("d2", 9.0), _dim("d3", 8.0)]
        hu = [_dim("d1", 3.0), _dim("d2", 9.0), _dim("d3", 2.0)]
        out = compute_dimension_diffs(ai, hu, severity_min=0.25)
        disagreeing = [d["dimension_id"] for d in out if d["disagree"]]
        assert disagreeing == ["d1", "d3"]

    def test_none_scores_treated_as_zero(self):
        ai = [{"dimension_id": "d1", "score": None}]
        hu = [_dim("d1", 0.0)]
        out = compute_dimension_diffs(ai, hu, severity_min=0.2)
        assert out[0]["diff"] == 0.0
        assert out[0]["disagree"] is False


class TestBlindResolutionStatus:
    def test_no_disagreement_is_approved(self):
        assert blind_resolution_status(0, flag_bias=False) == "approved"
        assert blind_resolution_status(0, flag_bias=True) == "approved"  # nothing to flag

    def test_disagreement_default_recalibrated(self):
        assert blind_resolution_status(2, flag_bias=False) == "recalibrated"

    def test_disagreement_with_bias_flag(self):
        assert blind_resolution_status(1, flag_bias=True) == "bias_flagged"

    def test_negative_count_treated_as_none(self):
        assert blind_resolution_status(-1, flag_bias=False) == "approved"
