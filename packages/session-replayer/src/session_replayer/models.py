"""
models.py
Pydantic models espelhando os schemas TypeScript de @plughub/schemas/evaluation.ts

Inclui modelos Arc 3 (Session Replayer) e Arc 6 (Evaluation Platform).
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

    # ── Arc 6 — form-aware evaluation context (optional, backward-compatible) ──

    #: EvaluationForm populated by evaluation-api when a campaign triggers this replay.
    #: When present, the evaluator agent uses this form as the evaluation template.
    #: Stored as a plain dict to avoid a circular dependency on evaluation-api models.
    evaluation_form: Optional[dict[str, Any]] = None

    #: EvaluationCampaign that triggered this evaluation (Arc 6).
    campaign_id:     Optional[str]            = None

    #: EvaluationInstance tracking record ID (Arc 6).
    instance_id:     Optional[str]            = None


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

    # ── Arc 6 — campaign context (optional, backward-compatible) ──
    form_id:     Optional[str] = None
    campaign_id: Optional[str] = None
    instance_id: Optional[str] = None


# ─────────────────────────────────────────────
# SessionClosedEvent (Kafka payload)
# ─────────────────────────────────────────────

class SessionClosedEvent(BaseModel):
    session_id:   str
    tenant_id:    str
    outcome:      Optional[str] = None
    close_reason: Optional[str] = None
    closed_at:    Optional[str] = None


# ─────────────────────────────────────────────
# Arc 6 — Evaluation Platform models
# (mirror of @plughub/schemas/evaluation.ts Arc 6 section)
# ─────────────────────────────────────────────

class KnowledgeSnippet(BaseModel):
    """RAG result from mcp-server-knowledge, attached to an EvaluationResult."""
    snippet_id:   str
    content:      str
    score:        float     # cosine similarity 0–1
    source_ref:   Optional[str]     = None
    retrieved_at: datetime          = Field(default_factory=datetime.utcnow)


class EvidenceRef(BaseModel):
    """Pointer to a specific event in the replay transcript supporting a score."""
    event_id:   str
    turn_index: int
    quote:      Optional[str] = None   # ≤500 chars
    category:   Literal["positive", "negative", "neutral"] = "neutral"


class EvaluationCriterionResponse(BaseModel):
    """Evaluator's structured answer to one criterion in the form."""
    criterion_id:  str
    na:            bool           = False
    score:         Optional[float]  = None   # type "score"
    boolean_value: Optional[bool]   = None   # type "boolean"
    choice_value:  Optional[str]    = None   # type "choice"
    text_value:    Optional[str]    = None   # type "text"
    notes:         Optional[str]    = None
    evidence:      list[EvidenceRef] = Field(default_factory=list)
