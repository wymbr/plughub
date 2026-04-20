"""
models.py
Channel Gateway data models.
Spec: PlugHub v24.0 sections 3.5, 4.7m
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Literal
from pydantic import BaseModel, Field
import uuid


# ── WebSocket — client → server ────────────────────────────────────────────

class WsMessageText(BaseModel):
    """Plain text message from client."""
    type: Literal["message.text"]
    text: str


class WsMenuSubmit(BaseModel):
    """Client submits a menu interaction result."""
    type: Literal["menu.submit"]
    menu_id: str
    interaction: Literal["text", "button", "list", "checklist", "form"]
    result: str | list[str] | dict[str, Any]


WsClientEvent = WsMessageText | WsMenuSubmit


# ── WebSocket — server → client ────────────────────────────────────────────

class WsConnectionAccepted(BaseModel):
    type: Literal["connection.accepted"] = "connection.accepted"
    contact_id: str
    session_id: str


class WsMessageOutbound(BaseModel):
    type: Literal["message.text"] = "message.text"
    message_id: str
    author: dict[str, Any]
    text: str
    timestamp: str


class WsMenuRender(BaseModel):
    type: Literal["menu.render"] = "menu.render"
    menu_id: str
    interaction: Literal["text", "button", "list", "checklist", "form"]
    prompt: str
    options: list[dict[str, str]] | None = None
    fields: list[dict[str, Any]] | None = None


class WsAgentTyping(BaseModel):
    type: Literal["agent.typing"] = "agent.typing"
    author_type: str


class WsSessionClosed(BaseModel):
    type: Literal["session.closed"] = "session.closed"
    reason: str


# ── Platform — normalised inbound event (published to conversations.inbound) ─

class ContextSnapshot(BaseModel):
    intent: str | None = None
    sentiment_score: float | None = None
    turn_number: int = 0


class MessageAuthor(BaseModel):
    type: Literal["customer", "agent_human", "agent_ai", "system"]
    id: str | None = None
    display_name: str | None = None


class MessageContent(BaseModel):
    type: Literal["text", "menu_result", "system_event"]
    text: str | None = None
    payload: dict[str, Any] | None = None


class NormalizedInboundEvent(BaseModel):
    message_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    contact_id: str
    session_id: str
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    direction: Literal["inbound"] = "inbound"
    channel: Literal["webchat"] = "webchat"
    author: MessageAuthor
    content: MessageContent
    context_snapshot: ContextSnapshot = Field(default_factory=ContextSnapshot)


# ── Platform — contact lifecycle events (conversations.events) ─────────────

class ContactOpenEvent(BaseModel):
    event_type: Literal["contact_open"] = "contact_open"
    contact_id: str
    session_id: str
    channel: Literal["webchat"] = "webchat"
    started_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ContactClosedEvent(BaseModel):
    event_type: Literal["contact_closed"] = "contact_closed"
    contact_id: str
    session_id: str
    channel: Literal["webchat"] = "webchat"
    reason: Literal["agent_done", "client_disconnect", "timeout"]
    started_at: str
    ended_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ── Platform — outbound event (consumed from conversations.outbound) ────────

class OutboundTextMessage(BaseModel):
    type: Literal["message.text"]
    contact_id: str
    session_id: str
    message_id: str
    channel: str
    author: dict[str, Any]
    text: str
    timestamp: str


class MenuPayload(BaseModel):
    type: Literal["menu.payload"]
    contact_id: str
    session_id: str
    menu_id: str
    channel: str
    interaction: Literal["text", "button", "list", "checklist", "form"]
    prompt: str
    options: list[dict[str, str]] | None = None
    fields: list[dict[str, Any]] | None = None


class AgentTypingPayload(BaseModel):
    type: Literal["agent.typing"]
    contact_id: str
    session_id: str
    channel: str
    author_type: str


class SessionClosedPayload(BaseModel):
    type: Literal["session.closed"]
    contact_id: str
    session_id: str
    channel: str
    reason: str
