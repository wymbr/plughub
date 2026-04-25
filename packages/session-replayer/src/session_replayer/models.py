"""
models.py
Pydantic models espelhando os schemas TypeScript de @plughub/schemas/evaluation.ts
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────
# Stream event reconstruído
# ─────────────────────────────────────────────

class ReplayEvent(BaseModel):
    event_id:         str
    type:             str
    timestamp:        datetime
    author:           Optional[dict[str, Any]] = None
    visibility:       Any                       = None
    payload:          dict[str, Any]            = Field(default_factory=dict)
    original_content: Optional[dict[str, Any]] = None
    masked_categories: list[str]               = Field(default_factory=list)
    delta_ms:         float                     = 0.0


# ─────────────────────────────────────────────
# ReplayContext
# ─────────────────────────────────────────────

class SessionMeta(BaseModel):
    channel:      str
    opened_at:    datetime
    closed_at:    Optional[datetime] = None
    outcome:      Optional[str]      = None
    close_reason: Optional[str]      = None
    duration_ms:  Optional[float]    = None


class SentimentEntry(BaseModel):
    score:     float
    timestamp: datetime


class ParticipantSummary(BaseModel):
    participant_id: str
    role:           str
    agent_type_id:  Optional[str]      = None
    joined_at:      datetime
    left_at:        Optional[datetime] = None


class ReplayContext(BaseModel):
    session_id:      str
    tenant_id:       str
    replay_id:       str
    session_meta:    SessionMeta
    events:          list[ReplayEvent]        = Field(default_factory=list)
    sentiment:       list[SentimentEntry]     = Field(default_factory=list)
    participants:    list[ParticipantSummary] = Field(default_factory=list)
    speed_factor:    float                    = 1.0
    source:          Literal["redis", "postgres"] = "redis"
    created_at:      datetime                 = Field(default_factory=datetime.utcnow)
    #: Quando True, o agente evaluator deve fornecer comparison_turns em evaluation_submit
    #: para que o Comparator produza um ComparisonReport junto ao EvaluationResult.
    comparison_mode: bool                     = False


# ─────────────────────────────────────────────
# EvaluationRequest
# ─────────────────────────────────────────────

class EvaluationRequest(BaseModel):
    event_type:      Literal["evaluation.requested"] = "evaluation.requested"
    evaluation_id:   str
    session_id:      str
    tenant_id:       str
    evaluator_pool:  str
    agent_type_id:   Optional[str] = None
    speed_factor:    float         = 10.0
    comparison_mode: bool          = False
    dimensions:      list[str]     = Field(default_factory=list)
    requested_at:    datetime      = Field(default_factory=datetime.utcnow)


# ─────────────────────────────────────────────
# SessionClosedEvent (Kafka payload)
# ─────────────────────────────────────────────

class SessionClosedEvent(BaseModel):
    session_id:   str
    tenant_id:    str
    outcome:      Optional[str] = None
    close_reason: Optional[str] = None
    closed_at:    Optional[str] = None
