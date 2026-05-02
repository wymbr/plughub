"""
comparator.py
Comparador turn-a-turn para comparison_mode do Session Replayer.

Responsabilidade única:
  - Recebe pares (production_text, replay_text) com metadados de latência
  - Calcula similaridade por turn usando Jaccard token-level (sem dependências externas)
  - Produz ComparisonReport com:
      - similarity_score: média ponderada de todos os turns
      - divergence_points: turns abaixo do threshold configurado
      - outcome_delta: se o outcome diferiu entre produção e replay
      - sentiment_delta: diferença entre sentimento final de produção vs replay
      - latency_delta: diferença de latência média de resposta

Não faz I/O. Puramente computacional — pode ser chamado de qualquer contexto
(consumer, MCP tool, testes unitários) sem efeitos colaterais.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


# ─── Tipos de entrada ──────────────────────────────────────────────────────────

@dataclass
class ComparisonTurn:
    """Um par de mensagens a comparar: produção vs replay."""
    turn_index:            int
    production_text:       str
    replay_text:           str
    production_latency_ms: Optional[float] = None
    replay_latency_ms:     Optional[float] = None


# ─── Tipos de saída ───────────────────────────────────────────────────────────

@dataclass
class DivergencePoint:
    turn_index:      int
    production_text: str
    replay_text:     str
    similarity:      float


@dataclass
class OutcomeDelta:
    production_outcome: str
    replay_outcome:     str
    diverged:           bool


@dataclass
class SentimentDelta:
    production_final: float
    replay_final:     float
    delta:            float


@dataclass
class LatencyDelta:
    production_avg_ms: float
    replay_avg_ms:     float
    delta_ms:          float


@dataclass
class ComparisonReport:
    """Resultado completo da comparação turn-a-turn."""
    similarity_score:  float
    divergence_points: list[DivergencePoint]     = field(default_factory=list)
    outcome_delta:     Optional[OutcomeDelta]    = None
    sentiment_delta:   Optional[SentimentDelta]  = None
    latency_delta:     Optional[LatencyDelta]    = None

    def to_dict(self) -> dict:
        """Serializa para o formato esperado pelo ComparisonReportSchema (TypeScript)."""
        result: dict = {
            "similarity_score":  round(self.similarity_score, 4),
            "divergence_points": [
                {
                    "turn_index":       dp.turn_index,
                    "production_text":  dp.production_text,
                    "replay_text":      dp.replay_text,
                    "similarity":       round(dp.similarity, 4),
                }
                for dp in self.divergence_points
            ],
        }
        if self.outcome_delta is not None:
            result["outcome_delta"] = {
                "production_outcome": self.outcome_delta.production_outcome,
                "replay_outcome":     self.outcome_delta.replay_outcome,
                "diverged":           self.outcome_delta.diverged,
            }
        if self.sentiment_delta is not None:
            result["sentiment_delta"] = {
                "production_final": round(self.sentiment_delta.production_final, 4),
                "replay_final":     round(self.sentiment_delta.replay_final, 4),
                "delta":            round(self.sentiment_delta.delta, 4),
            }
        if self.latency_delta is not None:
            result["latency_delta"] = {
                "production_avg_ms": round(self.latency_delta.production_avg_ms, 2),
                "replay_avg_ms":     round(self.latency_delta.replay_avg_ms, 2),
                "delta_ms":          round(self.latency_delta.delta_ms, 2),
            }
        return result


# ─── Comparator ───────────────────────────────────────────────────────────────

class Comparator:
    """
    Compara sessão de produção vs sessão de replay turn-a-turn.

    Algoritmo de similaridade: Jaccard sobre tokens normalizados.
    Escolhido por ser determinístico, sem dependências externas e O(n) no
    número de tokens — adequado para respostas de agente de 50–500 tokens.

    threshold: turns com similaridade < threshold são reportados como
               divergence_points. Default: 0.4 (limiar conservador que
               distingue respostas completamente diferentes de paráfrases).
    """

    def __init__(self, threshold: float = 0.4) -> None:
        if not 0.0 <= threshold <= 1.0:
            raise ValueError(f"threshold deve estar em [0, 1], recebido: {threshold}")
        self._threshold = threshold

    # ─── API pública ──────────────────────────────────────────────────────────

    def compare(
        self,
        turns:              list[ComparisonTurn],
        production_outcome: Optional[str] = None,
        replay_outcome:     Optional[str] = None,
        production_final_sentiment: Optional[float] = None,
        replay_final_sentiment:     Optional[float] = None,
    ) -> ComparisonReport:
        """
        Produz ComparisonReport a partir dos pares de turns e metadados opcionais.

        Args:
            turns: pares de mensagens a comparar (produção vs replay)
            production_outcome: outcome da sessão original (ex: "resolved")
            replay_outcome: outcome alcançado no replay
            production_final_sentiment: score de sentimento final da produção (-1 a 1)
            replay_final_sentiment: score de sentimento final do replay (-1 a 1)

        Returns:
            ComparisonReport com todos os campos calculados.
        """
        if not turns:
            return ComparisonReport(
                similarity_score  = 1.0,
                divergence_points = [],
            )

        # Calcula similaridade por turn
        similarities: list[float] = [
            self._jaccard(t.production_text, t.replay_text)
            for t in turns
        ]

        # Média simples — sem pesos por turn
        avg_similarity = sum(similarities) / len(similarities)

        # Divergence points: turns abaixo do threshold
        divergence_points = [
            DivergencePoint(
                turn_index      = t.turn_index,
                production_text = t.production_text,
                replay_text     = t.replay_text,
                similarity      = similarities[i],
            )
            for i, t in enumerate(turns)
            if similarities[i] < self._threshold
        ]

        # Outcome delta
        outcome_delta: Optional[OutcomeDelta] = None
        if production_outcome is not None and replay_outcome is not None:
            outcome_delta = OutcomeDelta(
                production_outcome = production_outcome,
                replay_outcome     = replay_outcome,
                diverged           = production_outcome != replay_outcome,
            )

        # Sentiment delta
        sentiment_delta: Optional[SentimentDelta] = None
        if production_final_sentiment is not None and replay_final_sentiment is not None:
            sentiment_delta = SentimentDelta(
                production_final = production_final_sentiment,
                replay_final     = replay_final_sentiment,
                delta            = replay_final_sentiment - production_final_sentiment,
            )

        # Latency delta
        latency_delta: Optional[LatencyDelta] = None
        prod_latencies = [t.production_latency_ms for t in turns if t.production_latency_ms is not None]
        replay_latencies = [t.replay_latency_ms for t in turns if t.replay_latency_ms is not None]

        if prod_latencies and replay_latencies:
            prod_avg   = sum(prod_latencies)   / len(prod_latencies)
            replay_avg = sum(replay_latencies) / len(replay_latencies)
            latency_delta = LatencyDelta(
                production_avg_ms = prod_avg,
                replay_avg_ms     = replay_avg,
                delta_ms          = replay_avg - prod_avg,
            )

        return ComparisonReport(
            similarity_score  = avg_similarity,
            divergence_points = divergence_points,
            outcome_delta     = outcome_delta,
            sentiment_delta   = sentiment_delta,
            latency_delta     = latency_delta,
        )

    # ─── Algoritmo de similaridade ────────────────────────────────────────────

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        """
        Normaliza e tokeniza texto.

        Normalização:
          - lowercase
          - remove pontuação (mantém apenas alfanuméricos e espaços)
          - split em whitespace
          - filtra tokens vazios

        Tokens de uma palavra são suficientes para Jaccard em linguagem natural.
        """
        normalized = re.sub(r"[^\w\s]", " ", text.lower())
        return {t for t in normalized.split() if t}

    @classmethod
    def _jaccard(cls, a: str, b: str) -> float:
        """
        Coeficiente de Jaccard sobre conjuntos de tokens.

          J(A, B) = |A ∩ B| / |A ∪ B|

        Casos especiais:
          - Ambos vazios → 1.0 (idênticos por definição)
          - Um vazio e outro não → 0.0 (completamente diferentes)
        """
        tokens_a = cls._tokenize(a)
        tokens_b = cls._tokenize(b)

        if not tokens_a and not tokens_b:
            return 1.0
        if not tokens_a or not tokens_b:
            return 0.0

        intersection = len(tokens_a & tokens_b)
        union        = len(tokens_a | tokens_b)

        return intersection / union
