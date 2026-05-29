"""
test_webhook_adapter.py
Unit tests for WebhookAdapter — Arc 19 Unified Session Model.

All tests use AsyncMock for Redis and Kafka producer — no network I/O.

Coverage:
  handle_trigger:
    - Returns a new session_id (UUID string)
    - Publishes to conversations.inbound with correct fields
    - Uses provided customer_id when present; generates sys: prefix when absent
    - Passes metadata through to the event

  handle_resume (Fase A):
    - Returns None for unknown / expired resume_token
    - Returns None for malformed token value (< 2 parts)
    - Returns session_id on success
    - Deletes the token after successful resume (one-shot)

  handle_resume (Fase B — stream events):
    - Writes session_resumed to canonical stream before Kafka publish
    - Sets status key back to "active" with keepttl=True on success
    - Does NOT block resume when stream write fails (non-fatal path)
    - Does NOT block resume when status key write fails (non-fatal path)

  get_status:
    - Returns "active" when Redis key holds "active"
    - Returns "suspended" when Redis key holds "suspended"
    - Returns "closed" when Redis key has expired (None)

  outbound no-ops:
    - deliver_text, deliver_menu, deliver_typing, deliver_session_closed
      all complete without error and make no external calls
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, call, patch, MagicMock

import pytest

from plughub_channel_gateway.adapters.webhook import WebhookAdapter
from plughub_channel_gateway.config import Settings


# ── Constants ──────────────────────────────────────────────────────────────────

TENANT_ID   = "tenant_test"
SESSION_ID  = "sid-wh-001"
SKILL_ID    = "skill_portabilidade_v1"
CUSTOMER_ID = "cust_abc123"

RESUME_TOKEN = "a" * 43  # opaque 43-char token
STEP_ID      = "step_aguardar_aprovacao"
EXPIRES_AT   = "2026-06-01T12:00:00+00:00"

# token_value format: "{session_id}:{step_id}:{expires_at}"
VALID_TOKEN_VALUE = f"{SESSION_ID}:{STEP_ID}:{EXPIRES_AT}"


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def mock_redis():
    redis = AsyncMock()
    redis.hget   = AsyncMock(return_value=None)
    redis.hdel   = AsyncMock(return_value=1)
    redis.get    = AsyncMock(return_value=None)
    redis.set    = AsyncMock(return_value=True)
    redis.setex  = AsyncMock(return_value=True)
    redis.xadd   = AsyncMock(return_value=b"1-0")
    return redis


@pytest.fixture
def mock_producer():
    producer = AsyncMock()
    producer.send_and_wait = AsyncMock()
    return producer


@pytest.fixture
def wh_settings():
    return Settings(
        kafka_brokers            = "localhost:9092",
        kafka_group_id           = "test-group",
        kafka_topic_inbound      = "conversations.inbound",
        kafka_topic_outbound     = "conversations.outbound",
        kafka_topic_events       = "conversations.events",
        redis_url                = "redis://localhost:6379",
        tenant_id                = TENANT_ID,
        storage_root             = "/tmp/plughub_test",
        attachment_expiry_days   = 1,
        database_url             = "postgresql://plughub:plughub@localhost/plughub",
        webchat_serving_base_url = "http://localhost:8010/webchat/v1/attachments",
        webchat_upload_base_url  = "http://localhost:8010/webchat/v1/upload",
    )


@pytest.fixture
def adapter(mock_redis, mock_producer, wh_settings):
    return WebhookAdapter(
        producer = mock_producer,
        redis    = mock_redis,
        settings = wh_settings,
    )


# ── handle_trigger tests ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_handle_trigger_returns_uuid(adapter):
    session_id = await adapter.handle_trigger(
        skill_id   = SKILL_ID,
        tenant_id  = TENANT_ID,
    )
    # Must be a parseable UUID
    parsed = uuid.UUID(session_id)
    assert str(parsed) == session_id


@pytest.mark.asyncio
async def test_handle_trigger_publishes_inbound_event(adapter, mock_producer):
    await adapter.handle_trigger(
        skill_id     = SKILL_ID,
        tenant_id    = TENANT_ID,
        trigger_type = "api",
        metadata     = {"source": "crm"},
    )
    mock_producer.send_and_wait.assert_called_once()
    args, kwargs = mock_producer.send_and_wait.call_args
    assert args[0] == "conversations.inbound"

    event = json.loads(kwargs["value"])
    assert event["channel"]        == "webhook"
    assert event["skill_id"]       == SKILL_ID
    assert event["tenant_id"]      == TENANT_ID
    assert event["trigger_type"]   == "api"
    assert event["metadata"]       == {"source": "crm"}
    assert "session_id"  in event
    assert "event_id"    in event
    assert "timestamp"   in event


@pytest.mark.asyncio
async def test_handle_trigger_uses_provided_customer_id(adapter, mock_producer):
    await adapter.handle_trigger(
        skill_id    = SKILL_ID,
        tenant_id   = TENANT_ID,
        customer_id = CUSTOMER_ID,
    )
    event = json.loads(mock_producer.send_and_wait.call_args[1]["value"])
    assert event["customer_id"] == CUSTOMER_ID


@pytest.mark.asyncio
async def test_handle_trigger_generates_sys_customer_id_when_absent(adapter, mock_producer):
    await adapter.handle_trigger(
        skill_id  = SKILL_ID,
        tenant_id = TENANT_ID,
    )
    event = json.loads(mock_producer.send_and_wait.call_args[1]["value"])
    assert event["customer_id"].startswith("sys:api:")


# ── handle_resume — Fase A (token resolution) ─────────────────────────────────

@pytest.mark.asyncio
async def test_handle_resume_returns_none_for_unknown_token(adapter, mock_redis):
    mock_redis.hget.return_value = None  # token not found
    result = await adapter.handle_resume(
        resume_token = RESUME_TOKEN,
        tenant_id    = TENANT_ID,
    )
    assert result is None


@pytest.mark.asyncio
async def test_handle_resume_returns_none_for_malformed_token_value(adapter, mock_redis):
    mock_redis.hget.return_value = "only_one_part"  # no colon separators
    result = await adapter.handle_resume(
        resume_token = RESUME_TOKEN,
        tenant_id    = TENANT_ID,
    )
    assert result is None


@pytest.mark.asyncio
async def test_handle_resume_returns_session_id_on_success(adapter, mock_redis):
    mock_redis.hget.return_value = VALID_TOKEN_VALUE
    result = await adapter.handle_resume(
        resume_token = RESUME_TOKEN,
        tenant_id    = TENANT_ID,
    )
    assert result == SESSION_ID


@pytest.mark.asyncio
async def test_handle_resume_deletes_token_after_success(adapter, mock_redis):
    mock_redis.hget.return_value = VALID_TOKEN_VALUE
    await adapter.handle_resume(
        resume_token = RESUME_TOKEN,
        tenant_id    = TENANT_ID,
    )
    mock_redis.hdel.assert_called_once_with(
        f"{TENANT_ID}:resume_tokens",
        RESUME_TOKEN,
    )


@pytest.mark.asyncio
async def test_handle_resume_publishes_kafka_event(adapter, mock_redis, mock_producer):
    mock_redis.hget.return_value = VALID_TOKEN_VALUE
    await adapter.handle_resume(
        resume_token = RESUME_TOKEN,
        tenant_id    = TENANT_ID,
        payload      = {"decision": "approved"},
    )
    mock_producer.send_and_wait.assert_called_once()
    event = json.loads(mock_producer.send_and_wait.call_args[1]["value"])
    assert event["event_type"]   == "session_resumed"
    assert event["session_id"]   == SESSION_ID
    assert event["resume_token"] == RESUME_TOKEN
    assert event["step_id"]      == STEP_ID
    assert event["payload"]      == {"decision": "approved"}


# ── handle_resume — Fase B (stream events) ───────────────────────────────────

@pytest.mark.asyncio
async def test_handle_resume_writes_session_resumed_to_stream(adapter, mock_redis):
    """
    Fase B: session_resumed must be written to the canonical stream
    BEFORE the Kafka conversations.inbound event is published.
    """
    mock_redis.hget.return_value = VALID_TOKEN_VALUE

    await adapter.handle_resume(
        resume_token = RESUME_TOKEN,
        tenant_id    = TENANT_ID,
        payload      = {"result": "ok"},
    )

    mock_redis.xadd.assert_called_once()
    stream_key, fields = (
        mock_redis.xadd.call_args[0][0],
        mock_redis.xadd.call_args[0][1],
    )

    assert stream_key == f"session:{SESSION_ID}:stream"
    assert fields["type"]        == "session_resumed"
    assert fields["author_id"]   == "webhook_adapter"
    assert fields["author_role"] == "system"

    payload_obj = json.loads(fields["payload"])
    assert payload_obj["step_id"]      == STEP_ID
    assert payload_obj["resume_token"] == RESUME_TOKEN
    assert payload_obj["payload"]      == {"result": "ok"}


@pytest.mark.asyncio
async def test_handle_resume_resets_status_key_to_active(adapter, mock_redis):
    """
    Fase B: status key must be reset to "active" with keepttl=True so
    get_status() reflects the transition without touching the existing TTL.
    """
    mock_redis.hget.return_value = VALID_TOKEN_VALUE

    await adapter.handle_resume(
        resume_token = RESUME_TOKEN,
        tenant_id    = TENANT_ID,
    )

    mock_redis.set.assert_called_once_with(
        f"{TENANT_ID}:session:{SESSION_ID}:status",
        "active",
        keepttl=True,
    )


@pytest.mark.asyncio
async def test_handle_resume_stream_write_failure_is_non_fatal(adapter, mock_redis):
    """
    Fase B non-fatal path: stream write failure must NOT block the resume flow.
    Kafka event and token deletion must still happen even when xadd raises.
    """
    mock_redis.hget.return_value = VALID_TOKEN_VALUE
    mock_redis.xadd.side_effect  = RuntimeError("Redis unavailable")

    result = await adapter.handle_resume(
        resume_token = RESUME_TOKEN,
        tenant_id    = TENANT_ID,
    )

    # Resume must succeed despite the stream write failure
    assert result == SESSION_ID
    # Token must still be deleted
    mock_redis.hdel.assert_called_once()


@pytest.mark.asyncio
async def test_handle_resume_status_key_failure_is_non_fatal(adapter, mock_redis):
    """
    Fase B non-fatal path: status key failure must NOT block the resume flow.
    """
    mock_redis.hget.return_value = VALID_TOKEN_VALUE
    mock_redis.set.side_effect   = RuntimeError("Redis SET failed")

    result = await adapter.handle_resume(
        resume_token = RESUME_TOKEN,
        tenant_id    = TENANT_ID,
    )

    assert result == SESSION_ID
    mock_redis.hdel.assert_called_once()


@pytest.mark.asyncio
async def test_handle_resume_stream_written_before_kafka(adapter, mock_redis, mock_producer):
    """
    Fase B ordering guarantee: xadd must be called before send_and_wait.
    Verifies that consumers (analytics-api, Monitor) see the stream transition
    before the routing engine re-allocation fires via Kafka.
    """
    call_order: list[str] = []

    async def _xadd(*args, **kwargs):
        call_order.append("xadd")
        return b"1-0"

    async def _set(*args, **kwargs):
        call_order.append("set")
        return True

    async def _send_and_wait(*args, **kwargs):
        call_order.append("kafka")

    mock_redis.xadd          = _xadd
    mock_redis.set            = _set
    mock_producer.send_and_wait = _send_and_wait
    mock_redis.hget.return_value = VALID_TOKEN_VALUE

    await adapter.handle_resume(
        resume_token = RESUME_TOKEN,
        tenant_id    = TENANT_ID,
    )

    # xadd and set must both precede the kafka publish
    assert call_order.index("xadd")  < call_order.index("kafka")
    assert call_order.index("set")   < call_order.index("kafka")


# ── get_status tests ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_status_returns_active(adapter, mock_redis):
    mock_redis.get.return_value = "active"
    result = await adapter.get_status(SESSION_ID, TENANT_ID)
    assert result == {"session_id": SESSION_ID, "status": "active"}


@pytest.mark.asyncio
async def test_get_status_returns_suspended(adapter, mock_redis):
    """Fase B: suspended is a valid status set by orchestrator-bridge."""
    mock_redis.get.return_value = "suspended"
    result = await adapter.get_status(SESSION_ID, TENANT_ID)
    assert result == {"session_id": SESSION_ID, "status": "suspended"}


@pytest.mark.asyncio
async def test_get_status_returns_closed_when_key_expired(adapter, mock_redis):
    mock_redis.get.return_value = None  # TTL elapsed
    result = await adapter.get_status(SESSION_ID, TENANT_ID)
    assert result == {"session_id": SESSION_ID, "status": "closed"}


@pytest.mark.asyncio
async def test_get_status_reads_correct_key(adapter, mock_redis):
    mock_redis.get.return_value = "active"
    await adapter.get_status(SESSION_ID, TENANT_ID)
    mock_redis.get.assert_called_once_with(
        f"{TENANT_ID}:session:{SESSION_ID}:status"
    )


# ── Outbound no-ops ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_deliver_text_is_noop(adapter, mock_redis, mock_producer):
    await adapter.deliver_text({"session_id": SESSION_ID, "text": "hi"})
    mock_producer.send_and_wait.assert_not_called()
    mock_redis.xadd.assert_not_called()


@pytest.mark.asyncio
async def test_deliver_menu_is_noop(adapter, mock_redis, mock_producer):
    await adapter.deliver_menu({"session_id": SESSION_ID})
    mock_producer.send_and_wait.assert_not_called()
    mock_redis.xadd.assert_not_called()


@pytest.mark.asyncio
async def test_deliver_typing_is_noop(adapter, mock_redis, mock_producer):
    await adapter.deliver_typing({"session_id": SESSION_ID})
    mock_producer.send_and_wait.assert_not_called()


@pytest.mark.asyncio
async def test_deliver_session_closed_is_noop(adapter, mock_redis, mock_producer):
    await adapter.deliver_session_closed({"session_id": SESSION_ID})
    mock_producer.send_and_wait.assert_not_called()
    mock_redis.xadd.assert_not_called()
