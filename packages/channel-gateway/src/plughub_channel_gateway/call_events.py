"""
call_events.py — o intervalo de CHAMADA dentro de um contato vai ao Kafka (WCH-02, relatórios).

Tópico `media.calls` (schema em `@plughub/schemas/media-calls.ts`), chave = `session_id`: início e
fim da mesma chamada saem em ordem. Até aqui o fato vivia só no stream da sessão (`media.call`) e
na cópia durável dele no PostgreSQL, que o analytics não lê — nenhum relatório via chamada.

`call_id` = id da entrada `media.call started` no stream: o discriminador da CHAMADA, não da
sessão — um contato de chat pode ter várias.

Fire-and-forget: falha de publicação é dita no log e nunca afeta a chamada.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any

logger = logging.getLogger("plughub.channel-gateway.call_events")

TOPIC = "media.calls"


def pool_from_sources(policy_sources: list[str]) -> str | None:
    """O pool de quem ATENDE, da procedência da política (`pool:<id>`). Vários atendentes de pools
    diferentes: o primeiro em ordem — a lista toda está no estado de mídia, não no relatório."""
    for src in policy_sources:
        if src.startswith("pool:") and len(src) > 5:
            return src[5:]
    return None


def started(*, tenant_id: str, session_id: str, call_id: str, channel: str, pool_id: str | None,
            customer_publish: list[str], started_at: str) -> dict:
    return {
        "event_id": str(uuid.uuid4()), "event_type": "call_started",
        "tenant_id": tenant_id, "session_id": session_id, "call_id": call_id, "channel": channel,
        "pool_id": pool_id, "customer_publish": list(customer_publish), "started_at": started_at,
    }


def ended(*, begun: dict, ended_at: str, end_reason: str) -> dict:
    """O fim leva os campos do início (o consumidor grava a LINHA INTEIRA — `ReplacingMergeTree`
    substitui a linha, não faz merge por coluna) mais a duração e o motivo."""
    t0 = datetime.fromisoformat(begun["started_at"])
    t1 = datetime.fromisoformat(ended_at)
    return {
        **{k: begun[k] for k in ("tenant_id", "session_id", "call_id", "channel", "pool_id",
                                 "customer_publish", "started_at")},
        "event_id": str(uuid.uuid4()), "event_type": "call_ended", "ended_at": ended_at,
        "duration_ms": max(0, int((t1 - t0).total_seconds() * 1000)),
        "end_reason": end_reason or "unknown",
    }


async def publish(producer: Any, event: dict) -> bool:
    """Nunca levanta; `False` = não publicado, e o log diz."""
    if producer is None:
        logger.warning("call_events: sem produtor Kafka — %s da chamada %s (sessao %s) NAO publicado",
                       event.get("event_type"), event.get("call_id"), event.get("session_id"))
        return False
    try:
        await producer.send(TOPIC, key=event["session_id"].encode("utf-8"),
                            value=json.dumps(event).encode("utf-8"))
        return True
    except Exception as exc:  # noqa: BLE001 — relatório não derruba chamada, mas não some calado
        logger.warning("call_events: %s da chamada %s (sessao %s) NAO publicado: %s",
                       event.get("event_type"), event.get("call_id"), event.get("session_id"), exc)
        return False
