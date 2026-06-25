"""
events.py
Pydantic mirror of the `ingestion_event_v1` family (@plughub/schemas/ingestion-event.ts).

This is the OPEN event interface of the module (anti-corruption boundary). Python
services in this repo define their own Pydantic models rather than importing the
TypeScript Zod schemas; this file is the authoritative Python copy of that contract.

Mandatory/optional per docs/arcos/quality-ingest.md §3. The contract uses the
module's own vocabulary (small enums + free strings for channel/outcome/pool_id/
masked_categories) — intentionally NOT coupled to internal enums.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field

INGESTION_EVENT_SCHEMA_VERSION = "ingestion_event_v1"

# Module vocabulary (anti-corruption)
AgentKind   = Literal["ai", "human"]
AuthorRole  = Literal["customer", "agent", "system"]
ContentType = Literal["text", "image", "audio", "video", "file", "location", "template"]
Visibility  = Literal["all", "agents_only"]
SegmentRole = Literal["primary", "specialist", "supervisor", "evaluator", "reviewer"]


class ContactOpened(BaseModel):
    event_type: Literal["contact.opened"]
    external_contact_id: str = Field(min_length=1)
    source: str = Field(min_length=1)
    channel: str = Field(min_length=1)
    opened_at: str                       # ISO-8601
    event_id: str | None = None
    # optional (contato)
    medium: str | None = None
    customer_ref: str | None = None


class ParticipantJoined(BaseModel):
    event_type: Literal["participant.joined"]
    external_contact_id: str = Field(min_length=1)
    segment_ref: str = Field(min_length=1)
    external_agent_id: str = Field(min_length=1)
    agent_kind: AgentKind
    pool_id: str = Field(min_length=1)
    started_at: str
    event_id: str | None = None
    source: str | None = None
    # optional (segment)
    role: SegmentRole = "primary"
    skill_id: str | None = None          # AI only
    deploy_version: str | None = None    # AI only


class MessageSent(BaseModel):
    event_type: Literal["message.sent"]
    external_contact_id: str = Field(min_length=1)
    ts: str
    author_role: AuthorRole
    content: str
    masked: bool
    event_id: str | None = None
    source: str | None = None
    # optional (msg)
    author_id: str | None = None
    segment_ref: str | None = None
    content_type: ContentType = "text"
    visibility: Visibility = "all"
    masked_categories: list[str] = Field(default_factory=list)


class ParticipantLeft(BaseModel):
    event_type: Literal["participant.left"]
    external_contact_id: str = Field(min_length=1)
    segment_ref: str = Field(min_length=1)
    ended_at: str
    event_id: str | None = None
    source: str | None = None
    # optional
    outcome: str | None = None
    tool_trace: list[Any] | None = None          # tier-2, internal only
    precomputed_metrics: dict[str, float] | None = None


class ContactClosed(BaseModel):
    event_type: Literal["contact.closed"]
    external_contact_id: str = Field(min_length=1)
    outcome: str = Field(min_length=1)
    closed_at: str
    event_id: str | None = None
    source: str | None = None
    # optional (contato)
    close_reason: str | None = None
    precomputed_metrics: dict[str, float] | None = None


# Plain union of the model classes (for typing / isinstance in the mapper).
IngestionEventModels = Union[
    ContactOpened, ParticipantJoined, MessageSent, ParticipantLeft, ContactClosed
]

# Discriminated union for parsing an unknown inbound event by `event_type`.
IngestionEvent = Annotated[IngestionEventModels, Field(discriminator="event_type")]


def derive_event_id(external_contact_id: str, event_type: str, index: int) -> str:
    """Stable, deterministic event_id when the sender did not provide one
    (mirrors deriveIngestionEventId in the TS schema)."""
    return f"ext:{external_contact_id}:{event_type}:{index}"
