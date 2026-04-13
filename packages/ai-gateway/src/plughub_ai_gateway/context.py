"""
context.py
Intra-turn extraction of session parameters.
Spec: PlugHub v24.0 section 2.2a

Extracts intent, confidence, sentiment_score and flags from each LLM response.
Written to Redis immediately — does not wait for end of turn.
"""

from __future__ import annotations
import re
from dataclasses import dataclass


@dataclass
class ExtractedContext:
    intent:          str | None
    confidence:      float
    sentiment_score: float
    flags:           list[str]


# Semantic flags detected by the extractor
SEMANTIC_FLAGS = {
    "churn_signal":     ["cancelar", "sair", "portabilidade", "cancelamento", "encerrar contrato"],
    "high_frustration": ["absurdo", "ridículo", "péssimo", "inaceitável", "revoltante"],
    "urgency":          ["urgente", "agora", "imediato", "emergência", "preciso agora"],
    "high_value":       ["platinum", "premium", "vip", "corporativo"],
    "escalation_hint":  ["falar com humano", "atendente", "gerente", "pessoa real"],
}


def extract_context_from_response(
    user_message: str,
    assistant_response: str,
    call_type: str,
) -> ExtractedContext:
    """
    Extracts context parameters from the message exchange.

    In production, specific call_types (intent_classification, sentiment_analysis)
    use dedicated LLM calls for precise extraction.
    For response_generation, lightweight heuristics are used to avoid added latency.
    """
    text = f"{user_message} {assistant_response}".lower()

    # ── Semantic flag detection ──
    detected_flags: list[str] = []
    for flag, keywords in SEMANTIC_FLAGS.items():
        if any(kw in text for kw in keywords):
            detected_flags.append(flag)

    # ── Heuristic sentiment ──
    # In production: sentiment model fine-tuned per vertical
    negative_words = ["problema", "erro", "ruim", "péssimo", "cancelar", "absurdo",
                      "indignado", "frustrado", "demora", "não funciona"]
    positive_words = ["ótimo", "excelente", "obrigado", "resolvido", "perfeito",
                      "satisfeito", "agradeço", "funcionou"]

    neg_count = sum(1 for w in negative_words if w in text)
    pos_count = sum(1 for w in positive_words if w in text)
    total = neg_count + pos_count or 1
    raw_sentiment = (pos_count - neg_count) / total
    sentiment_score = max(-1.0, min(1.0, raw_sentiment))

    # ── Intent and confidence ──
    # In production: multi-class classifier per vertical
    # Here: keyword heuristic
    intent, confidence = _classify_intent(text)

    return ExtractedContext(
        intent=intent,
        confidence=confidence,
        sentiment_score=sentiment_score,
        flags=detected_flags,
    )


def _classify_intent(text: str) -> tuple[str | None, float]:
    """Intent classification by keyword. Replace with a model in production."""
    intent_keywords: dict[str, list[str]] = {
        "portability_check":  ["portabilidade", "portar", "trocar de operadora"],
        "billing_query":      ["fatura", "cobrança", "boleto", "pagamento", "débito"],
        "technical_support":  ["internet", "sinal", "lentidão", "sem conexão", "não funciona"],
        "cancellation":       ["cancelar", "encerrar", "cancelamento", "sair"],
        "retention":          ["fidelidade", "fidelizar", "continuar", "renovar"],
        "general_inquiry":    ["informação", "dúvida", "como funciona", "quero saber"],
    }

    best_intent: str | None = None
    best_count = 0

    for intent, keywords in intent_keywords.items():
        count = sum(1 for kw in keywords if kw in text)
        if count > best_count:
            best_count = count
            best_intent = intent

    confidence = min(0.95, 0.50 + (best_count * 0.15)) if best_intent else 0.0
    return best_intent, confidence
