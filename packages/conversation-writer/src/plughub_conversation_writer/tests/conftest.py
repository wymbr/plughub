"""conftest.py — shared fixtures for Conversation Writer tests."""

from __future__ import annotations
import json
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from plughub_conversation_writer.config import Settings


# ── Settings ──────────────────────────────────────────────────────────────────

@pytest.fixture
def settings():
    return Settings(
        kafka_brokers="localhost:9092",
        kafka_group_id="test-group",
        kafka_topic_inbound="conversations.inbound",
        kafka_topic_outbound="conversations.outbound",
        kafka_topic_events="conversations.events",
        kafka_topic_eval_events="evaluation.events",
        redis_url="redis://localhost:6379/0",
        transcript_ttl_seconds=3600,
        postgres_dsn="postgresql://test:test@localhost/test",
    )


# ── Redis mock ────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_redis():
    r = AsyncMock()
    r.rpush  = AsyncMock(return_value=1)
    r.expire = AsyncMock(return_value=True)
    r.lrange = AsyncMock(return_value=[])
    r.delete = AsyncMock(return_value=1)
    r.get    = AsyncMock(return_value=None)
    r.setex  = AsyncMock(return_value=True)
    return r


# ── Kafka producer mock ───────────────────────────────────────────────────────

@pytest.fixture
def mock_producer():
    p = AsyncMock()
    p.send = AsyncMock()
    return p


# ── PostgresWriter mock ───────────────────────────────────────────────────────

@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.persist_transcript = AsyncMock(return_value=str(uuid.uuid4()))
    return db


# ── Message factory ───────────────────────────────────────────────────────────

def make_message(
    contact_id: str = "cid-001",
    direction: str = "inbound",
    text: str = "Olá",
    turn: int = 1,
    ts: str | None = None,
) -> dict:
    return {
        "message_id": str(uuid.uuid4()),
        "contact_id": contact_id,
        "session_id": "sid-001",
        "timestamp": ts or datetime.now(timezone.utc).isoformat(),
        "direction": direction,
        "channel": "chat",
        "author": {"type": "customer" if direction == "inbound" else "agent_ai"},
        "content": {"type": "text", "text": text},
        "context_snapshot": {"intent": None, "sentiment_score": None, "turn_number": turn},
    }


def make_contact_closed(
    contact_id: str = "cid-001",
    reason: str = "client_disconnect",
    started_at: str | None = None,
    ended_at: str | None = None,
) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "event_type": "contact_closed",
        "contact_id": contact_id,
        "session_id": "sid-001",
        "channel": "chat",
        "reason": reason,
        "started_at": started_at or now,
        "ended_at": ended_at or now,
    }
