"""
test_context.py
Tests for session parameter extraction.
"""

import pytest
from ..context import extract_context_from_response, ExtractedContext


def test_detects_churn_signal_flag():
    ctx = extract_context_from_response(
        user_message="quero cancelar minha linha",
        assistant_response="Entendo, vou verificar opções para você.",
        call_type="response_generation",
    )
    assert "churn_signal" in ctx.flags


def test_detects_escalation_hint_flag():
    ctx = extract_context_from_response(
        user_message="quero falar com um atendente humano",
        assistant_response="Vou transferir para um especialista.",
        call_type="response_generation",
    )
    assert "escalation_hint" in ctx.flags


def test_sentiment_negative_when_customer_frustrated():
    ctx = extract_context_from_response(
        user_message="isso é um absurdo, péssimo serviço",
        assistant_response="Lamento o inconveniente.",
        call_type="sentiment_analysis",
    )
    assert ctx.sentiment_score < 0


def test_sentiment_positive_when_customer_satisfied():
    ctx = extract_context_from_response(
        user_message="ótimo, problema resolvido, obrigado",
        assistant_response="Fico feliz em ter ajudado!",
        call_type="response_generation",
    )
    assert ctx.sentiment_score > 0


def test_portability_intent():
    ctx = extract_context_from_response(
        user_message="quero fazer portabilidade da minha linha",
        assistant_response="Posso ajudar com a portabilidade.",
        call_type="intent_classification",
    )
    assert ctx.intent == "portability_check"
    assert ctx.confidence > 0.5


def test_no_intent_when_generic_message():
    ctx = extract_context_from_response(
        user_message="olá",
        assistant_response="Olá! Como posso ajudar?",
        call_type="response_generation",
    )
    # intent may be None or confidence low
    assert ctx.intent is None or ctx.confidence < 0.6


def test_multiple_flags_detected():
    ctx = extract_context_from_response(
        user_message="quero cancelar, é urgente, não funciona nada",
        assistant_response="Vou verificar imediatamente.",
        call_type="response_generation",
    )
    assert len(ctx.flags) >= 2
    assert "churn_signal" in ctx.flags
    assert "urgency" in ctx.flags
