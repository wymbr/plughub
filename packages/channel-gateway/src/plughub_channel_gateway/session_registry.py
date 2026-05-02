"""
session_registry.py
In-process registry of active WebSocket connections keyed by contact_id.
Also maintains session metadata in Redis for cross-instance awareness.

Pilot note: cross-instance delivery uses Redis pub/sub.
The local registry handles same-instance delivery (fast path).
Redis pub/sub handles delivery to other instances (fallback).
Spec: channel-gateway-webchat.md — Estado Redis section
"""

from __future__ import annotations
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Callable, Awaitable

import redis.asyncio as aioredis
from fastapi import WebSocket

logger = logging.getLogger("plughub.channel-gateway.sessions")


class SessionRegistry:
    """
    Manages active WebSocket connections.
    - Local dict: contact_id → WebSocket (same-instance fast path)
    - Redis: contact_id → {instance_id, connected_at} (cross-instance awareness)
    - Redis pub/sub: channel `chat:deliver:{contact_id}` for cross-instance delivery
    """

    def __init__(self, redis: aioredis.Redis, instance_id: str, ttl: int) -> None:
        self._redis = redis
        self._instance_id = instance_id
        self._ttl = ttl
        self._connections: dict[str, WebSocket] = {}
        self._started_at: dict[str, str] = {}
        # Two-level dict: contact_id → { menu_id → [masked_field_ids] }
        # Written by OutboundConsumer when delivering menu.payload with masked_fields.
        # Read+cleared by WebchatAdapter._handle_menu_submit to redact sensitive values
        # before they are stored in the conversation history visible to agents.
        self._menu_masked_fields: dict[str, dict[str, list[str]]] = {}

    # ── Registration ──────────────────────────────────────────────────────

    async def register(self, contact_id: str, ws: WebSocket) -> None:
        self._connections[contact_id] = ws
        self._started_at[contact_id] = datetime.now(timezone.utc).isoformat()
        await self._redis.setex(
            f"chat:session:{contact_id}",
            self._ttl,
            json.dumps({"instance_id": self._instance_id,
                        "connected_at": self._started_at[contact_id]}),
        )
        logger.info("contact_id=%s registered (instance=%s)", contact_id, self._instance_id)

    async def unregister(self, contact_id: str) -> str | None:
        """Returns started_at timestamp or None if not found."""
        self._connections.pop(contact_id, None)
        started_at = self._started_at.pop(contact_id, None)
        await self._redis.delete(f"chat:session:{contact_id}")
        logger.info("contact_id=%s unregistered", contact_id)
        return started_at

    # ── Delivery ──────────────────────────────────────────────────────────

    async def send(self, contact_id: str, payload: dict) -> bool:
        """
        Deliver payload to the WebSocket for contact_id.
        Returns True if delivered, False if contact not connected on this instance.
        Falls back to Redis pub/sub for cross-instance delivery.
        """
        ws = self._connections.get(contact_id)
        if ws is not None:
            try:
                await ws.send_json(payload)
                logger.info("delivered locally to contact_id=%s", contact_id)
                return True
            except Exception as exc:
                logger.warning("Failed to send to contact_id=%s: %s", contact_id, exc)
                await self.unregister(contact_id)
                return False

        # Cross-instance: publish to Redis channel
        # Log which contact_ids ARE registered so mismatches are visible.
        registered = list(self._connections.keys())
        logger.warning(
            "contact_id=%s not found locally (registered: %s) — falling to pub/sub",
            contact_id, registered,
        )
        await self._redis.publish(
            f"chat:deliver:{contact_id}",
            json.dumps(payload),
        )
        return False  # delivered via pub/sub, not locally

    async def close_connection(self, contact_id: str) -> None:
        """
        Close the WebSocket connection for a contact and remove from registry.
        Called by OutboundConsumer when session.closed arrives from the platform,
        so the customer's browser receives the close immediately without waiting
        for a heartbeat timeout.
        """
        ws = self._connections.pop(contact_id, None)
        self._started_at.pop(contact_id, None)
        await self._redis.delete(f"chat:session:{contact_id}")
        if ws:
            try:
                await ws.close()
            except Exception:
                pass
        logger.info("contact_id=%s connection closed by platform", contact_id)

    # ── Conversation history ──────────────────────────────────────────────────

    async def append_message(
        self,
        session_id: str,
        message_id: str,
        author:     str,
        text:       str,
        timestamp:  str,
    ) -> None:
        """
        Append a message to the conversation history list for this session.

        Key:    session:{session_id}:messages  (Redis List, RPUSH)
        TTL:    same as session TTL (renewed on every append)
        Format: { id, author, text, timestamp } — matches ChatMessage in agent-assist-ui.

        Written by:
          - WebchatAdapter._handle_text / _handle_menu_submit  → inbound (customer)
          - OutboundConsumer._dispatch message.text branch      → outbound (agent_ai / agent_human)

        Read by: mcp-server GET /conversation_history/:sessionId (LRANGE 0 -1)
        """
        entry = json.dumps({
            "id":        message_id,
            "author":    author,
            "text":      text,
            "timestamp": timestamp,
        })
        key = f"session:{session_id}:messages"
        try:
            await self._redis.rpush(key, entry)
            await self._redis.expire(key, self._ttl)
        except Exception as exc:
            logger.warning(
                "Failed to append message to history: session=%s — %s", session_id, exc
            )

    # ── Masked menu fields ────────────────────────────────────────────────────

    def store_menu_masked_fields(
        self,
        contact_id:    str,
        menu_id:       str,
        masked_fields: list[str],
    ) -> None:
        """
        Record which form fields are masked for a given menu interaction.
        Called by OutboundConsumer when delivering a menu.payload that carries
        masked_fields, so WebchatAdapter._handle_menu_submit can redact them
        before writing to the agent-visible conversation history.
        """
        if contact_id not in self._menu_masked_fields:
            self._menu_masked_fields[contact_id] = {}
        self._menu_masked_fields[contact_id][menu_id] = list(masked_fields)
        logger.debug(
            "stored masked_fields for contact_id=%s menu_id=%s fields=%s",
            contact_id, menu_id, masked_fields,
        )

    def pop_menu_masked_fields(self, contact_id: str, menu_id: str) -> list[str]:
        """
        Retrieve and remove the masked field list for a menu submission.
        Returns an empty list if no masked fields were registered (non-masked menu
        or if the key was already consumed by a previous submission).
        One-shot: the entry is deleted after the first read.
        """
        contact_menus = self._menu_masked_fields.get(contact_id)
        if not contact_menus:
            return []
        fields = contact_menus.pop(menu_id, [])
        if not contact_menus:
            # Clean up empty contact-level dict
            self._menu_masked_fields.pop(contact_id, None)
        return fields

    async def get_started_at(self, contact_id: str) -> str | None:
        return self._started_at.get(contact_id)

    def is_local(self, contact_id: str) -> bool:
        return contact_id in self._connections

    # ── Cross-instance subscriber ─────────────────────────────────────────

    async def start_pubsub_listener(self) -> None:
        """
        Subscribe to chat:deliver:* — delivers messages published by other instances.
        Runs as background task.
        """
        pubsub = self._redis.pubsub()
        await pubsub.psubscribe("chat:deliver:*")
        logger.info("pub/sub listener started (pattern: chat:deliver:*)")
        async for message in pubsub.listen():
            if message["type"] != "pmessage":
                continue
            try:
                # channel = "chat:deliver:{contact_id}" (str because decode_responses=True)
                channel: str = message["channel"]
                contact_id = channel.removeprefix("chat:deliver:")
                payload = json.loads(message["data"])
                ws = self._connections.get(contact_id)
                if ws:
                    await ws.send_json(payload)
            except Exception as exc:
                logger.warning("pub/sub delivery error: %s", exc)
