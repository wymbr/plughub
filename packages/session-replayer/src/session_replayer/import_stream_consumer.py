"""
import_stream_consumer.py
Consumer Y (R13b) — reconstrói o stream durável `session_stream_events` (PostgreSQL)
para contatos IMPORTADOS, a partir dos eventos canônicos internos, sem depender do
stream Redis (que não existe p/ importados).

Por quê: o quality-ingest (R13a-2) emite eventos canônicos (conversations.events /
conversations.participants) que populam analytics + disparam sampling. Mas o
ReplayContext é montado do stream Redis (Persister/Hydrator) — vazio p/ importados.
Este consumer fecha essa lacuna: lê os MESMOS eventos canônicos (gated
`source=external_import`), mapeia-os para linhas de stream e as grava via o
`StreamPersister.insert_records` (mesmo escritor do Persister vivo → sem drift).
Depois o Hydrator → Replayer produzem um ReplayContext idêntico ao interno.

Decisões:
  - Gate `source=external_import`: tráfego vivo é ignorado (já tem o Persister vivo).
  - `original_content=null` sempre (importado é cego por construção / LGPD).
  - `event_type` no vocabulário do stream interno (`message`/`session_opened`/
    `session_closed`/`participant_joined`/`participant_left`).
  - `author.role` do transcript: `customer` preservado; `agent`→`primary`,
    `system`→`system` (granularidade por-segmento fica em analytics.segments; o rótulo
    de autor da mensagem é grosso de propósito p/ importação grau-transcript).
  - Ordem-independente: cada evento é inserido idempotente (event_id estável); o
    `delta_ms` é finalizado por `recompute_deltas` no fechamento e recomputado pelo
    Replayer na leitura.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from aiokafka import AIOKafkaConsumer

from .stream_persister import StreamPersister

logger = logging.getLogger(__name__)

IMPORT_SOURCE_MARKER = "external_import"
REEVAL_SOURCE_MARKER = "internal:reeval"
# Fontes cujo stream NÃO existe no Redis vivo e precisa ser reconstruído no PG a
# partir dos eventos canônicos: importação externa (CCaaS) e reavaliação interna
# (quality-export). Ambas alimentam o ReplayContext do avaliador. A origem (import
# vs reeval) é derivada do source por _origin_from_source e carimbada na linha.
_REBUILD_SOURCES = frozenset({IMPORT_SOURCE_MARKER, REEVAL_SOURCE_MARKER})


def _origin_from_source(source: Any) -> str:
    """Quality substrate isolation (ADR adr-quality-substrate-isolation) — deriva a
    procedência por-sessão do `source` do evento canônico:
      external_import → import | internal:reeval → reeval | demais/ausente → live.
    Espelha a derivação da analytics-api (models.origin_from_source) no caminho do
    stream durável. Hoje o gate de consumo admite só `external_import` (→ import);
    a derivação fica pronta para quando a porta de reavaliação for admitida."""
    s = source.strip() if isinstance(source, str) else ""
    if s == "external_import":
        return "import"
    if s == "internal:reeval":
        return "reeval"
    return "live"

# Topics carrying the canonical events the mapper understands.
TOPIC_EVENTS = "conversations.events"
TOPIC_PARTICIPANTS = "conversations.participants"

# Author role mapping: external transcript vocabulary → stream author role.
_ROLE_MAP = {"customer": "customer", "agent": "primary", "system": "system"}


def _parse_ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


def canonical_event_to_record(topic: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    """Map ONE canonical event → a session_stream_events record, or None.

    Returns None when the event is not import-sourced, not on a mapped topic, or not a
    mapped event type. The record shape matches what StreamPersister.insert_records and
    the Hydrator/Replayer read path expect.
    """
    if payload.get("source") not in _REBUILD_SOURCES:
        return None

    session_id = payload.get("session_id")
    if not session_id:
        return None

    if topic == TOPIC_EVENTS:
        et = payload.get("event_type") or payload.get("type")

        if et == "contact_open":
            return {
                "event_id":   f"{session_id}:session_opened",
                "event_type": "session_opened",
                "timestamp":  _parse_ts(payload.get("started_at") or payload.get("timestamp")),
                "author":     None,
                "visibility": "all",
                "payload":    {"channel": payload.get("channel", "")},
                "original_content": None,
                "masked_categories": [],
                "delta_ms":   0.0,
            }

        if et == "message_sent":
            author_role = payload.get("author_role") or payload.get("role") or "agent"
            stream_role = _ROLE_MAP.get(author_role, "primary")
            participant_id = payload.get("author_id") or ("customer" if author_role == "customer" else "")
            return {
                "event_id":   payload.get("message_id") or f"{session_id}:msg:{payload.get('timestamp')}",
                "event_type": "message",
                "timestamp":  _parse_ts(payload.get("timestamp")),
                "author":     {"role": stream_role, "participant_id": participant_id},
                "visibility": payload.get("visibility") or "all",
                "payload": {
                    "content": {
                        "type": payload.get("content_type") or "text",
                        "text": payload.get("content", ""),
                    },
                    "masked": True,
                    "masked_categories": payload.get("masked_categories") or [],
                },
                "original_content": None,   # importado é cego por construção
                "masked_categories": payload.get("masked_categories") or [],
                "delta_ms":   0.0,
            }

        if et == "contact_closed":
            return {
                "event_id":   f"{session_id}:session_closed",
                "event_type": "session_closed",
                "timestamp":  _parse_ts(payload.get("ended_at") or payload.get("timestamp")),
                "author":     None,
                "visibility": "all",
                "payload": {
                    "outcome":      payload.get("outcome"),
                    "close_reason": payload.get("close_reason"),
                },
                "original_content": None,
                "masked_categories": [],
                "delta_ms":   0.0,
            }
        return None

    if topic == TOPIC_PARTICIPANTS:
        t = payload.get("type") or payload.get("event_type")
        if t not in ("participant_joined", "participant_left"):
            return None
        event_id = payload.get("event_id") or f"{session_id}:{t}:{payload.get('segment_id') or payload.get('participant_id')}"
        author = {
            "role":           payload.get("role") or "primary",
            "participant_id": payload.get("participant_id") or "",
        }
        if t == "participant_joined":
            body = {
                "role":       payload.get("role"),
                "pool_id":    payload.get("pool_id"),
                "agent_type": payload.get("agent_type"),
            }
        else:
            body = {
                "participant_id": payload.get("participant_id"),
                "reason":         payload.get("outcome"),
            }
        return {
            "event_id":   event_id,
            "event_type": t,
            "timestamp":  _parse_ts(payload.get("timestamp") or payload.get("joined_at")),
            "author":     author,
            "visibility": "all",
            "payload":    body,
            "original_content": None,
            "masked_categories": [],
            "delta_ms":   0.0,
        }

    return None


class ImportStreamConsumer:
    """Consumes the canonical events for imported contacts and rebuilds
    session_stream_events. A pure PG writer (no Redis); idempotent."""

    def __init__(
        self,
        kafka_brokers: str,
        persister:     StreamPersister,
        *,
        group_id: str = "session-replayer-import",
    ) -> None:
        self._brokers   = kafka_brokers
        self._persister = persister
        self._group_id  = group_id
        self._consumer: AIOKafkaConsumer | None = None

    async def run(self) -> None:
        self._consumer = AIOKafkaConsumer(
            TOPIC_EVENTS,
            TOPIC_PARTICIPANTS,
            bootstrap_servers=self._brokers,
            group_id=self._group_id,
            auto_offset_reset="earliest",
            enable_auto_commit=True,
            value_deserializer=lambda v: json.loads(v.decode()),
        )
        await self._consumer.start()
        logger.info(
            "ImportStreamConsumer (Y) started — topics=[%s, %s] gated sources=%s",
            TOPIC_EVENTS, TOPIC_PARTICIPANTS, sorted(_REBUILD_SOURCES),
        )
        try:
            async for msg in self._consumer:
                try:
                    await self._handle(msg.topic, msg.value)
                except Exception as exc:  # noqa: BLE001 — never let one event kill the loop
                    logger.warning("ImportStreamConsumer: handler error: %s", exc)
        finally:
            await self._consumer.stop()

    async def stop(self) -> None:
        if self._consumer is not None:
            await self._consumer.stop()

    async def _handle(self, topic: str, payload: dict) -> None:
        rec = canonical_event_to_record(topic, payload)
        if rec is None:
            return
        session_id = payload.get("session_id")
        tenant_id  = payload.get("tenant_id")
        if not session_id or not tenant_id:
            return
        # Substrate isolation (ADR): carimba a procedência derivada do source. O gate
        # acima admite só external_import → 'import'; mantém-se a derivação genérica.
        rec["origin"] = _origin_from_source(payload.get("source"))
        await self._persister.insert_records(session_id, tenant_id, [rec])
        # Finalize delta_ms when the closing event arrives (best-effort; Replayer also
        # recomputes on read, so late/out-of-order messages stay correct in the context).
        if rec["event_type"] == "session_closed":
            await self._persister.recompute_deltas(session_id, tenant_id)
            logger.info(
                "ImportStreamConsumer: stream reconstructed for imported session %s", session_id
            )
