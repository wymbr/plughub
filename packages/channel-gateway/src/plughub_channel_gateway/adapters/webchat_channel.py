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
class.  Therefore deliver_text() and deliver_menu() do NOT send agent text or
menus to the WebSocket (deliver_text sends only SYSTEM messages, which the
widget's stream delivery does not map).  Typing indicators and session_closed DO
use the WebSocket because they are not written to the stream.  The history list
these methods used to maintain was retired in ALW-18 (2026-09-28): the Console
history is a projection of the stream.
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
        Webchat clients receive *agent* messages via their stream XREAD
        subscription (hybrid stream model), so no WebSocket send for those.

        Render v2 (queue-attended-model): SYSTEM messages (author.type ==
        "system" — routing engine: aviso de espera em fila muda e afins) are
        delivered directly via WS here. Since ALW-18 the routing-engine also records
        them in the stream as `system_notice` (for the Console history); the widget's
        stream delivery does not map that type, so there is still no duplication.
        """
        session_id  = payload.get("session_id", "")
        text        = payload.get("text", "")
        message_id  = payload.get("message_id", "")
        author_type = payload.get("author", {}).get("type", "agent_ai")
        timestamp   = payload.get("timestamp", "")
        if author_type == "system" and text:
            contact_id = payload.get("contact_id", "")
            try:
                await self._registry.send(contact_id, {
                    "type":       "msg.text",
                    "message_id": message_id,
                    "author":     {"type": "system", "role": "system",
                                   "name": "Sistema", "participant_id": "",
                                   "instance_id": ""},
                    "timestamp":  timestamp,
                    "text":       text,
                })
                logger.info(
                    "system text delivered via WS: contact_id=%s session=%s",
                    contact_id, session_id,
                )
            except Exception as exc:
                logger.warning(
                    "could not deliver system text: contact_id=%s — %s",
                    contact_id, exc,
                )

    async def deliver_menu(self, payload: dict) -> None:
        """
        Nothing to do: the interaction.request reaches the webchat client via the stream XREAD
        (hybrid stream model). Until ALW-18 (2026-09-28) this recorded the menu's masked fields
        so the history list could redact the answer; the list is gone — the Console history is a
        projection of the stream, where the bridge already writes the answer redacted.
        """
        logger.debug(
            "menu.payload skipped registry.send for webchat (hybrid stream model) "
            "contact_id=%s menu_id=%s",
            payload.get("contact_id", ""), payload.get("menu_id"),
        )

    async def deliver_typing(self, payload: dict) -> None:
        """Send an agent.typing WebSocket frame to the customer."""
        contact_id = payload.get("contact_id", "")
        ws_msg     = WsAgentTyping(author_type=payload.get("author_type", "agent_ai"))
        await self._registry.send(contact_id, ws_msg.model_dump())

    async def deliver_session_closed(self, payload: dict) -> None:
        """
        Send conn.session_ended and close the WebSocket.

        Render v2 (queue-attended-model): when the payload carries
        `farewell_text` (rejeição outage, timeout de fila muda, no-resource
        drop), render it as a message BEFORE the close frame — same WS
        delivery, race-free by construction (single payload, ordered sends).
        """
        contact_id = payload.get("contact_id", "")
        farewell   = payload.get("farewell_text") or ""
        if farewell:
            try:
                await self._registry.send(contact_id, {
                    "type":       "msg.text",
                    "message_id": "",
                    "author":     {"type": "system", "role": "system",
                                   "name": "Sistema", "participant_id": "",
                                   "instance_id": ""},
                    "timestamp":  "",
                    "text":       farewell,
                })
            except Exception as exc:
                logger.warning(
                    "could not deliver farewell: contact_id=%s — %s", contact_id, exc,
                )
        await self._registry.send(
            contact_id,
            WsSessionClosed(reason=payload.get("reason", "agent_done")).model_dump(),
        )
        await self._registry.close_connection(contact_id)
        logger.info("session.closed: notified and closed contact_id=%s", contact_id)
