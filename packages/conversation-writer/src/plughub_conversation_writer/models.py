"""
models.py
Conversation Writer data models.
Spec: conversation-writer.md
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Literal
from pydantic import BaseModel, Field
import uuid


# ── Inbound envelope (from conversations.inbound / conversations.outbound) ─────

class MessageAuthor(BaseModel):
    type: Literal["customer", "agent_human", "agent_ai", "system"]
    id: str | None = None
    display_name: str | None = None


class MessageContent(BaseModel):
    type: Literal["text", "menu_result", "system_event"]
    text: str | None = None
    payload: dict[str, Any] | None = None


class ContextSnapshot(BaseModel):
    intent: str | None = None
    sentiment_score: float | None = None
    turn_number: int = 0


class InboundMessage(BaseModel):
    """Normalized message from conversations.inbound or conversations.outbound."""
    message_id: str
    contact_id: str
    session_id: str
    timestamp: str
    direction: Literal["inbound", "outbound"]
    channel: str
    author: MessageAuthor
    content: MessageContent
    context_snapshot: ContextSnapshot = Field(default_factory=ContextSnapshot)


# ── contact_closed event (from conversations.events) ──────────────────────────

class ContactClosedEvent(BaseModel):
    event_type: Literal["contact_closed"]
    contact_id: str
    session_id: str
    channel: str
    reason: Literal["agent_done", "client_disconnect", "timeout"]
    started_at: str
    ended_at: str


# ── Contact metadata accumulated from lifecycle events ────────────────────────

class ContactMeta(BaseModel):
    """
    Accumulated metadata for a contact, built from events seen in
    conversations.events. Fields may be absent for contacts where
    the routing layer hasn't published them yet.
    """
    contact_id: str
    session_id: str | None = None
    pool_id: str | None = None
    agent_id: str | None = None
    agent_type: Literal["human", "ai"] | None = None
    outcome: Literal["resolved", "escalated", "abandoned"] | None = None
    started_at: str | None = None
    ended_at: str | None = None
    reason: str | None = None


# ── transcript.created (published to evaluation.events) ───────────────────────

class TranscriptCreatedEvent(BaseModel):
    event_type: Literal["transcript.created"] = "transcript.created"
    transcript_id: str
    contact_id: str
    agent_id: str | None = None
    agent_type: str | None = None
    pool_id: str | None = None
    outcome: str | None = None
    turn_count: int
    started_at: str
    ended_at: str
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
