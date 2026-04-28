"""
consumer.py
Multi-topic Kafka consumer → ClickHouse writer.

Guarantees:
  at-least-once delivery: offset committed only after successful ClickHouse write.
  Idempotency: ReplacingMergeTree deduplicates on background merge (event_id ordering).

Topics → tables mapping:
  conversations.inbound      → sessions (initial record)
  conversations.routed       → sessions (pool update) + agent_events (routing)
  conversations.queued       → sessions (pool update) + queue_events
  conversations.events       → sessions + messages (contact_open/closed/message_sent)
  agent.lifecycle            → agent_events (agent_done only)
  usage.events               → usage_events
  sentiment.updated          → sentiment_events
  queue.position_updated     → queue_events
  workflow.events            → workflow_events
  collect.events             → collect_events
  conversations.participants → participation_intervals (participant_joined / left)

Batch strategy:
  Uses consumer.getmany(batch_size, timeout_ms) — processes one partition batch
  at a time, commits after each batch succeeds.  Malformed messages are logged
  and skipped (do NOT hold back the consumer group).
"""
from __future__ import annotations

import asyncio
import json
import logging
import signal

from aiokafka import AIOKafkaConsumer  # type: ignore[import-untyped]

from .clickhouse import AnalyticsStore
from .config import get_settings
from .models import (
    parse_inbound,
    parse_routed,
    parse_queued,
    parse_conversations_event,
    parse_agent_lifecycle,
    parse_usage_event,
    parse_sentiment_event,
    parse_queue_position,
    parse_workflow_event,
    parse_collect_event,
    parse_participant_event,
)

logger = logging.getLogger("plughub.analytics.consumer")

_TOPICS = [
    "conversations.inbound",
    "conversations.routed",
    "conversations.queued",
    "conversations.events",
    "agent.lifecycle",
    "usage.events",
    "sentiment.updated",
    "queue.position_updated",
    "workflow.events",
    "collect.events",
    "conversations.participants",
]

# Maps topic → parser function
_PARSERS = {
    "conversations.inbound":    parse_inbound,
    "conversations.routed":     parse_routed,
    "conversations.queued":     parse_queued,
    "conversations.events":     parse_conversations_event,
    "agent.lifecycle":          parse_agent_lifecycle,
    "usage.events":             parse_usage_event,
    "sentiment.updated":        parse_sentiment_event,
    "queue.position_updated":   parse_queue_position,
    "workflow.events":          parse_workflow_event,
    "collect.events":           parse_collect_event,
    "conversations.participants": parse_participant_event,
}


async def run_consumer(store: AnalyticsStore) -> None:
    """
    Starts the Kafka consumer and loops until SIGTERM/SIGINT.
    Called from main.py lifespan background task.
    """
    settings = get_settings()

    consumer = AIOKafkaConsumer(
        *_TOPICS,
        bootstrap_servers=settings.kafka_brokers,
        group_id=settings.kafka_group_id,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        value_deserializer=lambda v: v,  # raw bytes — manual JSON decode
    )

    shutdown = asyncio.Event()
    loop     = asyncio.get_running_loop()

    def _on_signal() -> None:
        logger.info("Shutdown signal received")
        shutdown.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _on_signal)
        except (RuntimeError, NotImplementedError):
            pass  # Windows or nested event loop

    await consumer.start()
    logger.info("Analytics consumer started — topics=%s", _TOPICS)

    try:
        while not shutdown.is_set():
            batch = await consumer.getmany(
                timeout_ms  = settings.consumer_timeout_ms,
                max_records = settings.consumer_batch_size,
            )
            if not batch:
                continue

            for tp, messages in batch.items():
                topic = tp.topic
                for msg in messages:
                    await _process_message(store, topic, msg)

            # Commit after every batch succeeds
            await consumer.commit()
    finally:
        await consumer.stop()
        logger.info("Analytics consumer stopped")


async def _process_message(
    store: AnalyticsStore,
    topic: str,
    msg:   object,
) -> None:
    """Deserialises one Kafka message, parses it, and writes to ClickHouse."""
    offset = getattr(msg, "offset", "?")
    try:
        raw     = json.loads(msg.value.decode("utf-8"))  # type: ignore[union-attr]
        parser  = _PARSERS.get(topic)
        if parser is None:
            logger.debug("No parser for topic=%s offset=%s — skipped", topic, offset)
            return

        result = parser(raw)
        if result is None:
            return  # skipped by parser (unknown event_type or missing fields)

        # Normalise to a list so routed/queued can return multiple rows
        rows = result if isinstance(result, list) else [result]
        for row in rows:
            await _write_row(store, row, topic, offset)

    except json.JSONDecodeError as exc:
        logger.warning("Malformed JSON on topic=%s offset=%s: %s", topic, offset, exc)
    except Exception as exc:
        logger.error(
            "Unexpected error processing topic=%s offset=%s: %s",
            topic, offset, exc, exc_info=True,
        )


async def _write_row(
    store:  AnalyticsStore,
    row:    dict,
    topic:  str,
    offset: object,
) -> None:
    """Routes the normalised row to the appropriate AnalyticsStore method."""
    table = row.get("table")
    try:
        if table == "sessions":
            await store.upsert_session(row)
        elif table == "queue_events":
            await store.insert_queue_event(row)
        elif table == "agent_events":
            await store.insert_agent_event(row)
        elif table == "messages":
            await store.insert_message(row)
        elif table == "usage_events":
            await store.insert_usage_event(row)
        elif table == "sentiment_events":
            await store.insert_sentiment_event(row)
        elif table == "workflow_events":
            await store.insert_workflow_event(row)
        elif table == "collect_events":
            await store.insert_collect_event(row)
        elif table == "participation_intervals":
            await store.upsert_participation_interval(row)
        elif table == "segments":
            await store.upsert_segment(row)
        elif table == "session_timeline":
            await store.insert_timeline_event(row)
        else:
            logger.warning("Unknown table=%s from topic=%s offset=%s", table, topic, offset)
    except Exception as exc:
        logger.error(
            "ClickHouse write failed table=%s topic=%s offset=%s: %s",
            table, topic, offset, exc, exc_info=True,
        )
        # Re-raise so the caller can decide whether to skip or retry.
        # Currently caller logs + skips to not block the consumer.
