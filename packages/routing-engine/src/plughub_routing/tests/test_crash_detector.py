"""
test_crash_detector.py
Unit tests for CrashDetector — crash recovery and active session detection.

Coverage:
  - Crashed instances (no instance key in Redis) trigger requeue
  - Active conversations with engine lock are NOT re-queued (no false positive)
  - Active conversations with activity_flag are NOT re-queued (B2-03: BLPOP wait)
  - Conversations with both flags absent are re-queued (genuine crash)
"""
from __future__ import annotations

import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, AsyncMock, call
from datetime import datetime, timezone

from plughub_routing.crash_detector import CrashDetector
from plughub_routing.models import InstanceMeta


# ─── Helpers ──────────────────────────────────────────────────────────────────

TENANT  = "tenant_demo"
INST    = "instance_001"
CONV    = "conv_001"


def make_meta(conversations: list[str] = None, pools: list[str] = None) -> InstanceMeta:
    return InstanceMeta(
        pools                = pools or ["pool_demo"],
        agent_type_id        = "agente_demo_ia_v1",
        active_conversations = conversations or [CONV],
    )


def make_detector(redis_mock, instance_registry_mock, producer_mock=None):
    if producer_mock is None:
        producer_mock = AsyncMock()
        producer_mock.send = AsyncMock(return_value=None)
    return CrashDetector(
        redis_client      = redis_mock,
        instance_registry = instance_registry_mock,
        kafka_producer    = producer_mock,
    )


# ─── TestHandleCrash ──────────────────────────────────────────────────────────

class TestHandleCrash:
    async def test_requeues_genuine_crash(self):
        """
        Conversation without engine lock AND without activity flag must be re-queued.
        """
        redis = AsyncMock()
        # exists() returns 0 for both lock_key and activity_key → genuine crash
        redis.exists = AsyncMock(return_value=0)
        redis.srem   = AsyncMock(return_value=1)

        registry = AsyncMock()
        registry.get_instance_meta     = AsyncMock(return_value=make_meta())
        registry.delete_instance_meta  = AsyncMock()

        producer = AsyncMock()
        producer.send = AsyncMock(return_value=None)

        detector = make_detector(redis, registry, producer)
        await detector._handle_crash(TENANT, INST)

        # conversations.inbound must have been published
        producer.send.assert_called()
        call_args = producer.send.call_args_list
        inbound_calls = [c for c in call_args if "inbound" in str(c)]
        assert len(inbound_calls) >= 1

    async def test_skips_conversation_with_engine_lock(self):
        """
        Conversations with active pipeline:running lock must NOT be re-queued.
        """
        redis = AsyncMock()
        redis.srem = AsyncMock(return_value=1)

        def exists_side_effect(key):
            if "pipeline" in key and "running" in key:
                return 1  # engine lock exists
            return 0  # activity flag does not

        redis.exists = AsyncMock(side_effect=exists_side_effect)

        registry = AsyncMock()
        registry.get_instance_meta     = AsyncMock(return_value=make_meta())
        registry.delete_instance_meta  = AsyncMock()

        producer = AsyncMock()
        producer.send = AsyncMock(return_value=None)

        detector = make_detector(redis, registry, producer)
        await detector._handle_crash(TENANT, INST)

        # Only lifecycle event published, NOT conversations.inbound
        lifecycle_calls = [
            c for c in producer.send.call_args_list
            if c.args and "lifecycle" in str(c.args[0])
        ]
        assert len(lifecycle_calls) >= 1
        # conversations.inbound must NOT have been called for CONV
        inbound_calls = [
            c for c in producer.send.call_args_list
            if c.kwargs.get("value", {}).get("session_id") == CONV
        ]
        assert len(inbound_calls) == 0

    async def test_skips_conversation_with_activity_flag(self):
        """
        Conversations with active_instance flag must not be re-queued (B2-03).
        An agent blocked in BLPOP (menu/collect wait) sets this flag but NOT
        the pipeline lock — CrashDetector must treat it as still active.
        """
        redis = AsyncMock()
        redis.srem = AsyncMock(return_value=1)

        def exists_side_effect(key):
            if "active_instance" in key:
                return 1  # activity flag exists (agent is in BLPOP)
            return 0       # pipeline lock does not exist

        redis.exists = AsyncMock(side_effect=exists_side_effect)

        registry = AsyncMock()
        registry.get_instance_meta    = AsyncMock(return_value=make_meta())
        registry.delete_instance_meta = AsyncMock()

        producer = AsyncMock()
        producer.send = AsyncMock(return_value=None)

        detector = make_detector(redis, registry, producer)
        await detector._handle_crash(TENANT, INST)

        # conversations.inbound must NOT have been called for CONV
        inbound_calls = [
            c for c in producer.send.call_args_list
            if c.kwargs.get("value", {}).get("session_id") == CONV
        ]
        assert len(inbound_calls) == 0

    async def test_no_meta_only_cleans_pools(self):
        """Instance without InstanceMeta only removes from pool sets."""
        redis = AsyncMock()
        redis.srem       = AsyncMock(return_value=1)
        redis.scan_iter  = AsyncMock(return_value=_async_iter([]))

        registry = AsyncMock()
        registry.get_instance_meta = AsyncMock(return_value=None)

        producer = AsyncMock()
        producer.send = AsyncMock(return_value=None)

        detector = make_detector(redis, registry, producer)
        # Should not raise
        await detector._handle_crash(TENANT, INST)
        # Producer not called — no conversations to requeue
        producer.send.assert_not_called()


# ─── Async iterator helper ────────────────────────────────────────────────────

async def _async_iter(items):
    for item in items:
        yield item
