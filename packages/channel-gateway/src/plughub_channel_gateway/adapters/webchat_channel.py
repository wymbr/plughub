"""
adapters/webchat_channel.py
Channel-level delivery singleton for the webchat channel.

Architecture: channel-gateway-multi-channel.md § 4 (WebSocketAdapter sub-protocol)

This singleton is registered in OutboundConsumer at startup and handles all
outbound delivery for the "webchat" channel.  Actual WebSocket send calls are
delegated to SessionRegistry, which maps contact_id → active WebSocket.

Note on the hybrid stream model
---------------------------------
Webchat clients receive *text messages* and *interaction.request* events via
their XREAD subscription on the canonical session stream — NOT from this
class.  Therefore deliver_text() and deliver_menu() do NOT send to the
WebSocket; they handle only the side-effects (history persistence and
masked_fields bookkeeping respectively).  Typing indicators and session_closed
DO use the WebSocket because they are not written to the stream.
"""

from __future__ import annotations

import logging

from ..models import WsAgentTyping, WsSessionClosed
from ..session_registry import SessionRegistry
from .base import ChannelAdapter

logger = logging.getLogger("plughub.channel-gateway.webchat-channel")


class WebchatChannelAdapter(ChannelAdapter):
    """
    Outbound delivery singleton for the webchat channel.

    Args:
        registry:  SessionRegistry that maps contact_id → WebSocket connection.
    """

    channel = "webchat"

    def __init__(self, registry: SessionRegistry) -> None:
        self._registry = registry

    # ── ChannelAdapter interface ───────────────────────────────────────────────

    async def deliver_text(self, payload: dict) -> None:
        """
        Persist the outbound text to conversation history.

        Webchat clients receive the message directly via their stream XREAD
        subscription (hybrid stream model), so no WebSocket send is needed here.
        """
        session_id  = payload.get("session_id", "")
        text        = payload.get("text", "")
        message_id  = payload.get("message_id", "")
        author_type = payload.get("author", {}).get("type", "agent_ai")
        timestamp   = payload.get("timestamp", "")
        if session_id and text:
            await self._registry.append_message(
                session_id = session_id,
                message_id = message_id,
                author     = author_type,
                text       = text,
                timestamp  = timestamp,
            )

    async def deliver_menu(self, payload: dict) -> None:
        """
        Register masked_fields for the pending menu so WebchatAdapter can
        redact sensitive values when the customer submits the form.

        As with deliver_text, the actual interaction.request event reaches the
        webchat client via the stream XREAD — no WebSocket send here.
        """
        contact_id    = payload.get("contact_id", "")
        masked_fields: list[str] | None = payload.get("masked_fields") or None

        if masked_fields and payload.get("channel", "webchat") != "webchat":
            # Should not happen — OutboundConsumer routes by channel — but guard
            # defensively.
            logger.warning(
                "deliver_menu called with masked_fields for non-webchat channel "
                "contact_id=%s channel=%s",
                contact_id, payload.get("channel"),
            )
            return

        if masked_fields:
            self._registry.store_menu_masked_fields(
                contact_id, payload.get("menu_id", ""), masked_fields
            )
            logger.debug(
                "stored masked_fields contact_id=%s menu_id=%s fields=%s",
                contact_id, payload.get("menu_id"), masked_fields,
            )
        else:
            logger.debug(
                "menu.payload skipped registry.send for webchat (hybrid stream model) "
                "contact_id=%s menu_id=%s",
                contact_id, payload.get("menu_id"),
            )

    async def deliver_typing(self, payload: dict) -> None:
        """Send an agent.typing WebSocket frame to the customer."""
        contact_id = payload.get("contact_id", "")
        ws_msg     = WsAgentTyping(author_type=payload.get("author_type", "agent_ai"))
        await self._registry.send(contact_id, ws_msg.model_dump())

    async def deliver_session_closed(self, payload: dict) -> None:
        """Send conn.session_ended and close the WebSocket."""
        contact_id = payload.get("contact_id", "")
        await self._registry.send(
            contact_id,
            WsSessionClosed(reason=payload.get("reason", "agent_done")).model_dump(),
        )
        await self._registry.close_connection(contact_id)
        logger.info("session.closed: notified and closed contact_id=%s", contact_id)
