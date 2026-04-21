"""
usage_emitter.py
Channel Gateway — emissão de eventos de consumo (usage.events).

Princípio: metering ≠ pricing.
Publica apenas o fato do consumo — sem preço, sem plano, sem quota.
O módulo de pricing (a construir) lê estes dados e decide o que cobrar.

Tópico Kafka: usage.events

Dimensões publicadas por este módulo:

  whatsapp_conversations — 1 por conversa WhatsApp aberta
  voice_minutes          — minutos de chamada (ceil de duration_seconds/60)
  sms_segments           — 1 por segmento SMS entregue (inbound ou outbound)
  email_messages         — 1 por mensagem de e-mail enviada ou recebida
  webchat_attachments    — 1 por arquivo commitado com sucesso via upload flow

Uso por adapter (futuros):
  whatsapp.py → emit_whatsapp_conversation() on contact_open
  webrtc.py   → emit_voice_minutes() on call_end (duration_seconds = end - start)
  sms.py      → emit_sms_segment() on inbound or outbound segment
  email.py    → emit_email_message() on inbound or outbound message

Uso atual (webchat):
  upload_router.py → emit_attachment() on commit success

Fire-and-forget: erros são logados mas nunca bloqueiam o caminho operacional.
"""
from __future__ import annotations

import json
import logging
import math
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger("plughub.channel-gateway.usage")

_TOPIC = "usage.events"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _publish(producer: Any, event: dict) -> None:
    """Fire-and-forget publish. Never raises."""
    try:
        value = json.dumps(event).encode("utf-8")
        await producer.send(_TOPIC, value=value)
    except Exception as exc:
        logger.warning(
            "Failed to emit usage event dimension=%s tenant=%s: %s",
            event.get("dimension"), event.get("tenant_id"), exc,
        )


# ── WhatsApp ──────────────────────────────────────────────────────────────────

async def emit_whatsapp_conversation(
    producer:   Any,
    tenant_id:  str,
    session_id: str,
    contact_id: str,
) -> None:
    """
    Emite 1 unidade de whatsapp_conversations na abertura de um contato.
    Chamado pelo adapter WhatsApp em contact_open.

    A cobrança por conversa é o modelo de pricing do WhatsApp Business API:
    cada janela de 24h (ou conversa iniciada pela empresa) é faturada como
    uma unidade.  O módulo de pricing decide a tarifa exata.
    """
    await _publish(producer, {
        "event_id":         str(uuid.uuid4()),
        "tenant_id":        tenant_id,
        "session_id":       session_id,
        "dimension":        "whatsapp_conversations",
        "quantity":         1,
        "timestamp":        _now_iso(),
        "source_component": "channel-gateway",
        "metadata": {
            "contact_id": contact_id,
            "channel":    "whatsapp",
        },
    })


# ── Voice / WebRTC ────────────────────────────────────────────────────────────

async def emit_voice_minutes(
    producer:         Any,
    tenant_id:        str,
    session_id:       str,
    contact_id:       str,
    duration_seconds: float,
) -> None:
    """
    Emite o tempo de chamada em minutos (arredondado para cima) no encerramento.
    Chamado pelo adapter WebRTC / Voice em contact_close, passando
    duration_seconds = ended_at − started_at.

    quantity = ceil(duration_seconds / 60) — mínimo 1 minuto.
    """
    minutes = max(1, math.ceil(duration_seconds / 60))
    await _publish(producer, {
        "event_id":         str(uuid.uuid4()),
        "tenant_id":        tenant_id,
        "session_id":       session_id,
        "dimension":        "voice_minutes",
        "quantity":         minutes,
        "timestamp":        _now_iso(),
        "source_component": "channel-gateway",
        "metadata": {
            "contact_id":       contact_id,
            "channel":          "webrtc",
            "duration_seconds": round(duration_seconds, 3),
        },
    })


# ── SMS ───────────────────────────────────────────────────────────────────────

async def emit_sms_segment(
    producer:   Any,
    tenant_id:  str,
    session_id: str,
    contact_id: str,
    direction:  str,  # "inbound" | "outbound"
) -> None:
    """
    Emite 1 unidade de sms_segments por segmento entregue.
    Chamado pelo adapter SMS a cada segmento recebido ou enviado.

    Segmentos SMS: até 160 caracteres (GSM-7) ou 153 em mensagens multi-parte.
    O adapter SMS deve contar os segmentos antes de chamar este método.
    """
    await _publish(producer, {
        "event_id":         str(uuid.uuid4()),
        "tenant_id":        tenant_id,
        "session_id":       session_id,
        "dimension":        "sms_segments",
        "quantity":         1,
        "timestamp":        _now_iso(),
        "source_component": "channel-gateway",
        "metadata": {
            "contact_id": contact_id,
            "channel":    "sms",
            "direction":  direction,
        },
    })


# ── Email ─────────────────────────────────────────────────────────────────────

async def emit_email_message(
    producer:   Any,
    tenant_id:  str,
    session_id: str,
    contact_id: str,
    direction:  str,  # "inbound" | "outbound"
) -> None:
    """
    Emite 1 unidade de email_messages por mensagem entregue.
    Chamado pelo adapter Email a cada e-mail recebido ou enviado.
    """
    await _publish(producer, {
        "event_id":         str(uuid.uuid4()),
        "tenant_id":        tenant_id,
        "session_id":       session_id,
        "dimension":        "email_messages",
        "quantity":         1,
        "timestamp":        _now_iso(),
        "source_component": "channel-gateway",
        "metadata": {
            "contact_id": contact_id,
            "channel":    "email",
            "direction":  direction,
        },
    })


# ── WebChat — Attachments ─────────────────────────────────────────────────────

async def emit_attachment(
    producer:   Any,
    tenant_id:  str,
    session_id: str,
    file_id:    str,
    mime_type:  str,
    size_bytes: int,
) -> None:
    """
    Emite 1 unidade de webchat_attachments por arquivo commitado com sucesso.
    Chamado por upload_router.py após store.commit() no fluxo de upload webchat.

    size_bytes é incluído no metadata para que o módulo de pricing possa
    aplicar tarifação por volume de storage se necessário.
    """
    await _publish(producer, {
        "event_id":         str(uuid.uuid4()),
        "tenant_id":        tenant_id,
        "session_id":       session_id,
        "dimension":        "webchat_attachments",
        "quantity":         1,
        "timestamp":        _now_iso(),
        "source_component": "channel-gateway",
        "metadata": {
            "file_id":    file_id,
            "mime_type":  mime_type,
            "size_bytes": size_bytes,
            "channel":    "webchat",
        },
    })
