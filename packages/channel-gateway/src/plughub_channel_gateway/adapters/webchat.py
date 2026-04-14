"""
adapters/webchat.py
WebSocket adapter for the web chat channel.
Handles the full lifecycle of a webchat contact:
  - WebSocket connection → contact_open
  - Inbound messages/menu submits → conversations.inbound
  - Outbound messages/menus from conversations.outbound → WebSocket
  - WebSocket disconnect → contact_closed

Spec: PlugHub v24.0 section 3.5 / channel-gateway-webchat.md
"""

from __future__ import annotations
import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import WebSocket, WebSocketDisconnect
from aiokafka import AIOKafkaProducer

from ..config import Settings
from ..context_reader import ContextReader
from ..models import (
    NormalizedInboundEvent, MessageAuthor, MessageContent, ContextSnapshot,
    ContactOpenEvent, ContactClosedEvent,
    WsConnectionAccepted, WsMessageText, WsMenuSubmit,
)
from ..session_registry import SessionRegistry

logger = logging.getLogger("plughub.channel-gateway.webchat")


class WebchatAdapter:
    """
    Handles a single WebSocket connection for the duration of a contact.
    One instance per active connection.
    """

    def __init__(
        self,
        ws:               WebSocket,
        contact_id:       str,
        session_id:       str,
        producer:         AIOKafkaProducer,
        registry:         SessionRegistry,
        context_reader:   ContextReader,
        settings:         Settings,
    ) -> None:
        self._ws             = ws
        self._contact_id     = contact_id
        self._session_id     = session_id
        self._producer       = producer
        self._registry       = registry
        self._context_reader = context_reader
        self._settings       = settings
        self._started_at     = datetime.now(timezone.utc).isoformat()

    # ── Lifecycle ─────────────────────────────────────────────────────────

    async def handle(self) -> None:
        await self._ws.accept()
        await self._registry.register(self._contact_id, self._ws)

        # Announce connection to client
        await self._ws.send_json(
            WsConnectionAccepted(
                contact_id=self._contact_id,
                session_id=self._session_id,
            ).model_dump()
        )

        tenant_id = self._settings.tenant_id

        # Store session metadata in Redis so MCP tools (notification_send, conversation_escalate)
        # can look up contact_id, tenant_id, pool_id, and channel from session_id.
        # Keys:
        #   session:{session_id}:contact_id  — fast lookup (used by notification_send)
        #   session:{session_id}:meta        — full metadata JSON (used by conversation_escalate)
        ttl = self._settings.session_ttl_seconds
        await self._registry._redis.setex(
            f"session:{self._session_id}:contact_id",
            ttl,
            self._contact_id,
        )
        await self._registry._redis.setex(
            f"session:{self._session_id}:meta",
            ttl,
            json.dumps({
                "contact_id":  self._contact_id,
                "session_id":  self._session_id,
                "tenant_id":   tenant_id,
                "customer_id": self._contact_id,
                "channel":     "chat",
                "pool_id":     self._settings.entry_point_pool_id,
                "started_at":  self._started_at,
            }),
        )

        # Announce contact lifecycle to platform
        await self._publish_event(
            ContactOpenEvent(
                contact_id=self._contact_id,
                session_id=self._session_id,
                started_at=self._started_at,
            ).model_dump()
        )
        logger.info("contact_open contact_id=%s session_id=%s", self._contact_id, self._session_id)

        # Publish routing event so the Routing Engine allocates an agent immediately.
        # pool_id comes from the entry point configuration (PLUGHUB_ENTRY_POINT_POOL_ID).
        # If not configured, the routing event is omitted — useful for channels that
        # require a menu/IVR step before routing (pool determined by customer choice).
        if self._settings.entry_point_pool_id:
            routing_event = {
                "session_id":  self._session_id,
                "tenant_id":   tenant_id,
                "customer_id": self._contact_id,
                "channel":     "chat",
                "pool_id":     self._settings.entry_point_pool_id,
                "started_at":  self._started_at,
                "elapsed_ms":  0,
            }
            await self._publish_inbound(routing_event)
            logger.info(
                "routing_event published: session=%s pool=%s",
                self._session_id, self._settings.entry_point_pool_id,
            )
        else:
            logger.warning(
                "PLUGHUB_ENTRY_POINT_POOL_ID not configured — routing event not published "
                "for session=%s. Set this env var to enable automatic routing on connect.",
                self._session_id,
            )

        try:
            await self._receive_loop()
        except WebSocketDisconnect:
            logger.info("client_disconnect contact_id=%s", self._contact_id)
            await self._close(reason="client_disconnect")
        except asyncio.TimeoutError:
            logger.info("timeout contact_id=%s", self._contact_id)
            await self._close(reason="timeout")
        except Exception as exc:
            logger.error("error in webchat handler contact_id=%s: %s", self._contact_id, exc)
            await self._close(reason="client_disconnect")

    async def close_from_platform(self, reason: str = "agent_done") -> None:
        """Called by the outbound consumer when session.closed arrives from platform."""
        await self._close(reason=reason)
        try:
            await self._ws.close()
        except Exception:
            pass

    # ── Inbound loop ──────────────────────────────────────────────────────

    async def _receive_loop(self) -> None:
        timeout = self._settings.ws_connection_timeout_s
        while True:
            try:
                raw = await asyncio.wait_for(
                    self._ws.receive_text(),
                    timeout=float(timeout),
                )
            except asyncio.TimeoutError:
                # Send ping to check liveness before giving up
                try:
                    await self._ws.send_json({"type": "ping"})
                    raw = await asyncio.wait_for(
                        self._ws.receive_text(),
                        timeout=10.0,
                    )
                except Exception:
                    raise asyncio.TimeoutError

            data = json.loads(raw)
            msg_type = data.get("type")

            if msg_type == "message.text":
                await self._handle_text(WsMessageText.model_validate(data))
            elif msg_type == "menu.submit":
                await self._handle_menu_submit(WsMenuSubmit.model_validate(data))
            elif msg_type == "pong":
                pass  # heartbeat response
            else:
                logger.warning("unknown message type=%s contact_id=%s", msg_type, self._contact_id)

    async def _handle_text(self, msg: WsMessageText) -> None:
        snapshot = await self._context_reader.get_snapshot(self._session_id)
        event = NormalizedInboundEvent(
            contact_id=self._contact_id,
            session_id=self._session_id,
            author=MessageAuthor(type="customer"),
            content=MessageContent(type="text", text=msg.text),
            context_snapshot=snapshot,
        )
        # Persist to conversation history before publishing so the record exists
        # even if downstream consumers are temporarily unavailable.
        await self._registry.append_message(
            session_id=self._session_id,
            message_id=event.message_id,
            author="customer",
            text=msg.text,
            timestamp=event.timestamp,
        )
        await self._publish_inbound(event.model_dump())
        logger.info(
            "inbound text published: contact_id=%s session_id=%s turn=%s",
            self._contact_id, self._session_id, snapshot.turn_number,
        )

    async def _handle_menu_submit(self, msg: WsMenuSubmit) -> None:
        snapshot = await self._context_reader.get_snapshot(self._session_id)
        event = NormalizedInboundEvent(
            contact_id=self._contact_id,
            session_id=self._session_id,
            author=MessageAuthor(type="customer"),
            content=MessageContent(
                type="menu_result",
                payload={
                    "menu_id":     msg.menu_id,
                    "interaction": msg.interaction,
                    "result":      msg.result,
                },
            ),
            context_snapshot=snapshot,
        )
        # Serialise result to a readable string for the history display.
        # Mirrors the format the bridge uses when forwarding to human agents.
        result_str = (
            msg.result if isinstance(msg.result, str)
            else json.dumps(msg.result, ensure_ascii=False)
        )
        await self._registry.append_message(
            session_id=self._session_id,
            message_id=event.message_id,
            author="customer",
            text=f"[Seleção: {result_str}]",
            timestamp=event.timestamp,
        )
        await self._publish_inbound(event.model_dump())
        logger.debug("menu_submit interaction=%s contact_id=%s", msg.interaction, self._contact_id)

    # ── Close ─────────────────────────────────────────────────────────────

    async def _close(self, reason: str) -> None:
        started_at = await self._registry.unregister(self._contact_id)
        await self._publish_event(
            ContactClosedEvent(
                contact_id=self._contact_id,
                session_id=self._session_id,
                reason=reason,  # type: ignore[arg-type]
                started_at=started_at or self._started_at,
            ).model_dump()
        )
        logger.info(
            "contact_closed contact_id=%s reason=%s",
            self._contact_id, reason,
        )

    # ── Kafka helpers ─────────────────────────────────────────────────────

    async def _publish_inbound(self, payload: dict) -> None:
        await self._producer.send(
            self._settings.kafka_topic_inbound,
            value=json.dumps(payload).encode(),
        )

    async def _publish_event(self, payload: dict) -> None:
        await self._producer.send(
            self._settings.kafka_topic_events,
            value=json.dumps(payload).encode(),
        )
