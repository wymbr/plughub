"""
test_comparator.py
Unit tests para session_replayer/comparator.py

Cobre:
  - _jaccard: textos idênticos, completamente diferentes, overlap parcial,
               ambos vazios, um vazio
  - compare: sem turns, similarity_score, divergence_points, threshold,
             outcome_delta divergido/não-divergido, sentiment_delta, latency_delta,
             to_dict serialization
"""

import pytest
from session_replayer.comparator import (
    Comparator,
    ComparisonTurn,
    ComparisonReport,
)


# ─── _jaccard (via jaccardSimilarity público via Comparator._jaccard) ─────────

def test_jaccard_identical_texts():
    score = Comparator._jaccard("o cliente ligou para cancelar o plano", "o cliente ligou para cancelar o plano")
    assert score == 1.0


def test_jaccard_completely_different():
    score = Comparator._jaccard("bom dia senhor", "cancelamento efetivado com sucesso")
    assert score == 0.0


def test_jaccard_partial_overlap():
    score = Comparator._jaccard("bom dia como posso ajudar", "bom dia tudo bem")
    # "bom" e "dia" se cruzam → 2 em common
    assert 0.0 < score < 1.0


def test_jaccard_both_empty():
    score = Comparator._jaccard("", "")
    assert score == 1.0


def test_jaccard_one_empty():
    score = Comparator._jaccard("", "alguma coisa")
    assert score == 0.0
    score2 = Comparator._jaccard("alguma coisa", "")
    assert score2 == 0.0


def test_jaccard_case_insensitive():
    score = Comparator._jaccard("Bom Dia", "bom dia")
    assert score == 1.0


def test_jaccard_punctuation_stripped():
    score = Comparator._jaccard("olá! tudo bem?", "olá tudo bem")
    assert score == 1.0


# ─── compare — similarity_score e divergence_points ──────────────────────────

def test_compare_empty_turns():
    c = Comparator()
    report = c.compare([])
    assert report.similarity_score == 1.0
    assert report.divergence_points == []


def test_compare_identical_turns():
    c = Comparator()
    turns = [
        ComparisonTurn(0, "entendo sua situação vou verificar", "entendo sua situação vou verificar"),
        ComparisonTurn(1, "seu cancelamento foi processado", "seu cancelamento foi processado"),
    ]
    report = c.compare(turns)
    assert report.similarity_score == 1.0
    assert report.divergence_points == []


def test_compare_divergence_detected():
    c = Comparator(threshold=0.4)
    turns = [
        ComparisonTurn(0, "bom dia", "boa tarde"),                     # baixa sobreposição
        ComparisonTurn(1, "claro posso ajudar com isso", "cancelamento realizado"),  # sem sobreposição
    ]
    report = c.compare(turns)
    assert len(report.divergence_points) > 0
    for dp in report.divergence_points:
        assert dp.similarity < 0.4


def test_compare_no_divergence_above_threshold():
    c = Comparator(threshold=0.2)  # threshold baixo — só diverge em casos extremos
    turns = [
        ComparisonTurn(0, "bom dia como posso ajudar", "bom dia em que posso te ajudar"),
    ]
    report = c.compare(turns)
    assert len(report.divergence_points) == 0


def test_compare_similarity_score_is_average():
    c = Comparator()
    # Turn idêntico + turn vazio ambos → média de 1.0 + 1.0 = 1.0
    turns = [
        ComparisonTurn(0, "abc def", "abc def"),
        ComparisonTurn(1, "", ""),
    ]
    report = c.compare(turns)
    assert report.similarity_score == 1.0


# ─── compare — outcome_delta ──────────────────────────────────────────────────

def test_outcome_delta_diverged():
    c = Comparator()
    report = c.compare(
        [ComparisonTurn(0, "ok", "ok")],
        production_outcome="resolved",
        replay_outcome="abandoned",
    )
    assert report.outcome_delta is not None
    assert report.outcome_delta.diverged is True
    assert report.outcome_delta.production_outcome == "resolved"
    assert report.outcome_delta.replay_outcome == "abandoned"


def test_outcome_delta_not_diverged():
    c = Comparator()
    report = c.compare(
        [ComparisonTurn(0, "ok", "ok")],
        production_outcome="resolved",
        replay_outcome="resolved",
    )
    assert report.outcome_delta is not None
    assert report.outcome_delta.diverged is False


def test_outcome_delta_absent_when_not_provided():
    c = Comparator()
    report = c.compare([ComparisonTurn(0, "texto", "texto")])
    assert report.outcome_delta is None


# ─── compare — sentiment_delta ────────────────────────────────────────────────

def test_sentiment_delta_computed():
    c = Comparator()
    report = c.compare(
        [ComparisonTurn(0, "ok", "ok")],
        production_final_sentiment=0.6,
        replay_final_sentiment=0.2,
    )
    assert report.sentiment_delta is not None
    assert abs(report.sentiment_delta.delta - (-0.4)) < 1e-6


def test_sentiment_delta_absent_when_not_provided():
    c = Comparator()
    report = c.compare([ComparisonTurn(0, "ok", "ok")])
    assert report.sentiment_delta is None


# ─── compare — latency_delta ──────────────────────────────────────────────────

def test_latency_delta_computed():
    c = Comparator()
    turns = [
        ComparisonTurn(0, "a", "a", production_latency_ms=200.0, replay_latency_ms=150.0),
        ComparisonTurn(1, "b", "b", production_latency_ms=400.0, replay_latency_ms=250.0),
    ]
    report = c.compare(turns)
    assert report.latency_delta is not None
    assert abs(report.latency_delta.production_avg_ms - 300.0) < 1e-6
    assert abs(report.latency_delta.replay_avg_ms - 200.0) < 1e-6
    assert abs(report.latency_delta.delta_ms - (-100.0)) < 1e-6


def test_latency_delta_absent_when_no_latency():
    c = Comparator()
    turns = [ComparisonTurn(0, "a", "b")]
    report = c.compare(turns)
    assert report.latency_delta is None


# ─── to_dict serialization ────────────────────────────────────────────────────

def test_to_dict_minimal():
    c = Comparator()
    report = c.compare([ComparisonTurn(0, "igual", "igual")])
    d = report.to_dict()
    assert "similarity_score" in d
    assert "divergence_points" in d
    assert isinstance(d["divergence_points"], list)
    # campos opcionais não presentes quando ausentes
    assert "outcome_delta" not in d
    assert "sentiment_delta" not in d
    assert "latency_delta" not in d


def test_to_dict_full():
    c = Comparator(threshold=0.9)  # threshold alto para forçar divergência
    turns = [ComparisonTurn(0, "bom dia", "olá como vai", production_latency_ms=100.0, replay_latency_ms=80.0)]
    report = c.compare(
        turns,
        production_outcome="resolved",
        replay_outcome="abandoned",
        production_final_sentiment=0.5,
        replay_final_sentiment=-0.1,
    )
    d = report.to_dict()
    assert "similarity_score" in d
    assert "divergence_points" in d
    assert "outcome_delta" in d
    assert d["outcome_delta"]["diverged"] is True
    assert "sentiment_delta" in d
    assert "latency_delta" in d


# ─── Construtor — threshold inválido ─────────────────────────────────────────

def test_invalid_threshold_raises():
    with pytest.raises(ValueError, match="threshold"):
        Comparator(threshold=1.5)

    with pytest.raises(ValueError, match="threshold"):
        Comparator(threshold=-0.1)
