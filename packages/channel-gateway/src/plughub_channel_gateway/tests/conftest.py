"""
conftest.py
Shared fixtures for Channel Gateway tests.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from plughub_channel_gateway.models import ContextSnapshot

# ── Constants ─────────────────────────────────────────────────────────────────

CONTACT_ID = "cid-test-001"
SESSION_ID  = "sid-test-001"
TENANT_ID   = "tenant_test"
JWT_SECRET  = "test_secret_32chars_webchat_ok!!"

# Fake JWT claims returned by the _token_validator bypass in tests
FAKE_CLAIMS = {
    "sub":        CONTACT_ID,
    "session_id": SESSION_ID,
    "tenant_id":  TENANT_ID,
}


# ── Redis mock ────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_redis():
    """Async Redis client mock covering all paths used by WebchatAdapter."""
    redis = AsyncMock()
    redis.setex   = AsyncMock(return_value=True)
    redis.get     = AsyncMock(return_value=None)
    redis.delete  = AsyncMock(return_value=1)
    redis.publish = AsyncMock(return_value=1)
    redis.aclose  = AsyncMock()

    # StreamSubscriber uses xread with BLOCK.  In tests we add a small sleep so
    # the tight empty-response loop doesn't starve other tasks before they can
    # complete and unblock asyncio.wait(FIRST_COMPLETED).
    async def _xread_mock(*args, **kwargs):
        await asyncio.sleep(0.005)
        return []

    redis.xread = _xread_mock

    # _typing_listener uses pubsub.  The mock listen() is an empty async
    # generator so the typing task completes on the first event-loop tick,
    # which causes asyncio.wait(FIRST_COMPLETED) to return quickly.
    pubsub_mock = AsyncMock()

    async def _pubsub_listen():
        """Empty async generator — typing task exits immediately in tests."""
        return
        yield  # pragma: no cover — makes this function an async generator

    pubsub_mock.subscribe   = AsyncMock()
    pubsub_mock.unsubscribe = AsyncMock()
    pubsub_mock.aclose      = AsyncMock()
    pubsub_mock.listen      = _pubsub_listen  # not AsyncMock — real async gen

    redis.pubsub = lambda: pubsub_mock
    return redis


# ── Kafka producer mock ───────────────────────────────────────────────────────

@pytest.fixture
def mock_producer():
    """AIOKafkaProducer mock."""
    producer = AsyncMock()
    producer.send  = AsyncMock()
    producer.start = AsyncMock()
    producer.stop  = AsyncMock()
    return producer


# ── WebSocket mock helpers ────────────────────────────────────────────────────

def make_auth_msg(cursor: str | None = None) -> str:
    """Returns a JSON-encoded conn.authenticate message for the test handshake."""
    msg: dict = {"type": "conn.authenticate", "token": "test_token"}
    if cursor is not None:
        msg["cursor"] = cursor
    return json.dumps(msg)


def make_ws_mock(messages: list[str] | None = None, *, skip_auth: bool = False):
    """
    Build a mock WebSocket that:
      1. Yields a conn.authenticate message (prepended automatically)
      2. Yields the provided *messages* in order
      3. Raises WebSocketDisconnect when exhausted

    Set skip_auth=True to omit the auth message (for testing auth-failure paths).
    """
    from fastapi import WebSocketDisconnect

    ws = AsyncMock()
    ws.accept   = AsyncMock()
    ws.send_json = AsyncMock()
    ws.close    = AsyncMock()

    _msgs = []
    if not skip_auth:
        _msgs.append(make_auth_msg())
    _msgs.extend(messages or [])

    async def receive_text():
        if _msgs:
            return _msgs.pop(0)
        raise WebSocketDisconnect(code=1000)

    ws.receive_text = receive_text
    return ws


@pytest.fixture
def ws_factory():
    return make_ws_mock


# ── Adapter collaborator fixtures ─────────────────────────────────────────────

@pytest.fixture
def registry(mock_redis):
    """SessionRegistry mock wired to the shared redis mock."""
    reg = AsyncMock()
    reg.register   = AsyncMock()
    reg.unregister = AsyncMock(return_value="2024-01-01T10:00:00Z")
    reg.send       = AsyncMock(return_value=True)
    reg.append_message = AsyncMock()
    reg._redis     = mock_redis
    return reg


@pytest.fixture
def context_reader():
    """ContextReader mock that returns a generic snapshot."""
    cr = AsyncMock()
    cr.get_snapshot = AsyncMock(
        return_value=ContextSnapshot(
            intent="general_inquiry", sentiment_score=0.7, turn_number=1
        )
    )
    return cr


# ── Settings mock ─────────────────────────────────────────────────────────────

@pytest.fixture
def settings():
    from plughub_channel_gateway.config import Settings
    return Settings(
        kafka_brokers             = "localhost:9092",
        kafka_group_id            = "test-group",
        kafka_topic_inbound       = "conversations.inbound",
        kafka_topic_outbound      = "conversations.outbound",
        kafka_topic_events        = "conversations.events",
        redis_url                 = "redis://localhost:6379/0",
        ws_connection_timeout_s   = 30,
        ws_heartbeat_interval_s   = 10,
        ws_contact_max_duration_s = 3600,
        session_ttl_seconds       = 3600,
        jwt_secret                = JWT_SECRET,
        ws_auth_timeout_s         = 10,
        storage_root              = "/tmp/plughub_test_attachments",
        attachment_expiry_days    = 1,
        database_url              = "postgresql://plughub:plughub@localhost:5432/plughub",
        webchat_serving_base_url  = "http://localhost:8010/webchat/v1/attachments",
        webchat_upload_base_url   = "http://localhost:8010/webchat/v1/upload",
        tenant_id                 = TENANT_ID,
    )
