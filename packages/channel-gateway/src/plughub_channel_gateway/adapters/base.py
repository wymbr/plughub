"""
adapters/base.py
Abstract base class for all channel adapters.

Architecture: channel-gateway-multi-channel.md § 3 (ChannelAdapter hierarchy)

Design
------
One ChannelAdapter *singleton* is registered per channel in OutboundConsumer at
startup.  The singleton owns channel-level delivery logic and is stateless with
respect to individual connections — per-connection state lives in
SessionRegistry (webchat) or is tracked internally by the adapter (future
channels).

Delivery methods receive the raw Kafka payload dict (conversations.outbound)
and are responsible for:
  - Translating the platform-neutral payload to channel-native format.
  - Routing the message to the correct active connection/session.
  - Logging delivery errors without crashing the consumer loop.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar


class ChannelAdapter(ABC):
    """
    Abstract base for per-channel outbound delivery singletons.

    Subclasses must set `channel` to the channel identifier string they handle
    (e.g. "webchat", "whatsapp", "sms", "email", "voice").  The string must
    match what arrives in the `channel` field of `conversations.outbound`
    Kafka messages.

    All methods receive the raw payload dict from the Kafka message.  Each
    method silently no-ops for payloads not intended for its channel —
    OutboundConsumer already routes by channel before calling these.
    """

    channel: ClassVar[str]  # must be overridden by every concrete subclass

    @abstractmethod
    async def deliver_text(self, payload: dict) -> None:
        """
        Deliver a text message to the customer.

        For webchat this means persisting to conversation history (actual
        delivery to the client browser happens via the canonical Redis stream).
        For webhook channels this means calling the channel API (WhatsApp,
        SMS, Email).
        """

    @abstractmethod
    async def deliver_menu(self, payload: dict) -> None:
        """
        Deliver an interactive menu / form request to the customer.

        Channel adapters that do not natively support menus must implement a
        fallback strategy (e.g. sequential collect for SMS, link for email).
        """

    @abstractmethod
    async def deliver_typing(self, payload: dict) -> None:
        """
        Deliver an agent typing indicator to the customer.

        Channels that do not support typing indicators should no-op silently.
        """

    @abstractmethod
    async def deliver_session_closed(self, payload: dict) -> None:
        """
        Notify the customer that the session has ended and clean up any
        connection state (close WebSocket, release webhook hold, etc.).
        """
