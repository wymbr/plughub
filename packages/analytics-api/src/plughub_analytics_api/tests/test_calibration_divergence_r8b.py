"""
test_calibration_divergence_r8b.py — R8b (gatilho Estágio 1: divergência).

Cobre a lógica pura do gatilho (sem ClickHouse): divergence = 1 − score/100;
recalibration_recommended = divergence > limiar ∧ total ≥ N mínimo. É sinal, não
auto-mutação. Cobre também a coerção de tipos do config_client.
"""
from plughub_analytics_api.reports_query import apply_divergence_flags
from plughub_analytics_api.config_client import _coerce


def test_flag_fires_above_threshold_with_enough_sample():
    rows = [{"calibration_score": 70.0, "total": 50}]   # div 0.30 > 0.25, N=50 ≥ 30
    summary: dict = {}
    n = apply_divergence_flags(rows, summary, 0.25, 30)
    assert rows[0]["divergence"] == 0.30
    assert rows[0]["recalibration_recommended"] is True
    assert n == 1
    assert summary["recalibration_recommended_count"] == 1


def test_no_flag_below_threshold():
    rows = [{"calibration_score": 80.0, "total": 50}]   # div 0.20 ≤ 0.25
    apply_divergence_flags(rows, {}, 0.25, 30)
    assert rows[0]["divergence"] == 0.20
    assert rows[0]["recalibration_recommended"] is False


def test_no_flag_when_sample_too_small():
    rows = [{"calibration_score": 50.0, "total": 5}]    # div 0.50 mas N=5 < 30
    apply_divergence_flags(rows, {}, 0.25, 30)
    assert rows[0]["recalibration_recommended"] is False


def test_null_score_is_na():
    rows = [{"calibration_score": None, "total": 100}]
    apply_divergence_flags(rows, {}, 0.25, 30)
    assert rows[0]["divergence"] is None
    assert rows[0]["recalibration_recommended"] is False


def test_config_coerce_preserves_default_type():
    assert _coerce("0.3", 0.25) == 0.3          # str → float
    assert _coerce("30", 30) == 30              # str → int
    assert _coerce(None, 30) == 30              # ausente → default
    assert _coerce("lixo", 30) == 30            # inválido → default


if __name__ == "__main__":
    for fn in [
        test_flag_fires_above_threshold_with_enough_sample,
        test_no_flag_below_threshold,
        test_no_flag_when_sample_too_small,
        test_null_score_is_na,
        test_config_coerce_preserves_default_type,
    ]:
        fn()
        print(f"  PASS {fn.__name__}")
    print("ALL PASS")
