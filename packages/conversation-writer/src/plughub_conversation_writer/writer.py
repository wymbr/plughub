"""
writer.py
Main Conversation Writer logic.
Multi-topic Kafka consumer that:
  - Accumulates messages from conversations.inbound / conversations.outbound in Redis
  - Tracks contact lifecycle metadata from conversations.events
  - On contact_closed: persists transcript to PostgreSQL and publishes transcript.created
Spec: conversation-writer.md — Fluxo de processamento section
"""

from __future__ import annotations
import asyncio
import json
import logging

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer

from .config import Settings
from .models import ContactClosedEvent, InboundMessage, TranscriptCreatedEvent
from .postgres_writer import PostgresWriter
from .redis_buffer import RedisBuffer

logger = logging.getLogger("plughub.conversation-writer")


class ConversationWriter:
    def __init__(
        self,
        buffer: RedisBuffer,
        db: PostgresWriter,
        producer: AIOKafkaProducer,
        settings: Settings,
    ) -> None:
        self._buffer   = buffer
        self._db       = db
        self._producer = producer
        self._settings = settings

    async def run(self) -> None:
        consumer = AIOKafkaConsumer(
            self._settings.kafka_topic_inbound,
            self._settings.kafka_topic_outbound,
            self._settings.kafka_topic_events,
            bootstrap_servers=self._settings.kafka_brokers,
            group_id=self._settings.kafka_group_id,
            auto_offset_reset="earliest",
        )
        await consumer.start()
        logger.info(
            "Conversation Writer started — topics: %s, %s, %s",
            self._settings.kafka_topic_inbound,
            self._settings.kafka_topic_outbound,
            self._settings.kafka_topic_events,
        )
        try:
            async for msg in consumer:
                topic   = msg.topic
                payload = json.loads(msg.value.decode())
                asyncio.create_task(self._dispatch(topic, payload))
        finally:
            await consumer.stop()

    # ── Dispatch ──────────────────────────────────────────────────────────────

    async def _dispatch(self, topic: str, payload: dict) -> None:
        try:
            if topic in (
                self._settings.kafka_topic_inbound,
                self._settings.kafka_topic_outbound,
            ):
                await self._handle_message(payload)

            elif topic == self._settings.kafka_topic_events:
                event_type = payload.get("event_type")
                if event_type == "contact_closed":
                    await self._handle_contact_closed(payload)
                elif event_type == "contact_open":
                    await self._handle_contact_open(payload)
                elif event_type == "agent_done":
                    await self._handle_agent_done(payload)
                # other event types silently ignored

        except Exception as exc:
            logger.error("dispatch error topic=%s: %s", topic, exc, exc_info=True)

    # ── Handlers ──────────────────────────────────────────────────────────────

    async def _handle_message(self, payload: dict) -> None:
        """Accumulate normalized message in Redis buffer."""
        try:
            msg = InboundMessage.model_validate(payload)
            await self._buffer.append_message(msg)
            logger.debug(
                "buffered message_id=%s contact_id=%s direction=%s",
                msg.message_id, msg.contact_id, msg.direction,
            )
        except Exception as exc:
            logger.warning("invalid message payload: %s — %s", payload.get("message_id"), exc)

    async def _handle_contact_open(self, payload: dict) -> None:
        """Store initial contact metadata when WebSocket connection opens."""
        contact_id = payload.get("contact_id")
        if not contact_id:
            return
        await self._buffer.upsert_meta(
            contact_id,
            session_id=payload.get("session_id"),
            started_at=payload.get("started_at"),
        )
        logger.debug("contact_open stored meta contact_id=%s", contact_id)

    async def _handle_agent_done(self, payload: dict) -> None:
        """
        Capture agent assignment metadata from agent_done event.
        Published by mcp-server-plughub when an agent finishes their turn.
        Enriches the contact metadata with pool, agent, outcome.
        """
        contact_id = payload.get("contact_id")
        if not contact_id:
            return
        await self._buffer.upsert_meta(
            contact_id,
            pool_id=payload.get("pool_id"),
            agent_id=payload.get("agent_id"),
            agent_type=payload.get("agent_type"),
            outcome=payload.get("outcome"),
        )
        logger.debug("agent_done meta updated contact_id=%s", contact_id)

    async def _handle_contact_closed(self, payload: dict) -> None:
        """
        On contact_closed:
          1. Validate event
          2. Read accumulated messages from Redis
          3. Read contact metadata from Redis
          4. Persist transcript to PostgreSQL
          5. Publish transcript.created to evaluation.events
          6. Cleanup Redis keys
        """
        try:
            event = ContactClosedEvent.model_validate(payload)
        except Exception as exc:
            logger.warning("invalid contact_closed payload: %s", exc)
            return

        contact_id = event.contact_id

        # Enrich meta with close-time info
        await self._buffer.upsert_meta(
            contact_id,
            ended_at=event.ended_at,
            reason=event.reason,
        )

        messages = await self._buffer.get_messages(contact_id)
        meta     = await self._buffer.get_meta(contact_id)

        # Use event fields as fallback for started_at/ended_at
        if not meta.started_at:
            meta.started_at = event.started_at
        if not meta.ended_at:
            meta.ended_at = event.ended_at

        if not messages:
            logger.warning(
                "contact_closed with no buffered messages contact_id=%s — writing empty transcript",
                contact_id,
            )

        # Persist
        try:
            transcript_id = await self._db.persist_transcript(
                meta=meta,
                messages=messages,
                ended_at=event.ended_at,
            )
        except Exception as exc:
            logger.error(
                "Failed to persist transcript contact_id=%s: %s",
                contact_id, exc, exc_info=True,
            )
            return  # Do not publish or clean up on DB failure

        # Publish transcript.created
        tc_event = TranscriptCreatedEvent(
            transcript_id=transcript_id,
            contact_id=contact_id,
            agent_id=meta.agent_id,
            agent_type=meta.agent_type,
            pool_id=meta.pool_id,
            outcome=meta.outcome,
            turn_count=len(messages),
            started_at=meta.started_at or event.started_at,
            ended_at=meta.ended_at or event.ended_at,
        )
        await self._producer.send(
            self._settings.kafka_topic_eval_events,
            value=tc_event.model_dump_json().encode(),
        )
        logger.info(
            "transcript.created published transcript_id=%s contact_id=%s turns=%d",
            transcript_id, contact_id, len(messages),
        )

        # Clean up Redis buffer
        await self._buffer.cleanup(contact_id)
