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
  agent.events               → agent_business_events (Arc 12)
  session.signals            → session_signal (F10 — survey; session_at enriched
                               from origin opened_at for deferred surveys, F11)
  calibration.events         → calibration_events (Arc 13)

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
import uuid
from datetime import datetime, timezone

from aiokafka import AIOKafkaConsumer, AIOKafkaProducer  # type: ignore[import-untyped]

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
    parse_agent_business_event,
    parse_session_signal_event,
    parse_calibration_event,
    parse_journey_merged,
    parse_pool_occupancy,
)
from .segment_enricher import SegmentEnricher
from .deployments_client import fetch_skill_version

logger = logging.getLogger("plughub.analytics.consumer")

# ── Channel cache ─────────────────────────────────────────────────────────────
# conversations.inbound carries channel ('webhook', 'webchat', etc.).
# conversations.routed does NOT carry channel (routing engine only knows pool/agent)
# so parse_routed writes channel='' which REPLACES the inbound row in
# ReplacingMergeTree, losing the channel.  The recovery subquery in
# reports_query.py compensates, but only before ClickHouse merges rows.
#
# Solution: cache session_id→channel when parse_inbound fires; inject it back
# into the parse_routed sessions row before writing to ClickHouse.
# Key: (tenant_id, session_id) → channel string.  FIFO eviction at _CHANNEL_CACHE_MAX.
_channel_cache: dict[tuple[str, str], str] = {}
_CHANNEL_CACHE_MAX = 50_000


def _cache_inbound_channel(payload: dict) -> None:
    """Store channel + origin_session_id for a session when conversations.inbound fires."""
    session_id        = payload.get("session_id")
    tenant_id         = payload.get("tenant_id")
    channel           = payload.get("channel", "")
    origin_session_id = payload.get("origin_session_id") or None
    if session_id and tenant_id and (channel or origin_session_id):
        key = (tenant_id, session_id)
        if len(_channel_cache) >= _CHANNEL_CACHE_MAX:
            _channel_cache.pop(next(iter(_channel_cache)))
        _channel_cache[key] = (channel, origin_session_id)


def _inject_cached_channel(rows: list[dict]) -> None:
    """For parse_routed rows: restore channel + origin_session_id from cache."""
    for row in rows:
        if row.get("table") == "sessions":
            key    = (row.get("tenant_id", ""), row.get("session_id", ""))
            cached = _channel_cache.get(key)
            if not cached:
                continue
            cached_channel, cached_origin = cached
            # Restore channel if parse_routed wrote ''
            if not row.get("channel", "") and cached_channel:
                row["channel"] = cached_channel
            # Restore origin_session_id if missing
            if not row.get("origin_session_id") and cached_origin:
                row["origin_session_id"] = cached_origin


async def _enrich_session_root(row: dict, redis: object) -> None:
    """
    Journey J1 — set a sessions row's root_session_id (+ journey_id cache) from the
    AUTHORITATIVE ContextStore value (session.root_session_id), when present.

    sessions is a ReplacingMergeTree (whole-row replace). Several events that write a
    sessions row — conversations.routed/queued, session_suspended, contact_closed via
    abandon paths — do NOT carry the propagated root, so the parser falls back to
    session_id (self) and clobbers a CHILD session's transitive root. channel-gateway
    seeds session.root_session_id in the ContextStore on trigger/delegate; read it here
    so EVERY sessions write preserves the correct root (single central point, covers all
    writers, uses the source of truth). Fail-soft: missing/broken entry leaves the
    parser's value (self) untouched — correct for genuine top-level roots.
    """
    tenant_id  = row.get("tenant_id")
    session_id = row.get("session_id")
    if not tenant_id or not session_id:
        return
    try:
        raw = await redis.hget(  # type: ignore[union-attr]
            f"{tenant_id}:ctx:{session_id}", "session.root_session_id"
        )
        if not raw:
            return
        entry = json.loads(raw if isinstance(raw, str) else raw.decode())
        value = entry.get("value") if isinstance(entry, dict) else entry
        value = str(value).strip() if value is not None else ""
        if value:
            row["root_session_id"] = value
            row["journey_id"]      = value
    except Exception as exc:
        logger.debug("root enrich failed session=%s: %s", session_id, exc)


# ── F11: session_at enrichment for session.signals ────────────────────────────
# parse_session_signal_event sets session_at = captured_at, which is correct only
# for same-day ("no ato") surveys.  For DEFERRED surveys (captured_at days after
# the original session) the golden rule (§7) requires bucketizing by the ORIGINAL
# session's date so the quali aligns with the quanti.  We resolve it from
# analytics.sessions.opened_at keyed by origin_session_id and overwrite session_at;
# date (partition) and TTL follow automatically in the row builder.
# Fallback (origin not in sessions yet / lookup error): keep captured_at — the row
# is never dropped.  Cache mirrors _channel_cache (bounded FIFO).
_session_opened_cache: dict[tuple[str, str], str] = {}
_SESSION_OPENED_CACHE_MAX = 50_000


async def _enrich_participant_deploy_version(rows: list[dict], raw: dict) -> None:
    """R9 — preenche segments.deploy_version a partir da versão corrente do skill
    (agent-registry) quando o evento trouxe flow_id mas não deploy_version. No-op
    quando o bridge já enviou deploy_version (precedência ao valor exato-no-início)."""
    base = get_settings().agent_registry_url
    if not base:
        return
    tenant_id = raw.get("tenant_id") or ""
    for row in rows:
        if row.get("table") != "segments":
            continue
        flow_id = row.get("flow_id") or ""
        if not flow_id or row.get("deploy_version"):
            continue
        version = await fetch_skill_version(base, tenant_id, flow_id)
        if version:
            row["deploy_version"] = version


async def _enrich_signal_session_at(rows: list[dict], store: "AnalyticsStore") -> None:
    """Overwrite session_at with the original session's opened_at when resolvable."""
    for row in rows:
        if row.get("table") != "session_signal":
            continue
        tenant_id = row.get("tenant_id", "")
        origin_id = row.get("origin_session_id") or row.get("session_id", "")
        if not tenant_id or not origin_id:
            continue
        key       = (tenant_id, origin_id)
        opened_at = _session_opened_cache.get(key)
        if opened_at is None:
            try:
                opened_at = await store.lookup_session_opened_at(tenant_id, origin_id)
            except Exception as exc:
                logger.debug(
                    "session_at enrichment lookup failed origin=%s: %s", origin_id, exc
                )
                opened_at = None
            if opened_at:
                if len(_session_opened_cache) >= _SESSION_OPENED_CACHE_MAX:
                    _session_opened_cache.pop(next(iter(_session_opened_cache)))
                _session_opened_cache[key] = opened_at
        if opened_at:
            row["session_at"] = opened_at


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
    "agent.events",
    "session.signals",
    "calibration.events",
    "journey.merges",
    "pool.occupancy",
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
    "agent.events":               parse_agent_business_event,
    "session.signals":            parse_session_signal_event,
    "calibration.events":         parse_calibration_event,
    "journey.merges":             parse_journey_merged,
    "pool.occupancy":             parse_pool_occupancy,
}

# Topics that require segment_id enrichment before being passed to the parser.
_ENRICHED_TOPICS = frozenset({"sentiment.updated", "mcp.audit"})

# Redis key TTL for open pause intervals (24 h — covers overnight shifts)
_PAUSE_KEY_TTL = 86_400

# Redis key TTL for open login intervals (Fase 1b). Refreshed on every
# agent_ready while the interval stays open, so a long shift never expires
# mid-session; a stale key (>24 h with no event) is treated as an orphan.
_LOGIN_KEY_TTL = 86_400

# ── Reliability ───────────────────────────────────────────────────────────────
MAX_ATTEMPTS    = 3
BACKOFF_BASE_MS = 500   # 500ms → 1 000ms between retries


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

    # ── DLQ producer ──────────────────────────────────────────────────────────
    dlq_producer = AIOKafkaProducer(
        bootstrap_servers=settings.kafka_brokers,
        value_serializer=lambda v: json.dumps(v).encode("utf-8"),
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
    await dlq_producer.start()
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
                    await _process_with_retry(
                        store, topic, msg, enricher, redis,
                        dlq_producer, settings.kafka_dlq_topic, settings.kafka_group_id,
                    )

            # Commit after every batch succeeds (DLQ-routed messages are also committed)
            await consumer.commit()
    finally:
        await consumer.stop()
        await dlq_producer.stop()
        logger.info("Analytics consumer stopped")


async def _process_with_retry(
    store:     AnalyticsStore,
    topic:     str,
    msg:       object,
    enricher:  SegmentEnricher | None,
    redis:     object | None,
    producer:  AIOKafkaProducer,
    dlq_topic: str,
    group_id:  str,
) -> None:
    """
    Wraps _process_message with retry + DLQ.

    Reliability contract:
      • JSON decode errors are logged and skipped immediately (no retry — bad data
        will always fail).
      • All other failures (ClickHouse write errors, enrichment errors, parse
        errors) are retried up to MAX_ATTEMPTS times with exponential backoff.
      • After MAX_ATTEMPTS exhausted, the raw message bytes are published to the
        DLQ topic so no event is permanently lost.
    """
    offset = getattr(msg, "offset", "?")
    last_error: BaseException | None = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            await _process_message(store, topic, msg, enricher, redis)
            return  # success
        except json.JSONDecodeError as exc:
            logger.warning("Malformed JSON topic=%s offset=%s: %s", topic, offset, exc)
            return  # no retry for structurally bad data
        except Exception as exc:
            last_error = exc
            if attempt < MAX_ATTEMPTS:
                delay_ms = BACKOFF_BASE_MS * (2 ** (attempt - 1))
                logger.warning(
                    "[retry %d/%d] topic=%s offset=%s error=%s delay=%dms",
                    attempt, MAX_ATTEMPTS, topic, offset,
                    exc, delay_ms,
                )
                await asyncio.sleep(delay_ms / 1000)

    # All retries exhausted — publish to DLQ
    err_str = str(last_error)
    logger.error(
        "[dlq] All %d attempts failed topic=%s offset=%s error=%s",
        MAX_ATTEMPTS, topic, offset, err_str,
    )
    await _publish_dlq_analytics(msg, topic, err_str, producer, dlq_topic, group_id)


async def _publish_dlq_analytics(
    msg:       object,
    topic:     str,
    error:     str,
    producer:  AIOKafkaProducer,
    dlq_topic: str,
    group_id:  str,
) -> None:
    raw_bytes: bytes = getattr(msg, "value", None) or b""
    payload = {
        "event_id":      str(uuid.uuid4()),
        "source_topic":  topic,
        "consumer_group": group_id,
        "service":       "analytics-api",
        "error":         error,
        "attempt_count": MAX_ATTEMPTS,
        "payload_raw":   raw_bytes.decode("utf-8", errors="replace"),
        "failed_at":     datetime.now(timezone.utc).isoformat(),
    }
    try:
        await producer.send_and_wait(dlq_topic, value=payload)
    except Exception as dlq_err:
        logger.error("[dlq] Failed to publish to DLQ: %s", dlq_err)


async def _process_message(
    store:    AnalyticsStore,
    topic:    str,
    msg:      object,
    enricher: SegmentEnricher | None = None,
    redis:    object | None = None,
) -> None:
    """Deserialises one Kafka message, enriches if needed, parses, and writes to ClickHouse.

    Raises on any error so _process_with_retry can decide whether to retry or DLQ.
    JSONDecodeError propagates as-is (caller treats it as a skip, not a retry).
    """
    offset = getattr(msg, "offset", "?")
    raw    = json.loads(msg.value.decode("utf-8"))  # type: ignore[union-attr]
    parser = _PARSERS.get(topic)
    if parser is None:
        logger.debug("No parser for topic=%s offset=%s — skipped", topic, offset)
        return

    # ── Arc 5: post-hoc segment_id enrichment ────────────────────────────
    if topic in _ENRICHED_TOPICS and enricher is not None:
        result = await _parse_with_enrichment(raw, topic, parser, enricher)
    else:
        result = parser(raw)

    # ── Fase 1b: login interval tracking (independent of the parse/pause flow) ──
    # agent_ready (human, carries user_id/user_login) and agent_login (native) open
    # a logged-in interval; agent_logout closes it. Runs before the None check so
    # agent_login/agent_logout (which the parser skips) are still captured.
    if topic == "agent.lifecycle" and redis is not None:
        try:
            await _handle_login_interval(raw, store, redis)
        except Exception as exc:
            logger.debug("login interval handling failed: %s", exc)

    if result is None:
        return  # skipped by parser (unknown event_type or missing fields)

    # Normalise to a list so routed/queued can return multiple rows
    rows = result if isinstance(result, list) else [result]

    # ── Channel preservation across parse_routed ──────────────────────────
    # parse_inbound carries channel ('webhook', 'webchat', …); populate cache.
    # parse_routed writes channel='' (routing event has no channel field);
    # restore from cache so ReplacingMergeTree keeps the original channel.
    if topic == "conversations.inbound":
        _cache_inbound_channel(raw)
    elif topic == "conversations.routed":
        _inject_cached_channel(rows)

    # ── F11: bucketize survey signals by the ORIGINAL session's date ──────────
    # Resolves session_at from analytics.sessions.opened_at (origin_session_id);
    # corrects deferred surveys where captured_at ≠ session_at. No-op fallback to
    # captured_at when the origin session is not resolvable.
    elif topic == "session.signals":
        await _enrich_signal_session_at(rows, store)

    # ── R9: deploy_version do segmento (fallback) ─────────────────────────────
    # Quando o bridge não enviou deploy_version mas há flow_id (segmento de IA),
    # resolve a versão corrente do skill no agent-registry (cache curto). Exato
    # quando o bridge envia; robusto quando não (independe do caminho de carga).
    elif topic == "conversations.participants":
        await _enrich_participant_deploy_version(rows, raw)

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

    # ── Journey J1: authoritative root_session_id from ContextStore ───────────
    # Overrides the parser's self-fallback so routed/queued/suspended/closed writes
    # don't clobber a child's transitive root (sessions is ReplacingMergeTree — the
    # whole row is replaced by the last write).
    if redis is not None:
        for row in rows:
            if row.get("table") == "sessions":
                await _enrich_session_root(row, redis)

    for row in rows:
        await _write_row(store, row, topic, offset)


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


def _login_redis_key(tenant_id: str, instance_id: str) -> str:
    """Redis key that stores the open login interval for an agent instance."""
    return f"{tenant_id}:login:{instance_id}"


def _iso_duration_ms(start_iso: str, end_iso: str) -> int | None:
    from datetime import datetime as _dt
    try:
        s = _dt.fromisoformat((start_iso or "").replace("Z", "+00:00"))
        e = _dt.fromisoformat((end_iso or "").replace("Z", "+00:00"))
        return int((e - s).total_seconds() * 1000)
    except Exception:
        return None


async def _apply_pool_diff(
    state:       dict,
    pools:       list,
    ts:          str,
    tenant_id:   str,
    instance_id: str,
    store:       object,
) -> None:
    """
    Open/close per-pool presence sub-intervals so the timeline can draw a lane per
    pool aligned to the agent's total lane. Mutates
    ``state['pools_open'] = {pool_id: {interval_id, entered_at}}``.

    Called only when the event carries an authoritative pools[] snapshot
    (registerHumanAgent / partial logout publish the full list; resume does not).
    """
    import uuid as _uuid

    pools_open: dict = state.setdefault("pools_open", {})
    new_set = {p for p in pools if p}
    cur_set = set(pools_open.keys())

    base = {
        "login_interval_id": state.get("interval_id", ""),
        "tenant_id":         tenant_id,
        "instance_id":       instance_id,
        "user_id":           state.get("user_id", ""),
        "user_login":        state.get("user_login", ""),
        "agent_type_id":     state.get("agent_type_id", ""),
    }

    for pid in new_set - cur_set:                      # entered a pool → open
        pid_interval = str(_uuid.uuid4())
        pools_open[pid] = {"interval_id": pid_interval, "entered_at": ts}
        await store.upsert_agent_pool_interval({       # type: ignore[attr-defined]
            **base, "interval_id": pid_interval, "pool_id": pid,
            "entered_at": ts, "left_at": None, "duration_ms": None,
        })

    for pid in cur_set - new_set:                      # left a pool → close
        info = pools_open.pop(pid)
        await store.upsert_agent_pool_interval({       # type: ignore[attr-defined]
            **base, "interval_id": info.get("interval_id", ""), "pool_id": pid,
            "entered_at": info.get("entered_at", ""), "left_at": ts,
            "duration_ms": _iso_duration_ms(info.get("entered_at", ""), ts),
        })


async def _handle_login_interval(raw: dict, store: object, redis: object) -> None:
    """
    Fase 1b + timeline — logged-time + per-pool presence state machine, keyed on
    {tenant_id}:login:{instance_id}.

    agent_ready / agent_login:
      • opens the login interval on the first event (writes the open row);
      • whenever the event carries an authoritative pools[] snapshot, diffs it
        against the open pools to open/close per-pool presence sub-intervals.
    agent_logout:
      • closes every open pool presence, then the login interval.
    """
    import json as _json
    import uuid as _uuid

    event       = raw.get("event")
    tenant_id   = raw.get("tenant_id", "")
    instance_id = raw.get("instance_id", "")
    if not tenant_id or not instance_id:
        return
    ts  = raw.get("timestamp") or datetime.now(timezone.utc).isoformat()
    key = _login_redis_key(tenant_id, instance_id)

    if event in ("agent_ready", "agent_login"):
        try:
            existing = await redis.get(key)  # type: ignore[union-attr]
        except Exception:
            existing = None

        if existing:
            try:
                state = _json.loads(existing)
            except Exception:
                return
        else:
            _pools = raw.get("pools") or []
            state = {
                "interval_id":   str(_uuid.uuid4()),
                "logged_in_at":  ts,
                "user_id":       raw.get("user_id", "") or "",
                "user_login":    raw.get("user_login", "") or "",
                "agent_type_id": raw.get("agent_type_id", "") or "",
                # agent_ready carries pools[] (not pool_id); fall back to pools[0]
                # so the login interval shares the pool with busy/pause rows.
                "pool_id":       raw.get("pool_id") or (_pools[0] if _pools else "") or "",
                "pools_open":    {},
            }
            await store.upsert_agent_login_interval({  # type: ignore[attr-defined]
                "interval_id":   state["interval_id"],
                "tenant_id":     tenant_id,
                "instance_id":   instance_id,
                "user_id":       state["user_id"],
                "user_login":    state["user_login"],
                "agent_type_id": state["agent_type_id"],
                "pool_id":       state["pool_id"],
                "logged_in_at":  ts,
                "logged_out_at": None,
                "duration_ms":   None,
            })

        # Per-pool presence diff — only when the event carries a pools snapshot.
        pools = raw.get("pools")
        if isinstance(pools, list) and pools:
            try:
                await _apply_pool_diff(state, pools, ts, tenant_id, instance_id, store)
            except Exception as exc:
                logger.debug("pool diff failed instance=%s: %s", instance_id, exc)

        try:
            await redis.set(key, _json.dumps(state), ex=_LOGIN_KEY_TTL)  # type: ignore[union-attr]
        except Exception as exc:
            logger.debug("Login Redis store failed instance=%s: %s", instance_id, exc)

    elif event == "agent_logout":
        try:
            raw_state = await redis.get(key)  # type: ignore[union-attr]
            if not raw_state:
                return  # no open interval — nothing to close
            state = _json.loads(raw_state)
            await redis.delete(key)  # type: ignore[union-attr]
        except Exception as exc:
            logger.debug("Login Redis lookup failed instance=%s: %s", instance_id, exc)
            return

        # Close every open pool presence first.
        base = {
            "login_interval_id": state.get("interval_id", ""),
            "tenant_id":         tenant_id,
            "instance_id":       instance_id,
            "user_id":           state.get("user_id", ""),
            "user_login":        state.get("user_login", ""),
            "agent_type_id":     state.get("agent_type_id", ""),
        }
        for pid, info in (state.get("pools_open") or {}).items():
            try:
                await store.upsert_agent_pool_interval({  # type: ignore[attr-defined]
                    **base, "interval_id": info.get("interval_id", ""), "pool_id": pid,
                    "entered_at": info.get("entered_at", ""), "left_at": ts,
                    "duration_ms": _iso_duration_ms(info.get("entered_at", ""), ts),
                })
            except Exception as exc:
                logger.debug("pool close failed instance=%s pool=%s: %s", instance_id, pid, exc)

        # Close the login interval.
        await store.upsert_agent_login_interval({  # type: ignore[attr-defined]
            "interval_id":   state.get("interval_id", ""),
            "tenant_id":     tenant_id,
            "instance_id":   instance_id,
            "user_id":       state.get("user_id", ""),
            "user_login":    state.get("user_login", ""),
            "agent_type_id": state.get("agent_type_id", ""),
            "pool_id":       state.get("pool_id", ""),
            "logged_in_at":  state.get("logged_in_at", ""),
            "logged_out_at": ts,
            "duration_ms":   _iso_duration_ms(state.get("logged_in_at", ""), ts),
        })

        # Close any OPEN pause interval — but only on an EXPLICIT logout, detected
        # by the absence of the durable pause marker ({tenant}:agent_paused:{id},
        # cleared synchronously by /api/agent-clear-pause before the grace-delayed
        # agent_logout). On navigation/crash the marker persists (agent stays
        # paused) → the pause interval stays open and continuous; resume closes it.
        try:
            durable = await redis.get(f"{tenant_id}:agent_paused:{instance_id}")  # type: ignore[union-attr]
            if not durable:
                pause_key = _pause_redis_key(tenant_id, instance_id)
                pause_raw = await redis.get(pause_key)  # type: ignore[union-attr]
                if pause_raw:
                    pstate = _json.loads(pause_raw)
                    await redis.delete(pause_key)  # type: ignore[union-attr]
                    p_in = pstate.get("paused_at", "")
                    await store.upsert_agent_pause_interval({  # type: ignore[attr-defined]
                        "interval_id":   pstate.get("interval_id", ""),
                        "tenant_id":     tenant_id,
                        "instance_id":   instance_id,
                        "agent_type_id": pstate.get("agent_type_id", ""),
                        "pool_id":       pstate.get("pool_id", ""),
                        "reason_id":     pstate.get("reason_id", ""),
                        "reason_label":  pstate.get("reason_label", ""),
                        "note":          pstate.get("note"),
                        "paused_at":     p_in,
                        "resumed_at":    ts,
                        "duration_ms":   _iso_duration_ms(p_in, ts),
                    })
        except Exception as exc:
            logger.debug("pause close on logout failed instance=%s: %s", instance_id, exc)


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
        elif table == "evaluation_dimension_scores":
            await store.insert_evaluation_dimension_score(row)
        elif table == "evaluation_finalized":
            await store.upsert_evaluation_finalized(row)
        elif table == "contact_insights":
            await store.insert_contact_insight(row)
        elif table == "agent_pause_intervals":
            await store.upsert_agent_pause_interval(row)
        elif table == "agent_business_events":
            await store.insert_agent_business_event(row)
        elif table == "session_signal":
            await store.insert_session_signal(row)
        elif table == "calibration_events":
            await store.insert_calibration_event(row)
        elif table == "pool_occupancy_peaks":
            await store.upsert_pool_occupancy_peak(row)
        elif table == "journey_aliases":
            await store.insert_journey_alias(row)
        else:
            logger.warning("Unknown table=%s from topic=%s offset=%s", table, topic, offset)
    except Exception as exc:
        logger.error(
            "ClickHouse write failed table=%s topic=%s offset=%s: %s",
            table, topic, offset, exc, exc_info=True,
        )
        # Re-raise so _process_with_retry can decide whether to retry or DLQ.
