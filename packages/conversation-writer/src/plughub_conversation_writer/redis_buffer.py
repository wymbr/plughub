"""
redis_buffer.py
Redis buffer for in-flight transcript messages.

Keys:
  transcript:{contact_id}      — list of JSON-encoded InboundMessage objects
  contact_meta:{contact_id}    — JSON ContactMeta (lifecycle events)

Both keys use the same TTL (transcript_ttl_seconds) as protection against
contacts that never receive a contact_closed event.

Spec: conversation-writer.md — Acumulação em Redis section
"""

from __future__ import annotations
import json
import logging

import redis.asyncio as aioredis

from .models import ContactMeta, InboundMessage

logger = logging.getLogger("plughub.conversation-writer.redis")

TRANSCRIPT_KEY = "transcript:{contact_id}"
META_KEY = "contact_meta:{contact_id}"


class RedisBuffer:
    def __init__(self, redis: aioredis.Redis, ttl: int) -> None:
        self._redis = redis
        self._ttl = ttl

    # ── Message buffer ────────────────────────────────────────────────────────

    async def append_message(self, msg: InboundMessage) -> None:
        """RPUSH one message to transcript:{contact_id}. Refreshes TTL."""
        key = TRANSCRIPT_KEY.format(contact_id=msg.contact_id)
        await self._redis.rpush(key, msg.model_dump_json())
        await self._redis.expire(key, self._ttl)

    async def get_messages(self, contact_id: str) -> list[InboundMessage]:
        """LRANGE all messages for a contact. Returns empty list on miss."""
        key = TRANSCRIPT_KEY.format(contact_id=contact_id)
        try:
            raw_list = await self._redis.lrange(key, 0, -1)
            return [InboundMessage.model_validate_json(r) for r in raw_list]
        except Exception as exc:
            logger.warning("Failed to read messages for contact_id=%s: %s", contact_id, exc)
            return []

    async def delete_messages(self, contact_id: str) -> None:
        key = TRANSCRIPT_KEY.format(contact_id=contact_id)
        await self._redis.delete(key)

    # ── Contact metadata ──────────────────────────────────────────────────────

    async def upsert_meta(self, contact_id: str, **fields) -> None:
        """
        Merge *fields* into existing ContactMeta for the contact.
        Creates a new record if none exists.
        """
        key = META_KEY.format(contact_id=contact_id)
        try:
            raw = await self._redis.get(key)
            if raw:
                data = json.loads(raw)
                data.update({k: v for k, v in fields.items() if v is not None})
            else:
                data = {"contact_id": contact_id, **{k: v for k, v in fields.items() if v is not None}}
            await self._redis.setex(key, self._ttl, json.dumps(data))
        except Exception as exc:
            logger.warning("Failed to upsert meta for contact_id=%s: %s", contact_id, exc)

    async def get_meta(self, contact_id: str) -> ContactMeta:
        """Returns ContactMeta or a minimal stub if not found."""
        key = META_KEY.format(contact_id=contact_id)
        try:
            raw = await self._redis.get(key)
            if raw:
                return ContactMeta.model_validate_json(raw)
        except Exception as exc:
            logger.warning("Failed to read meta for contact_id=%s: %s", contact_id, exc)
        return ContactMeta(contact_id=contact_id)

    async def delete_meta(self, contact_id: str) -> None:
        key = META_KEY.format(contact_id=contact_id)
        await self._redis.delete(key)

    # ── Cleanup ───────────────────────────────────────────────────────────────

    async def cleanup(self, contact_id: str) -> None:
        """Delete both keys after successful persistence."""
        await self.delete_messages(contact_id)
        await self.delete_meta(contact_id)
