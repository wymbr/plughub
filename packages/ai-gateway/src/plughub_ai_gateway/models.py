"""
models.py
Pydantic models for AI Gateway routes.
Spec: PlugHub v24.0 section 2.2a
"""

from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel, Field


# ─────────────────────────────────────────────
# Shared models
# ─────────────────────────────────────────────

CallType = Literal[
    "intent_classification",
    "sentiment_analysis",
    "response_generation",
    "tool_decision",
    "free",
]

ModelProfile = Literal["fast", "balanced", "powerful"]


class ConversationMessage(BaseModel):
    role:      Literal["customer", "agent", "system"]
    content:   str
    timestamp: str | None = None


# ─────────────────────────────────────────────
# POST /v1/turn
# ─────────────────────────────────────────────

class TurnRequest(BaseModel):
    session_id:   str             = Field(..., description="Active session UUID")
    agent_id:     str             = Field(..., description="Agent instance ID")
    tenant_id:    str             = Field(..., description="Tenant ID")
    call_type:    CallType        = Field(default="response_generation")
    model_profile: ModelProfile   = Field(default="balanced")

    # Agent instruction (from prompt_id resolved by the Agent Registry)
    system_prompt: str            = Field(..., description="Full agent instruction")

    # Conversation history
    messages: list[ConversationMessage] = Field(default_factory=list)

    # Additional input (available tools, extra context)
    tools:   list[dict[str, Any]] = Field(default_factory=list)
    context: dict[str, Any]       = Field(default_factory=dict)

    # Settings
    max_tokens:   int  = Field(default=1024, ge=1, le=8192)
    temperature:  float = Field(default=0.3, ge=0.0, le=1.0)


class ExtractedParams(BaseModel):
    """Intra-turn extracted parameters — written to the session Redis."""
    intent:          str | None  = None
    confidence:      float       = Field(default=0.0, ge=0.0, le=1.0)
    sentiment_score: float       = Field(default=0.0, ge=-1.0, le=1.0)
    flags:           list[str]   = Field(default_factory=list)


class TurnResponse(BaseModel):
    session_id:        str
    agent_id:          str
    content:           str
    tool_calls:        list[dict[str, Any]] = Field(default_factory=list)
    stop_reason:       str
    extracted_params:  ExtractedParams
    model_used:        str
    input_tokens:      int
    output_tokens:     int
    latency_ms:        int


# ─────────────────────────────────────────────
# POST /v1/reason
# ─────────────────────────────────────────────

class OutputFieldSchema(BaseModel):
    type:     Literal["string", "number", "boolean", "object", "array"]
    enum:     list[str] | None  = None
    minimum:  float | None      = None
    maximum:  float | None      = None
    required: bool              = True


class ReasonRequest(BaseModel):
    session_id:    str
    agent_id:      str
    tenant_id:     str
    prompt_id:     str = Field(..., description="Ref ao Prompt Registry")
    input:         dict[str, Any]
    output_schema: dict[str, OutputFieldSchema]
    model_profile: ModelProfile = Field(default="balanced")
    # Injected by the engine — controls format retry
    attempt:       int          = Field(default=0, ge=0)


class ReasonResponse(BaseModel):
    session_id:   str
    result:       dict[str, Any]
    model_used:   str
    input_tokens: int
    output_tokens: int
    latency_ms:   int


# ─────────────────────────────────────────────
# GET /v1/health
# ─────────────────────────────────────────────

class HealthResponse(BaseModel):
    status:   Literal["ok", "degraded"]
    redis:    Literal["ok", "error"]
    anthropic: Literal["ok", "error"]
    version:  str = "1.0.0"


# ─────────────────────────────────────────────
# POST /inference  (spec 2.2a)
# ─────────────────────────────────────────────

class InferenceMessage(BaseModel):
    role:    str
    content: str


class InferenceRequest(BaseModel):
    session_id:    str = Field(..., description="Active session UUID")
    turn_id:       str = Field(..., description="Turn ID (for Redis params key)")
    tenant_id:     str = Field(..., description="Tenant ID")
    agent_type_id: str = Field(..., description="Agent type ID (for rate limiting)")
    model_profile: ModelProfile = Field(default="balanced")
    messages:      list[InferenceMessage]       = Field(default_factory=list)
    tools:         list[dict[str, Any]] | None  = Field(default=None)


class InferenceResponse(BaseModel):
    content:         str
    intent:          str | None
    confidence:      float
    sentiment_score: float
    risk_flag:       bool
    model_used:      str   # e.g. "anthropic/claude-sonnet-4-6"
    cached:          bool
