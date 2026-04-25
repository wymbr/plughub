"""
test_redis_buffer.py
Unit tests for RedisBuffer.
"""

from __future__ import annotations
import json
import pytest
from unittest.mock import AsyncMock, call

from plughub_conversation_writer.models import InboundMessage
from plughub_conversation_writer.redis_buffer import RedisBuffer

from .conftest import make_message

TTL = 3600


@pytest.fixture
def buffer(mock_redis):
    return RedisBuffer(redis=mock_redis, ttl=TTL)


class TestAppendMessage:
    async def test_rpush_to_correct_key(self, buffer, mock_redis):
        msg = InboundMessage.model_validate(make_message(contact_id="cid-abc"))
        await buffer.append_message(msg)
        mock_redis.rpush.assert_called_once()
        key, _ = mock_redis.rpush.call_args.args
        assert key == "transcript:cid-abc"

    async def test_rpush_json_encoded(self, buffer, mock_redis):
        msg = InboundMessage.model_validate(make_message(contact_id="cid-abc", text="Teste"))
        await buffer.append_message(msg)
        _, raw = mock_redis.rpush.call_args.args
        parsed = json.loads(raw)
        assert parsed["contact_id"] == "cid-abc"
        assert parsed["content"]["text"] == "Teste"

    async def test_expire_set_after_push(self, buffer, mock_redis):
        msg = InboundMessage.model_validate(make_message(contact_id="cid-abc"))
        await buffer.append_message(msg)
        mock_redis.expire.assert_called_once_with("transcript:cid-abc", TTL)


class TestGetMessages:
    async def test_empty_list_when_no_messages(self, buffer, mock_redis):
        mock_redis.lrange.return_value = []
        msgs = await buffer.get_messages("cid-empty")
        assert msgs == []

    async def test_parses_stored_messages(self, buffer, mock_redis):
        raw = [json.dumps(make_message(contact_id="cid-1", text="msg1")).encode()]
        mock_redis.lrange.return_value = raw
        msgs = await buffer.get_messages("cid-1")
        assert len(msgs) == 1
        assert msgs[0].content.text == "msg1"

    async def test_parses_multiple_messages(self, buffer, mock_redis):
        raws = [json.dumps(make_message(contact_id="cid-1", text=f"msg{i}")).encode()
                for i in range(5)]
        mock_redis.lrange.return_value = raws
        msgs = await buffer.get_messages("cid-1")
        assert len(msgs) == 5

    async def test_returns_empty_on_redis_error(self, buffer, mock_redis):
        mock_redis.lrange.side_effect = Exception("connection lost")
        msgs = await buffer.get_messages("cid-err")
        assert msgs == []

    async def test_lrange_uses_correct_key(self, buffer, mock_redis):
        mock_redis.lrange.return_value = []
        await buffer.get_messages("my-contact")
        mock_redis.lrange.assert_called_once_with("transcript:my-contact", 0, -1)


class TestDeleteMessages:
    async def test_deletes_correct_key(self, buffer, mock_redis):
        await buffer.delete_messages("cid-del")
        mock_redis.delete.assert_called_with("transcript:cid-del")


class TestMeta:
    async def test_upsert_meta_creates_new(self, buffer, mock_redis):
        mock_redis.get.return_value = None
        await buffer.upsert_meta("cid-1", pool_id="retencao_humano", agent_type="human")
        mock_redis.setex.assert_called_once()
        key, ttl, raw = mock_redis.setex.call_args.args
        assert key == "contact_meta:cid-1"
        data = json.loads(raw)
        assert data["pool_id"] == "retencao_humano"
        assert data["agent_type"] == "human"

    async def test_upsert_meta_merges_existing(self, buffer, mock_redis):
        existing = json.dumps({"contact_id": "cid-1", "pool_id": "pool_a"})
        mock_redis.get.return_value = existing
        await buffer.upsert_meta("cid-1", agent_id="agent-123")
        _, _, raw = mock_redis.setex.call_args.args
        data = json.loads(raw)
        assert data["pool_id"] == "pool_a"      # preserved
        assert data["agent_id"] == "agent-123"  # added

    async def test_upsert_meta_ignores_none_values(self, buffer, mock_redis):
        mock_redis.get.return_value = None
        await buffer.upsert_meta("cid-1", pool_id="pool_a", agent_id=None)
        _, _, raw = mock_redis.setex.call_args.args
        data = json.loads(raw)
        assert "agent_id" not in data

    async def test_get_meta_returns_stub_on_miss(self, buffer, mock_redis):
        mock_redis.get.return_value = None
        meta = await buffer.get_meta("ghost")
        assert meta.contact_id == "ghost"
        assert meta.pool_id is None

    async def test_get_meta_parses_stored(self, buffer, mock_redis):
        mock_redis.get.return_value = json.dumps({
            "contact_id": "cid-1",
            "pool_id": "retencao_humano",
            "agent_type": "ai",
        })
        meta = await buffer.get_meta("cid-1")
        assert meta.pool_id == "retencao_humano"
        assert meta.agent_type == "ai"

    async def test_get_meta_returns_stub_on_error(self, buffer, mock_redis):
        mock_redis.get.side_effect = Exception("redis down")
        meta = await buffer.get_meta("cid-err")
        assert meta.contact_id == "cid-err"


class TestCleanup:
    async def test_cleanup_deletes_both_keys(self, buffer, mock_redis):
        await buffer.cleanup("cid-x")
        deleted_keys = [c.args[0] for c in mock_redis.delete.call_args_list]
        assert "transcript:cid-x" in deleted_keys
        assert "contact_meta:cid-x" in deleted_keys
