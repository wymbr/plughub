"""
context_reader.py
Reads the AI Gateway session state from Redis to populate context_snapshot
in each inbound event.
The AI Gateway writes session:{session_id}:ai after every turn.
Spec: channel-gateway-webchat.md — context_snapshot section
"""

from __future__ import annotations
import json
import logging

import redis.asyncio as aioredis

from .models import ContextSnapshot

logger = logging.getLogger("plughub.channel-gateway.context")


class ContextReader:
    def __init__(self, redis: aioredis.Redis) -> None:
        self._redis = redis

    async def get_snapshot(self, session_id: str) -> ContextSnapshot:
        """
        Read session:{session_id}:ai from Redis.
        Returns empty ContextSnapshot if not found (first turn or AI Gateway not yet written).
        """
        try:
            raw = await self._redis.get(f"session:{session_id}:ai")
            if not raw:
                return ContextSnapshot()
            data = json.loads(raw)
            return ContextSnapshot(
                intent=data.get("intent"),
                sentiment_score=data.get("sentiment_score"),
                turn_number=data.get("turn_count", 0),
            )
        except Exception as exc:
            logger.warning("Failed to read context snapshot for session=%s: %s", session_id, exc)
            return ContextSnapshot()
