"""
models.py
Pydantic models para o Usage Aggregator.
Espelham os schemas TypeScript de @plughub/schemas/usage.ts.
"""
from __future__ import annotations
from typing import Any
from pydantic import BaseModel, Field


VALID_DIMENSIONS = {
    # Plataforma
    "sessions",
    "messages",
    # IA
    "llm_tokens_input",
    "llm_tokens_output",
    # Canais
    "whatsapp_conversations",
    "voice_minutes",
    "sms_segments",
    "email_messages",
    # Infra (reservado)
    "storage_gb",
    "data_transfer_gb",
    "compute_ms",
}


class UsageEvent(BaseModel):
    """Evento de consumo publicado em usage.events."""
    event_id:         str
    tenant_id:        str
    session_id:       str | None = None
    dimension:        str
    quantity:         float
    timestamp:        str
    source_component: str
    metadata:         dict[str, Any] = Field(default_factory=dict)
