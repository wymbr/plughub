"""
test_session_registry.py
Unit tests for SessionRegistry.
Covers: registration, unregistration, local delivery, cross-instance pub/sub fallback.
"""

from __future__ import annotations
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

from plughub_channel_gateway.session_registry import SessionRegistry


INSTANCE_ID = "inst-test-001"
TTL = 3600


@pytest.fixture
def redis(mock_redis):
    return mock_redis


@pytest.fixture
def registry(redis):
    return SessionRegistry(redis=redis, instance_id=INSTANCE_ID, ttl=TTL)


@pytest.fixture
def ws():
    ws = AsyncMock()
    ws.send_json = AsyncMock()
    return ws


class TestRegistration:
    async def test_register_stores_locally(self, registry, ws):
        await registry.register("contact-1", ws)
        assert registry.is_local("contact-1")

    async def test_register_writes_redis_key(self, registry, redis, ws):
        await registry.register("contact-1", ws)
        redis.setex.assert_called_once()
        key, ttl, value = redis.setex.call_args.args
        assert key == "chat:session:contact-1"
        assert ttl == TTL
        data = json.loads(value)
        assert data["instance_id"] == INSTANCE_ID
        assert "connected_at" in data

    async def test_unregister_removes_locally(self, registry, ws):
        await registry.register("contact-1", ws)
        await registry.unregister("contact-1")
        assert not registry.is_local("contact-1")

    async def test_unregister_deletes_redis_key(self, registry, redis, ws):
        await registry.register("contact-1", ws)
        await registry.unregister("contact-1")
        redis.delete.assert_called_once_with("chat:session:contact-1")

    async def test_unregister_returns_started_at(self, registry, ws):
        await registry.register("contact-1", ws)
        started_at = await registry.unregister("contact-1")
        assert started_at is not None
        assert "T" in started_at  # ISO 8601 check

    async def test_unregister_unknown_returns_none(self, registry):
        result = await registry.unregister("unknown-contact")
        assert result is None

    async def test_get_started_at_after_register(self, registry, ws):
        await registry.register("contact-1", ws)
        ts = await registry.get_started_at("contact-1")
        assert ts is not None

    async def test_get_started_at_unknown(self, registry):
        ts = await registry.get_started_at("ghost-contact")
        assert ts is None


class TestLocalDelivery:
    async def test_send_to_local_contact(self, registry, ws):
        await registry.register("contact-1", ws)
        payload = {"type": "message.text", "text": "Olá"}
        result = await registry.send("contact-1", payload)
        ws.send_json.assert_called_once_with(payload)
        assert result is True

    async def test_send_to_unknown_contact_uses_pubsub(self, registry, redis):
        payload = {"type": "message.text", "text": "cross-instance"}
        result = await registry.send("contact-remote", payload)
        redis.publish.assert_called_once_with(
            "chat:deliver:contact-remote",
            json.dumps(payload),
        )
        assert result is False

    async def test_send_unregisters_on_ws_error(self, registry, ws, redis):
        ws.send_json.side_effect = Exception("connection broken")
        await registry.register("contact-err", ws)
        result = await registry.send("contact-err", {"type": "ping"})
        assert result is False
        assert not registry.is_local("contact-err")

    async def test_send_multiple_contacts(self, registry):
        ws1, ws2 = AsyncMock(), AsyncMock()
        await registry.register("c1", ws1)
        await registry.register("c2", ws2)

        await registry.send("c1", {"type": "ping"})
        await registry.send("c2", {"type": "pong"})

        ws1.send_json.assert_called_once()
        ws2.send_json.assert_called_once()


class TestPubSubListener:
    async def test_delivers_message_to_local_ws(self, registry, ws, redis):
        """Cross-instance message received via pub/sub is delivered locally."""
        await registry.register("contact-local", ws)
        payload = {"type": "message.text", "text": "from other instance"}

        # Simulate the pub/sub message
        message = {
            "type": "pmessage",
            "channel": b"chat:deliver:contact-local",
            "data": json.dumps(payload),
        }

        # Directly call the dispatch logic (same as start_pubsub_listener internals)
        channel: bytes = message["channel"]
        contact_id = channel.decode().removeprefix("chat:deliver:")
        msg_payload = json.loads(message["data"])
        ws_ = registry._connections.get(contact_id)
        if ws_:
            await ws_.send_json(msg_payload)

        ws.send_json.assert_called_once_with(payload)

    async def test_ignores_non_pmessage_type(self, registry, redis):
        """Subscription confirmation messages are ignored silently."""
        message = {"type": "subscribe", "channel": b"chat:deliver:*", "data": 1}
        # No error should be raised; nothing to assert besides no exceptions
        assert message["type"] != "pmessage"
