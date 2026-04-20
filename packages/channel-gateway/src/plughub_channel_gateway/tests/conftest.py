"""
conftest.py
Shared fixtures for Channel Gateway tests.
"""

from __future__ import annotations
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ── Redis mock ────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_redis():
    """Async Redis client mock."""
    redis = AsyncMock()
    redis.setex = AsyncMock(return_value=True)
    redis.delete = AsyncMock(return_value=1)
    redis.get = AsyncMock(return_value=None)
    redis.publish = AsyncMock(return_value=1)
    redis.aclose = AsyncMock()
    return redis


# ── Kafka producer mock ───────────────────────────────────────────────────────

@pytest.fixture
def mock_producer():
    """AIOKafkaProducer mock."""
    producer = AsyncMock()
    producer.send = AsyncMock()
    producer.start = AsyncMock()
    producer.stop = AsyncMock()
    return producer


# ── WebSocket mock ────────────────────────────────────────────────────────────

def make_ws_mock(messages: list[str] | None = None):
    """
    Build a mock WebSocket that yields messages from *messages* then raises
    WebSocketDisconnect.
    """
    from fastapi import WebSocketDisconnect

    ws = AsyncMock()
    ws.accept = AsyncMock()
    ws.send_json = AsyncMock()
    ws.close = AsyncMock()

    _messages = list(messages or [])

    async def receive_text():
        if _messages:
            return _messages.pop(0)
        raise WebSocketDisconnect(code=1000)

    ws.receive_text = receive_text
    return ws


@pytest.fixture
def ws_factory():
    return make_ws_mock


# ── Settings mock ─────────────────────────────────────────────────────────────

@pytest.fixture
def settings():
    from plughub_channel_gateway.config import Settings
    return Settings(
        kafka_brokers="localhost:9092",
        kafka_group_id="test-group",
        kafka_topic_inbound="conversations.inbound",
        kafka_topic_outbound="conversations.outbound",
        kafka_topic_events="conversations.events",
        redis_url="redis://localhost:6379/0",
        ws_connection_timeout_s=30,
        ws_heartbeat_interval_s=10,
        ws_contact_max_duration_s=3600,
        session_ttl_seconds=3600,
    )
