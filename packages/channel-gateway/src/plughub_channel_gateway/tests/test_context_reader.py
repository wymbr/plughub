"""
test_context_reader.py
Unit tests for ContextReader.
Verifies Redis key resolution and graceful fallback on miss/error.
"""

from __future__ import annotations
import json
import pytest
from unittest.mock import AsyncMock

from plughub_channel_gateway.context_reader import ContextReader
from plughub_channel_gateway.models import ContextSnapshot


@pytest.fixture
def redis():
    r = AsyncMock()
    r.get = AsyncMock(return_value=None)
    return r


@pytest.fixture
def reader(redis):
    return ContextReader(redis=redis)


class TestContextReader:
    async def test_returns_empty_snapshot_when_key_missing(self, reader, redis):
        redis.get.return_value = None
        snap = await reader.get_snapshot("session-123")
        assert isinstance(snap, ContextSnapshot)
        assert snap.intent is None
        assert snap.sentiment_score is None
        assert snap.turn_number == 0

    async def test_reads_correct_redis_key(self, reader, redis):
        redis.get.return_value = None
        await reader.get_snapshot("ses-abc")
        redis.get.assert_called_once_with("session:ses-abc:ai")

    async def test_parses_full_snapshot(self, reader, redis):
        redis.get.return_value = json.dumps({
            "intent": "portability_check",
            "sentiment_score": 0.75,
            "turn_count": 4,
        })
        snap = await reader.get_snapshot("ses-abc")
        assert snap.intent == "portability_check"
        assert snap.sentiment_score == 0.75
        assert snap.turn_number == 4

    async def test_parses_partial_snapshot(self, reader, redis):
        redis.get.return_value = json.dumps({"intent": "churn_risk"})
        snap = await reader.get_snapshot("ses-partial")
        assert snap.intent == "churn_risk"
        assert snap.sentiment_score is None
        assert snap.turn_number == 0  # defaults to 0 when turn_count absent

    async def test_returns_empty_on_redis_error(self, reader, redis):
        redis.get.side_effect = Exception("connection refused")
        snap = await reader.get_snapshot("ses-error")
        assert isinstance(snap, ContextSnapshot)
        assert snap.intent is None

    async def test_returns_empty_on_invalid_json(self, reader, redis):
        redis.get.return_value = "not-valid-json{"
        snap = await reader.get_snapshot("ses-bad-json")
        assert isinstance(snap, ContextSnapshot)
        assert snap.turn_number == 0

    async def test_zero_turn_count(self, reader, redis):
        redis.get.return_value = json.dumps({
            "intent": "general_inquiry",
            "sentiment_score": 0.5,
            "turn_count": 0,
        })
        snap = await reader.get_snapshot("ses-zero")
        assert snap.turn_number == 0
