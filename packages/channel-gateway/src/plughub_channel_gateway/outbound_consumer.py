"""
outbound_consumer.py
Kafka consumer for conversations.outbound.
Delivers messages and menus to clients via WebSocket,
and closes sessions when session.closed arrives.
Spec: channel-gateway-webchat.md — Consumo de conversations.outbound section
"""

from __future__ import annotations
import asyncio
import json
import logging

from aiokafka import AIOKafkaConsumer

from .config import Settings
from .models import WsMessageOutbound, WsMenuRender, WsAgentTyping, WsSessionClosed
from .session_registry import SessionRegistry

logger = logging.getLogger("plughub.channel-gateway.outbound")


class OutboundConsumer:
    def __init__(
        self,
        registry: SessionRegistry,
        settings: Settings,
    ) -> None:
        self._registry = registry
        self._settings = settings

    async def run(self) -> None:
        consumer = AIOKafkaConsumer(
            self._settings.kafka_topic_outbound,
            bootstrap_servers=self._settings.kafka_brokers,
            group_id=self._settings.kafka_group_id,
            auto_offset_reset="latest",
        )
        await consumer.start()
        logger.info("outbound consumer started — topic=%s", self._settings.kafka_topic_outbound)

        try:
            async for msg in consumer:
                asyncio.create_task(self._dispatch(json.loads(msg.value.decode())))
        finally:
            await consumer.stop()

    async def _dispatch(self, payload: dict) -> None:
        msg_type    = payload.get("type")
        contact_id  = payload.get("contact_id")
        channel     = payload.get("channel")

        # Only process chat messages
        if channel != "chat" or not contact_id:
            return

        try:
            if msg_type == "message.text":
                ws_msg = WsMessageOutbound(
                    message_id=payload.get("message_id", ""),
                    author=payload.get("author", {}),
                    text=payload.get("text", ""),
                    timestamp=payload.get("timestamp", ""),
                )
                await self._registry.send(contact_id, ws_msg.model_dump())

                # Persist to conversation history.
                # Both AI (notification_send tool) and human (mcp-server WS handler)
                # outbound messages arrive here with the same envelope format, so this
                # single point captures all outbound regardless of agent type.
                session_id  = payload.get("session_id", "")
                author_type = payload.get("author", {}).get("type", "agent_ai")
                if session_id and ws_msg.text:
                    await self._registry.append_message(
                        session_id=session_id,
                        message_id=ws_msg.message_id,
                        author=author_type,
                        text=ws_msg.text,
                        timestamp=ws_msg.timestamp,
                    )

            elif msg_type == "menu.payload":
                ws_msg = WsMenuRender(
                    menu_id=payload["menu_id"],
                    interaction=payload["interaction"],
                    prompt=payload["prompt"],
                    options=payload.get("options"),
                    fields=payload.get("fields"),
                )
                await self._registry.send(contact_id, ws_msg.model_dump())

            elif msg_type == "agent.typing":
                ws_msg = WsAgentTyping(author_type=payload.get("author_type", "agent_ai"))
                await self._registry.send(contact_id, ws_msg.model_dump())

            elif msg_type == "session.closed":
                # Platform signals end of contact — notify client then close the WS immediately.
                await self._registry.send(
                    contact_id,
                    WsSessionClosed(reason=payload.get("reason", "agent_done")).model_dump(),
                )
                # Close the WebSocket so the customer's browser knows the session ended.
                # This also unregisters the contact so the WebchatAdapter's receive loop
                # raises WebSocketDisconnect, which publishes contact_closed to Kafka.
                # The bridge handles that idempotently (human_agent flag already deleted).
                await self._registry.close_connection(contact_id)
                logger.info("session.closed: notified and closed contact_id=%s", contact_id)

            else:
                logger.debug("unhandled outbound type=%s contact_id=%s", msg_type, contact_id)

        except Exception as exc:
            logger.error("dispatch error type=%s contact_id=%s: %s", msg_type, contact_id, exc)
