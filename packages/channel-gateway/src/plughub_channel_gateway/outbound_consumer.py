"""
outbound_consumer.py
Kafka consumer for conversations.outbound.

Routes each outbound message to the correct ChannelAdapter based on the
`channel` field in the payload.  Each adapter handles delivery to the
customer in a channel-appropriate way.

Architecture: channel-gateway-multi-channel.md § 6 (OutboundConsumer registry)

Registry pattern
----------------
OutboundConsumer holds a dict[str, ChannelAdapter].  New channels are added
by registering their adapter singleton at startup — no changes to this file.

    consumer = OutboundConsumer(
        adapters  = {"webchat": WebchatChannelAdapter(registry)},
        settings  = settings,
    )
"""

from __future__ import annotations

import asyncio
import json
import logging

from aiokafka import AIOKafkaConsumer

from .adapters.base import ChannelAdapter
from .config import Settings

logger = logging.getLogger("plughub.channel-gateway.outbound")


class OutboundConsumer:
    def __init__(
        self,
        adapters:  dict[str, ChannelAdapter],
        settings:  Settings,
    ) -> None:
        self._adapters  = adapters
        self._settings  = settings

    async def run(self) -> None:
        consumer = AIOKafkaConsumer(
            self._settings.kafka_topic_outbound,
            bootstrap_servers = self._settings.kafka_brokers,
            group_id          = self._settings.kafka_group_id,
            auto_offset_reset = "latest",
            # Low-latency tuning: reduce broker wait time before returning data.
            fetch_max_wait_ms = 100,
            fetch_min_bytes   = 1,
        )
        await consumer.start()
        logger.info(
            "outbound consumer started — topic=%s channels=%s",
            self._settings.kafka_topic_outbound,
            list(self._adapters),
        )

        try:
            async for msg in consumer:
                asyncio.create_task(self._dispatch(json.loads(msg.value.decode())))
        finally:
            await consumer.stop()

    async def _dispatch(self, payload: dict) -> None:
        msg_type   = payload.get("type")
        contact_id = payload.get("contact_id")
        channel    = payload.get("channel")

        if not contact_id or not channel:
            if msg_type or channel:
                logger.debug(
                    "outbound skipped type=%s channel=%s contact_id=%s",
                    msg_type, channel, contact_id,
                )
            return

        adapter = self._adapters.get(channel)
        if adapter is None:
            logger.debug(
                "outbound: no adapter registered for channel=%s type=%s contact_id=%s",
                channel, msg_type, contact_id,
            )
            return

        logger.info(
            "outbound dispatch type=%s channel=%s contact_id=%s session_id=%s",
            msg_type, channel, contact_id, payload.get("session_id"),
        )

        try:
            if msg_type == "message.text":
                await adapter.deliver_text(payload)

            elif msg_type == "menu.payload":
                await adapter.deliver_menu(payload)

            elif msg_type == "agent.typing":
                await adapter.deliver_typing(payload)

            elif msg_type == "session.closed":
                await adapter.deliver_session_closed(payload)

            else:
                logger.debug(
                    "unhandled outbound type=%s channel=%s contact_id=%s",
                    msg_type, channel, contact_id,
                )

        except Exception as exc:
            logger.error(
                "dispatch error type=%s channel=%s contact_id=%s: %s",
                msg_type, channel, contact_id, exc,
            )
