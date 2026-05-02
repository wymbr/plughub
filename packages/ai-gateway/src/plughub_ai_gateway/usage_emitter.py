"""
usage_emitter.py
Utilitário de emissão de eventos de consumo (usage.events) para o AI Gateway.

Princípio: metering ≠ pricing.
Publica apenas o fato do consumo — sem preço, sem plano, sem quota.
O módulo de pricing (a construir) lê estes dados e decide o que cobrar.

Tópico Kafka: usage.events
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("plughub.ai_gateway.usage")


async def emit_llm_tokens(
    producer: Any,           # aiokafka.AIOKafkaProducer ou duck-type compatível
    tenant_id:    str,
    session_id:   str | None,
    model_id:     str,
    agent_type_id: str | None,
    input_tokens:  int,
    output_tokens: int,
    gateway_id:   str = "ai-gateway",
) -> None:
    """
    Publica dois eventos de consumo em usage.events:
      - llm_tokens_input  (qty = input_tokens)
      - llm_tokens_output (qty = output_tokens)

    Input e output são eventos separados porque têm tarifas distintas em todos
    os provedores LLM.

    Fire-and-forget: erros são logados mas nunca bloqueiam o caminho operacional.
    """
    if input_tokens <= 0 and output_tokens <= 0:
        return  # resposta em cache ou erro — sem tokens reais

    timestamp = datetime.now(timezone.utc).isoformat()
    metadata: dict[str, Any] = {
        "model_id":     model_id,
        "gateway_id":   gateway_id,
    }
    if agent_type_id:
        metadata["agent_type_id"] = agent_type_id

    events = []
    if input_tokens > 0:
        events.append({
            "event_id":         str(uuid.uuid4()),
            "tenant_id":        tenant_id,
            "session_id":       session_id,
            "dimension":        "llm_tokens_input",
            "quantity":         input_tokens,
            "timestamp":        timestamp,
            "source_component": "ai-gateway",
            "metadata":         metadata,
        })
    if output_tokens > 0:
        events.append({
            "event_id":         str(uuid.uuid4()),
            "tenant_id":        tenant_id,
            "session_id":       session_id,
            "dimension":        "llm_tokens_output",
            "quantity":         output_tokens,
            "timestamp":        timestamp,
            "source_component": "ai-gateway",
            "metadata":         metadata,
        })

    for event in events:
        try:
            value = json.dumps(event).encode("utf-8")
            await producer.send("usage.events", value=value)
        except Exception as exc:
            # Metering nunca bloqueia operação — falha silenciosa com log
            logger.warning(
                "Failed to emit usage event dimension=%s tenant=%s: %s",
                event["dimension"], tenant_id, exc,
            )
