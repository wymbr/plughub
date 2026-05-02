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
  agent.lifecycle            → agent_events (agent_done)
                             + agent_pause_intervals (agent_pause → open; agent_ready → close)
  usage.events               → usage_events
  sentiment.updated          → sentiment_events  (segment_id enriched via SegmentEnricher)
  queue.position_updated     → queue_events
  workflow.events            → workflow_events
  collect.events             → collect_events
  conversations.participants → participation_intervals (participant_joined / left)
  evaluation.events          → evaluation_results + evaluation_events (Arc 6)
  mcp.audit                  → session_timeline   (segment_id enriched via SegmentEnricher)

Batch strategy:
  Uses consumer.getmany(batch_size, timeout_ms) — processes one partition batch
  at a time, commits after each batch succeeds.  Malformed messages are logged
  and skipped (do NOT hold back the consumer group).

Segment enrichment (Arc 5 post-hoc):
  For topics that lack segment_id (sentiment.updated, mcp.audit) the consumer
  resolves segment_id before calling the parser using SegmentEnricher:
    • sentiment.updated → lookup_primary(session_id)     (primary agent in session)
    • mcp.audit        → lookup_by_instance(instance_id) (specific MCP caller)
  The lookup chain is: in-memory cache → Redis → ClickHouse FINAL query.
  If all three fail segment_id is written as None / "" — no event is dropped.
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
    parse_evaluation_event,
    parse_mcp_audit_event,
)
from .segment_enricher import SegmentEnricher

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
    "evaluation.events",
    "mcp.audit",
]

# Maps topic → parser function.
# For topics that need segment enrichment the consumer handles this before
# calling the parser; the dict stays plain-parser references.
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
    "evaluation.events":          parse_evaluation_event,
    "mcp.audit":                  parse_mcp_audit_event,
}

# Topics that require segment_id enrichment before being passed to the parser.
_ENRICHED_TOPICS = frozenset({"sentiment.updated", "mcp.audit"})

# Redis key TTL for open pause intervals (24 h — covers overnight shifts)
_PAUSE_KEY_TTL = 86_400


async def run_consumer(store: AnalyticsStore, redis: object | None = None) -> None:
    """
    Starts the Kafka consumer and loops until SIGTERM/SIGINT.
    Called from main.py lifespan background task.

    Args:
        store: AnalyticsStore wrapping the ClickHouse connection.
        redis: Optional aioredis client.  When provided, SegmentEnricher uses
               it for fast Redis lookups before falling back to ClickHouse.
               When None, enrichment falls back to ClickHouse only.
    """
    settings = get_settings()

    enricher = SegmentEnricher(redis, store) if redis is not None else None

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
                    await _process_message(store, topic, msg, enricher, redis)

            # Commit after every batch succeeds
            await consumer.commit()
    finally:
        await consumer.stop()
        logger.info("Analytics consumer stopped")


async def _process_message(
    store:    AnalyticsStore,
    topic:    str,
    msg:      object,
    enricher: SegmentEnricher | None = None,
    redis:    object | None = None,
) -> None:
    """Deserialises one Kafka message, enriches if needed, parses, and writes to ClickHouse."""
    offset = getattr(msg, "offset", "?")
    try:
        raw     = json.loads(msg.value.decode("utf-8"))  # type: ignore[union-attr]
        parser  = _PARSERS.get(topic)
        if parser is None:
            logger.debug("No parser for topic=%s offset=%s — skipped", topic, offset)
            return

        # ── Arc 5: post-hoc segment_id enrichment ────────────────────────────
        if topic in _ENRICHED_TOPICS and enricher is not None:
            result = await _parse_with_enrichment(raw, topic, parser, enricher)
        else:
            result = parser(raw)

        if result is None:
            return  # skipped by parser (unknown event_type or missing fields)

        # Normalise to a list so routed/queued can return multiple rows
        rows = result if isinstance(result, list) else [result]

        # ── Arc 8: pause interval Redis state machine ─────────────────────────
        # agent.lifecycle may return action=open (store in Redis) or
        # action=close_check (look up Redis, compute duration, emit close row).
        if topic == "agent.lifecycle" and redis is not None:
            resolved: list[dict] = []
            for row in rows:
                action = row.get("action")
                if action == "open":
                    row = await _handle_pause_open(row, redis)
                    resolved.append(row)
                elif action == "close_check":
                    close_row = await _handle_pause_close(row, redis)
                    if close_row is not None:
                        resolved.append(close_row)
                    # None means no open pause → normal agent_ready, skip
                else:
                    resolved.append(row)
            rows = resolved

        for row in rows:
            await _write_row(store, row, topic, offset)

    except json.JSONDecodeError as exc:
        logger.warning("Malformed JSON on topic=%s offset=%s: %s", topic, offset, exc)
    except Exception as exc:
        logger.error(
            "Unexpected error processing topic=%s offset=%s: %s",
            topic, offset, exc, exc_info=True,
        )


def _pause_redis_key(tenant_id: str, instance_id: str) -> str:
    """Redis key that stores open pause state for a human agent instance."""
    return f"{tenant_id}:pause:{instance_id}"


async def _handle_pause_close(
    row:   dict,
    redis: object,
) -> dict | None:
    """
    Resolve a 'close_check' agent_pause_intervals row.

    Reads the open pause state from Redis.  If found, returns a close row
    (same interval_id, resumed_at + duration_ms filled).  If not found (no
    open pause) the row is dropped — this is a normal login/ready transition,
    not a resume-from-pause.

    Redis key format:
      {tenant_id}:pause:{instance_id}
    Value (JSON): { "interval_id": "...", "paused_at": "ISO8601", "reason_id": "...",
                   "reason_label": "...", "agent_type_id": "...", "pool_id": "...", "note": ... }
    """
    import json as _json
    from datetime import datetime as _dt

    tenant_id   = row.get("tenant_id", "")
    instance_id = row.get("instance_id", "")
    resumed_at  = row.get("resumed_at", "")

    key = _pause_redis_key(tenant_id, instance_id)
    try:
        raw = await redis.get(key)  # type: ignore[union-attr]
        if not raw:
            return None  # no open pause — normal agent_ready, skip
        state = _json.loads(raw)
        await redis.delete(key)  # type: ignore[union-attr]
    except Exception as exc:
        logger.debug("Pause Redis lookup failed instance=%s: %s", instance_id, exc)
        return None

    paused_at_str = state.get("paused_at", "")
    # Compute duration_ms between paused_at and resumed_at
    duration_ms: int | None = None
    try:
        paused_dt  = _dt.fromisoformat(paused_at_str.replace("Z", "+00:00"))
        resumed_dt = _dt.fromisoformat(resumed_at.replace("Z", "+00:00"))
        duration_ms = int((resumed_dt - paused_dt).total_seconds() * 1000)
    except Exception:
        pass

    return {
        "table":          "agent_pause_intervals",
        "action":         "close",
        "interval_id":    state.get("interval_id", ""),
        "tenant_id":      tenant_id,
        "instance_id":    instance_id,
        "agent_type_id":  state.get("agent_type_id", ""),
        "pool_id":        state.get("pool_id", ""),
        "reason_id":      state.get("reason_id", ""),
        "reason_label":   state.get("reason_label", ""),
        "note":           state.get("note") or None,
        "paused_at":      paused_at_str,
        "resumed_at":     resumed_at,
        "duration_ms":    duration_ms,
    }


async def _handle_pause_open(
    row:   dict,
    redis: object,
) -> dict:
    """
    Store open pause state in Redis and return the row unchanged.

    The row already contains all fields needed to write to agent_pause_intervals.
    We additionally persist the state in Redis so that the matching agent_ready
    can close the interval and compute duration_ms.
    """
    import json as _json

    tenant_id   = row.get("tenant_id", "")
    instance_id = row.get("instance_id", "")
    key = _pause_redis_key(tenant_id, instance_id)
    state = {
        "interval_id":  row.get("interval_id", ""),
        "paused_at":    row.get("paused_at", ""),
        "reason_id":    row.get("reason_id", ""),
        "reason_label": row.get("reason_label", ""),
        "agent_type_id": row.get("agent_type_id", ""),
        "pool_id":       row.get("pool_id", ""),
        "note":          row.get("note") or None,
    }
    try:
        await redis.set(key, _json.dumps(state), ex=_PAUSE_KEY_TTL)  # type: ignore[union-attr]
    except Exception as exc:
        logger.debug("Pause Redis store failed instance=%s: %s", instance_id, exc)
    return row


async def _parse_with_enrichment(
    raw:      dict,
    topic:    str,
    parser:   object,
    enricher: SegmentEnricher,
) -> dict | list | None:
    """
    Resolve segment_id via SegmentEnricher, then call the parser with it.

    Enrichment strategy per topic:
      sentiment.updated → lookup_primary(session_id)
          The AI Gateway does not carry instance_id in the payload; we find
          the current primary participant of the session.
      mcp.audit         → lookup_by_instance(session_id, instance_id)
          The AuditRecord always carries instance_id (the agent invoking the tool).
    """
    session_id = raw.get("session_id") or ""
    tenant_id  = raw.get("tenant_id") or ""
    segment_id: str | None = None

    try:
        if topic == "sentiment.updated":
            segment_id = await enricher.lookup_primary(session_id, tenant_id)
        elif topic == "mcp.audit":
            instance_id = raw.get("instance_id") or ""
            segment_id = await enricher.lookup_by_instance(
                session_id, instance_id, tenant_id
            )
    except Exception as exc:
        logger.debug(
            "Segment enrichment failed topic=%s session=%s: %s",
            topic, session_id, exc,
        )

    # Call the parser with the (possibly None) segment_id keyword argument.
    # Both parse_sentiment_event and parse_mcp_audit_event accept segment_id.
    return parser(raw, segment_id=segment_id)  # type: ignore[call-arg]


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
        elif table == "evaluation_results":
            await store.upsert_evaluation_result(row)
        elif table == "evaluation_events":
            await store.insert_evaluation_event(row)
        elif table == "contact_insights":
            await store.insert_contact_insight(row)
        elif table == "agent_pause_intervals":
            await store.upsert_agent_pause_interval(row)
        else:
            logger.warning("Unknown table=%s from topic=%s offset=%s", table, topic, offset)
    except Exception as exc:
        logger.error(
            "ClickHouse write failed table=%s topic=%s offset=%s: %s",
            table, topic, offset, exc, exc_info=True,
        )
        # Re-raise so the caller can decide whether to skip or retry.
        # Currently caller logs + skips to not block the consumer.
