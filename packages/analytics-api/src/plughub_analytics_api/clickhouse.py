"""
clickhouse.py
ClickHouse client wrapper + DDL for the Analytics API.

Six tables, all in database `plughub`:

  sessions        — session lifecycle (opened, closed, channel, pool, durations)
  queue_events    — contact queued / dequeued / abandoned / position_updated
  agent_events    — contact routed + agent_done (outcome, handle time)
  messages        — messages published to the canonical stream
  usage_events    — metering events (passthrough from usage.events Kafka topic)
  sentiment_events— per-turn sentiment scores from AI Gateway

Design decisions:
  - ReplacingMergeTree on every table for idempotent re-inserts (Kafka at-least-once).
  - sessions uses version=closed_at: a second insert with closed_at set replaces the
    initial row that had closed_at = NULL.
  - All DateTime columns store UTC (ClickHouse DateTime64 with timezone 'UTC').
  - date column (Date) is the partition key for efficient time-range pruning.
  - ORDER BY always starts with (tenant_id, ...) so tenant-scoped queries are fast.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import clickhouse_connect  # type: ignore[import-untyped]

logger = logging.getLogger("plughub.analytics.clickhouse")

# ─── DDL ─────────────────────────────────────────────────────────────────────

_DDL_DATABASE = "CREATE DATABASE IF NOT EXISTS {db}"

_DDL_SESSIONS = """
CREATE TABLE IF NOT EXISTS {db}.sessions
(
    session_id     String,
    tenant_id      String,
    channel        String,
    pool_id        String,
    customer_id    Nullable(String),
    opened_at      DateTime64(3, 'UTC'),
    closed_at      Nullable(DateTime64(3, 'UTC')),
    close_reason   Nullable(String),
    outcome        Nullable(String),
    wait_time_ms   Nullable(Int64),
    handle_time_ms Nullable(Int64),
    date           Date
)
ENGINE = ReplacingMergeTree(closed_at)
PARTITION BY toYYYYMM(date)
ORDER BY (tenant_id, session_id)
"""

# Forward-compatible migration for tables that already exist without customer_id.
# ClickHouse ADD COLUMN IF NOT EXISTS is idempotent.
_DDL_SESSIONS_MIGRATE = (
    "ALTER TABLE {db}.sessions ADD COLUMN IF NOT EXISTS"
    " customer_id Nullable(String) DEFAULT NULL"
)

_DDL_QUEUE_EVENTS = """
CREATE TABLE IF NOT EXISTS {db}.queue_events
(
    event_id           String,
    tenant_id          String,
    session_id         String,
    pool_id            String,
    event_type         String,
    queue_position     Nullable(Int32),
    estimated_wait_ms  Nullable(Int64),
    available_agents   Nullable(Int32),
    timestamp          DateTime64(3, 'UTC'),
    date               Date
)
ENGINE = ReplacingMergeTree()
PARTITION BY toYYYYMM(date)
ORDER BY (tenant_id, event_id)
"""

_DDL_AGENT_EVENTS = """
CREATE TABLE IF NOT EXISTS {db}.agent_events
(
    event_id       String,
    tenant_id      String,
    session_id     String,
    agent_type_id  String,
    pool_id        String,
    instance_id    String,
    event_type     String,
    outcome        Nullable(String),
    handoff_reason Nullable(String),
    handle_time_ms Nullable(Int64),
    routing_mode   Nullable(String),
    timestamp      DateTime64(3, 'UTC'),
    date           Date
)
ENGINE = ReplacingMergeTree()
PARTITION BY toYYYYMM(date)
ORDER BY (tenant_id, event_id)
"""

_DDL_MESSAGES = """
CREATE TABLE IF NOT EXISTS {db}.messages
(
    message_id   String,
    tenant_id    String,
    session_id   String,
    author_role  String,
    channel      String,
    content_type String,
    visibility   String,
    timestamp    DateTime64(3, 'UTC'),
    date         Date
)
ENGINE = ReplacingMergeTree()
PARTITION BY toYYYYMM(date)
ORDER BY (tenant_id, message_id)
"""

_DDL_USAGE_EVENTS = """
CREATE TABLE IF NOT EXISTS {db}.usage_events
(
    event_id         String,
    tenant_id        String,
    session_id       String,
    dimension        String,
    quantity         Int64,
    source_component String,
    timestamp        DateTime64(3, 'UTC'),
    date             Date
)
ENGINE = ReplacingMergeTree()
PARTITION BY toYYYYMM(date)
ORDER BY (tenant_id, event_id)
"""

_DDL_SENTIMENT_EVENTS = """
CREATE TABLE IF NOT EXISTS {db}.sentiment_events
(
    event_id   String,
    tenant_id  String,
    session_id String,
    pool_id    String,
    score      Float32,
    category   String,
    timestamp  DateTime64(3, 'UTC'),
    date       Date
)
ENGINE = ReplacingMergeTree()
PARTITION BY toYYYYMM(date)
ORDER BY (tenant_id, session_id, timestamp)
"""

_DDL_WORKFLOW_EVENTS = """
CREATE TABLE IF NOT EXISTS {db}.workflow_events
(
    event_id        String,
    tenant_id       String,
    instance_id     String,
    flow_id         String,
    campaign_id     Nullable(String),
    event_type      String,
    status          Nullable(String),
    current_step    Nullable(String),
    suspend_reason  Nullable(String),
    decision        Nullable(String),
    outcome         Nullable(String),
    duration_ms     Nullable(Int64),
    wait_duration_ms Nullable(Int64),
    error           Nullable(String),
    timestamp       DateTime64(3, 'UTC'),
    date            Date
)
ENGINE = ReplacingMergeTree()
PARTITION BY toYYYYMM(date)
ORDER BY (tenant_id, instance_id, timestamp)
"""

_DDL_COLLECT_EVENTS = """
CREATE TABLE IF NOT EXISTS {db}.collect_events
(
    collect_token  String,
    tenant_id      String,
    instance_id    String,
    flow_id        String,
    campaign_id    Nullable(String),
    step_id        String,
    target_type    String,
    channel        String,
    interaction    String,
    status         String,
    send_at        Nullable(DateTime64(3, 'UTC')),
    responded_at   Nullable(DateTime64(3, 'UTC')),
    elapsed_ms     Nullable(Int64),
    timestamp      DateTime64(3, 'UTC'),
    date           Date
)
ENGINE = ReplacingMergeTree(status)
PARTITION BY toYYYYMM(date)
ORDER BY (tenant_id, collect_token)
"""

_ALL_DDL = [
    _DDL_DATABASE,
    _DDL_SESSIONS,
    _DDL_QUEUE_EVENTS,
    _DDL_AGENT_EVENTS,
    _DDL_MESSAGES,
    _DDL_USAGE_EVENTS,
    _DDL_SENTIMENT_EVENTS,
    _DDL_WORKFLOW_EVENTS,
    _DDL_COLLECT_EVENTS,
]

# Migrations applied after CREATE IF NOT EXISTS (idempotent ALTER TABLE statements).
_MIGRATIONS = [
    _DDL_SESSIONS_MIGRATE,
]


# ─── Client wrapper ───────────────────────────────────────────────────────────

class AnalyticsStore:
    """
    Wraps a synchronous clickhouse_connect client.
    All insert methods run via asyncio.to_thread() to avoid blocking the event loop.
    """

    def __init__(
        self,
        host:     str,
        port:     int,
        user:     str,
        password: str,
        database: str,
    ) -> None:
        self._client   = clickhouse_connect.get_client(
            host=host, port=port,
            username=user, password=password,
        )
        self._database = database

    # ── Schema ────────────────────────────────────────────────────────────────

    def ensure_schema(self) -> None:
        """Creates the database and all tables if they don't exist. Idempotent."""
        for ddl in _ALL_DDL:
            stmt = ddl.format(db=self._database)
            self._client.command(stmt)
        # Forward-compatible migrations (idempotent ALTER TABLE statements).
        for ddl in _MIGRATIONS:
            try:
                self._client.command(ddl.format(db=self._database))
            except Exception as exc:
                logger.warning("Migration skipped (already applied?): %s — %s", ddl[:60], exc)
        logger.info("ClickHouse schema ensured (database=%s)", self._database)

    async def ensure_schema_async(self) -> None:
        await asyncio.to_thread(self.ensure_schema)

    # ── Inserts ───────────────────────────────────────────────────────────────

    def _insert(self, table: str, rows: list[list[Any]], columns: list[str]) -> None:
        if not rows:
            return
        self._client.insert(
            f"{self._database}.{table}",
            rows,
            column_names=columns,
        )

    # sessions

    _SESSION_COLS = [
        "session_id", "tenant_id", "channel", "pool_id", "customer_id",
        "opened_at", "closed_at", "close_reason", "outcome",
        "wait_time_ms", "handle_time_ms", "date",
    ]

    async def upsert_session(self, row: dict) -> None:
        await asyncio.to_thread(
            self._insert, "sessions", [_session_row(row)], self._SESSION_COLS
        )

    # queue_events

    _QUEUE_COLS = [
        "event_id", "tenant_id", "session_id", "pool_id",
        "event_type", "queue_position", "estimated_wait_ms", "available_agents",
        "timestamp", "date",
    ]

    async def insert_queue_event(self, row: dict) -> None:
        await asyncio.to_thread(
            self._insert, "queue_events", [_queue_row(row)], self._QUEUE_COLS
        )

    # agent_events

    _AGENT_COLS = [
        "event_id", "tenant_id", "session_id", "agent_type_id", "pool_id",
        "instance_id", "event_type", "outcome", "handoff_reason",
        "handle_time_ms", "routing_mode", "timestamp", "date",
    ]

    async def insert_agent_event(self, row: dict) -> None:
        await asyncio.to_thread(
            self._insert, "agent_events", [_agent_row(row)], self._AGENT_COLS
        )

    # messages

    _MESSAGE_COLS = [
        "message_id", "tenant_id", "session_id", "author_role",
        "channel", "content_type", "visibility", "timestamp", "date",
    ]

    async def insert_message(self, row: dict) -> None:
        await asyncio.to_thread(
            self._insert, "messages", [_message_row(row)], self._MESSAGE_COLS
        )

    # usage_events

    _USAGE_COLS = [
        "event_id", "tenant_id", "session_id",
        "dimension", "quantity", "source_component", "timestamp", "date",
    ]

    async def insert_usage_event(self, row: dict) -> None:
        await asyncio.to_thread(
            self._insert, "usage_events", [_usage_row(row)], self._USAGE_COLS
        )

    # sentiment_events

    _SENTIMENT_COLS = [
        "event_id", "tenant_id", "session_id", "pool_id",
        "score", "category", "timestamp", "date",
    ]

    async def insert_sentiment_event(self, row: dict) -> None:
        await asyncio.to_thread(
            self._insert, "sentiment_events", [_sentiment_row(row)], self._SENTIMENT_COLS
        )

    # workflow_events

    _WORKFLOW_EVENT_COLS = [
        "event_id", "tenant_id", "instance_id", "flow_id", "campaign_id",
        "event_type", "status", "current_step", "suspend_reason", "decision",
        "outcome", "duration_ms", "wait_duration_ms", "error", "timestamp", "date",
    ]

    async def insert_workflow_event(self, row: dict) -> None:
        await asyncio.to_thread(
            self._insert, "workflow_events", [_workflow_event_row(row)], self._WORKFLOW_EVENT_COLS
        )

    # collect_events

    _COLLECT_EVENT_COLS = [
        "collect_token", "tenant_id", "instance_id", "flow_id", "campaign_id",
        "step_id", "target_type", "channel", "interaction", "status",
        "send_at", "responded_at", "elapsed_ms", "timestamp", "date",
    ]

    async def insert_collect_event(self, row: dict) -> None:
        await asyncio.to_thread(
            self._insert, "collect_events", [_collect_event_row(row)], self._COLLECT_EVENT_COLS
        )


# ─── Row builders ─────────────────────────────────────────────────────────────

def _parse_dt(ts: str | None) -> datetime | None:
    """Parses an ISO8601 string to a naive UTC datetime (ClickHouse expects naive)."""
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    except Exception:
        return datetime.utcnow()


def _today_utc(ts: str | None = None) -> datetime:
    """Returns a date for the partition key. Prefers the event timestamp."""
    dt = _parse_dt(ts)
    return dt if dt else datetime.utcnow()


def _session_row(d: dict) -> list:
    ts = d.get("timestamp") or d.get("opened_at") or d.get("started_at")
    return [
        d.get("session_id", ""),
        d.get("tenant_id", ""),
        d.get("channel", ""),
        d.get("pool_id", "") or "",
        d.get("customer_id") or d.get("contact_id") or None,
        _parse_dt(d.get("opened_at") or d.get("started_at") or d.get("timestamp")) or datetime.utcnow(),
        _parse_dt(d.get("closed_at") or d.get("ended_at")),
        d.get("close_reason"),
        d.get("outcome"),
        d.get("wait_time_ms"),
        d.get("handle_time_ms"),
        _today_utc(ts),
    ]


def _queue_row(d: dict) -> list:
    ts = d.get("timestamp") or d.get("published_at")
    return [
        d.get("event_id", ""),
        d.get("tenant_id", ""),
        d.get("session_id", ""),
        d.get("pool_id", "") or "",
        d.get("event_type", ""),
        d.get("queue_position") or d.get("queue_length"),
        d.get("estimated_wait_ms"),
        d.get("available_agents"),
        _parse_dt(ts) or datetime.utcnow(),
        _today_utc(ts),
    ]


def _agent_row(d: dict) -> list:
    ts = d.get("timestamp") or d.get("routed_at")
    return [
        d.get("event_id", ""),
        d.get("tenant_id", ""),
        d.get("session_id", ""),
        d.get("agent_type_id", "") or "",
        d.get("pool_id", "") or "",
        d.get("instance_id", "") or "",
        d.get("event_type", ""),
        d.get("outcome"),
        d.get("handoff_reason"),
        d.get("handle_time_ms"),
        d.get("routing_mode"),
        _parse_dt(ts) or datetime.utcnow(),
        _today_utc(ts),
    ]


def _message_row(d: dict) -> list:
    ts = d.get("timestamp")
    return [
        d.get("message_id", ""),
        d.get("tenant_id", ""),
        d.get("session_id", ""),
        d.get("author_role", ""),
        d.get("channel", "") or "",
        d.get("content_type", "") or "",
        d.get("visibility", "all"),
        _parse_dt(ts) or datetime.utcnow(),
        _today_utc(ts),
    ]


def _usage_row(d: dict) -> list:
    ts = d.get("timestamp")
    return [
        d.get("event_id", ""),
        d.get("tenant_id", ""),
        d.get("session_id", "") or "",
        d.get("dimension", ""),
        int(d.get("quantity", 0)),
        d.get("source_component", "") or "",
        _parse_dt(ts) or datetime.utcnow(),
        _today_utc(ts),
    ]


def _sentiment_row(d: dict) -> list:
    ts = d.get("timestamp")
    return [
        d.get("event_id", ""),
        d.get("tenant_id", ""),
        d.get("session_id", ""),
        d.get("pool_id", "") or "",
        float(d.get("score", 0.0)),
        d.get("category", "neutral"),
        _parse_dt(ts) or datetime.utcnow(),
        _today_utc(ts),
    ]


def _workflow_event_row(d: dict) -> list:
    ts = d.get("timestamp")
    return [
        d.get("event_id", ""),
        d.get("tenant_id", ""),
        d.get("instance_id", ""),
        d.get("flow_id", ""),
        d.get("campaign_id"),
        d.get("event_type", ""),
        d.get("status"),
        d.get("current_step"),
        d.get("suspend_reason"),
        d.get("decision"),
        d.get("outcome"),
        d.get("duration_ms"),
        d.get("wait_duration_ms"),
        d.get("error"),
        _parse_dt(ts) or datetime.utcnow(),
        _today_utc(ts),
    ]


def _collect_event_row(d: dict) -> list:
    ts = d.get("timestamp")
    return [
        d.get("collect_token", ""),
        d.get("tenant_id", ""),
        d.get("instance_id", ""),
        d.get("flow_id", ""),
        d.get("campaign_id"),
        d.get("step_id", ""),
        d.get("target_type", ""),
        d.get("channel", ""),
        d.get("interaction", ""),
        d.get("status", ""),
        _parse_dt(d.get("send_at")),
        _parse_dt(d.get("responded_at")),
        d.get("elapsed_ms"),
        _parse_dt(ts) or datetime.utcnow(),
        _today_utc(ts),
    ]
