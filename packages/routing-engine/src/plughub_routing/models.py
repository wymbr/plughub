"""
models.py
Pydantic models for the Routing Engine.
Spec: PlugHub v24.0 sections 3.3, 3.3a, 4.6
"""

from __future__ import annotations
from typing import Literal, Any
from pydantic import BaseModel, Field


# ─────────────────────────────────────────────
# Routing Expression — weights for priority_score
# Spec 4.6
# ─────────────────────────────────────────────

class RoutingExpression(BaseModel):
    """Weights for priority_score calculation per pool. Spec 4.6."""
    weight_sla:      float = Field(default=1.0, ge=0.0)
    weight_wait:     float = Field(default=0.8, ge=0.0)
    weight_tier:     float = Field(default=0.6, ge=0.0)
    weight_churn:    float = Field(default=0.9, ge=0.0)
    weight_business: float = Field(default=0.4, ge=0.0)


# ─────────────────────────────────────────────
# Customer Profile
# ─────────────────────────────────────────────

class CustomerProfile(BaseModel):
    tier:           Literal["platinum", "gold", "standard"] = "standard"
    churn_risk:     float = Field(default=0.0, ge=0.0, le=1.0)
    ltv:            float | None = None
    business_score: float = Field(default=0.0, ge=0.0, le=1.0)
    # risk flag → forces 'human' (supervised) mode regardless of confidence
    risk_flag:      bool  = False


# ─────────────────────────────────────────────
# Inbound Event
# ─────────────────────────────────────────────

class ConversationInboundEvent(BaseModel):
    session_id:   str
    tenant_id:    str
    customer_id:  str
    channel:      Literal["whatsapp", "webchat", "voice", "email", "sms", "instagram", "telegram", "webrtc"]

    # Target pool — set by channel-gateway on contact open (entry point config)
    # or by conversation_escalate (explicit escalation target).
    # When present, routing is restricted to this pool only.
    # When absent, the router searches all candidate pools for the channel (legacy fallback).
    pool_id:      str | None = None

    # Optional enrichment — populated later by AI Gateway (step reason) or inferred upstream.
    # Never used for initial routing; the entry point pool_id is the sole routing signal.
    intent:       str | None = None
    confidence:   float      = Field(default=0.0, ge=0.0, le=1.0)
    customer_profile: CustomerProfile = Field(default_factory=CustomerProfile)
    process_context:  dict[str, Any] | None = None
    started_at:   str
    elapsed_ms:   int = 0  # time the contact has already been waiting (0 for new ones)
    # Competency requirements inferred by the AI Gateway
    requirements: dict[str, int] = Field(default_factory=dict)

    # Conference fields — populated by agent_join_conference tool (mcp-server).
    # When agent_type_id is present, routing is restricted to instances of that
    # specific agent type within the declared pool (human supervisor invited a
    # specific AI agent into an active session).
    # conference_id is propagated to RoutingResult → bridge → session_context
    # so the activated AI agent knows it is operating in a supervised conference.
    # channel_identity declares how the AI agent appears to the customer:
    #   { "text": "Assistente", "voice_profile": "assistant_pt_br" }
    agent_type_id:    str | None = None
    conference_id:    str | None = None
    channel_identity: dict[str, str] | None = None  # { text, voice_profile }


# ─────────────────────────────────────────────
# Agent Instance — real-time state
# Populated by kafka_listener from agent.lifecycle
# Redis key: {tenant_id}:instance:{instance_id}  TTL: 30s
# ─────────────────────────────────────────────

class AgentInstance(BaseModel):
    instance_id:      str
    agent_type_id:    str
    tenant_id:        str
    # pool_id is written by mcp-server (human agents) but omitted by the
    # orchestrator-bridge bootstrap (which uses pools: list[str] instead).
    # Optional to allow both sources to validate without errors.
    pool_id:          str = ""
    pools:            list[str] = Field(default_factory=list)  # all pools this instance belongs to
    # execution_model defaults to "stateless" so bootstrap instances (which do
    # not include this field explicitly) validate cleanly.
    execution_model:  Literal["stateless", "stateful"] = "stateless"
    max_concurrent:   int = 1
    current_sessions: int = Field(default=0, ge=0)
    # 'state' kept for compatibility with internal scorer/router;
    # Redis uses 'status' (login|ready|busy|paused|logout|draining)
    state:            str = "ready"
    last_seen:        str | None = None
    # registered_at is written by mcp-server but omitted by bootstrap instances.
    registered_at:    str = ""
    # Competency profile declared in agent_login
    profile:          dict[str, int] = Field(default_factory=dict)


# ─────────────────────────────────────────────
# Pool Config — read from Redis cache (populated by kafka_listener)
# Never access PostgreSQL directly.
# ─────────────────────────────────────────────

class PoolConfig(BaseModel):
    pool_id:        str
    tenant_id:      str
    channel_types:  list[str]
    sla_target_ms:  int

    # Spec 4.6 — weights for priority_score
    routing_expression: RoutingExpression = Field(default_factory=RoutingExpression)

    # Scenario 2 — queue prioritisation (queue_scorer)
    competency_weights: dict[str, float] = Field(default_factory=dict)
    aging_factor:   float = Field(default=0.4, ge=0.0, le=2.0)
    breach_factor:  float = Field(default=0.8, ge=0.0, le=3.0)

    # Remote sites for cross-site routing (in order of preference)
    remote_sites:   list[str] = Field(default_factory=list)

    # Indicates whether the pool is a human-agent pool (determines saturation by channel)
    is_human_pool:  bool = False

    # Runtime queue depth — populated from the pool snapshot at routing time.
    # Defaults to 0 (not available) when not yet written by the router.
    # Used exclusively as a tie-breaker in decide() when two pools have equal score.
    queue_length:   int  = 0


# ─────────────────────────────────────────────
# Queue
# ─────────────────────────────────────────────

class QueuedContact(BaseModel):
    """Contact waiting in a pool queue."""
    session_id:    str
    tenant_id:     str
    pool_id:       str
    tier:          Literal["platinum", "gold", "standard"] = "standard"
    queued_at_ms:  int   # timestamp epoch ms
    requirements:  dict[str, int] = Field(default_factory=dict)


# ─────────────────────────────────────────────
# Routing Decision — return value of decide()
# ─────────────────────────────────────────────

RoutingMode = Literal["autonomous", "hybrid", "supervised"]


class AllocatedAgent(BaseModel):
    instance_id:   str
    agent_type_id: str
    pool_id:       str
    score:         float


class RoutingDecision(BaseModel):
    """Result of decide(). Includes primary agent, fallback, mode and re-evaluation turn."""
    conversation_id:   str
    tenant_id:         str
    mode:              RoutingMode
    primary:           AllocatedAgent | None = None
    fallback:          AllocatedAgent | None = None
    # Re-evaluation turn: None (autonomous), 5 (hybrid), 1 (supervised)
    reevaluation_turn: int | None = None
    # Saturated pool policy (section 3.3a)
    saturated:         bool = False
    saturation_action: str | None = None
    decided_at:        str


# ─────────────────────────────────────────────
# Routing Result — used by router.py (route/dequeue)
# ─────────────────────────────────────────────

class RoutingResult(BaseModel):
    session_id:     str
    tenant_id:      str
    allocated:      bool
    instance_id:    str | None = None
    agent_type_id:  str | None = None
    pool_id:        str | None = None
    resource_score: float = 0.0   # competency score (scenario 1)
    priority_score: float = 0.0   # SLA/priority score (spec 4.6)
    routing_mode:   RoutingMode = "autonomous"
    cross_site:     bool = False
    allocated_site: str | None = None
    queued:         bool = False
    queue_eta_ms:   int | None = None
    routed_at:      str
    # Passed through from ConversationInboundEvent when this routing was
    # triggered by an agent_join_conference invite (conference mode).
    conference_id:    str | None = None
    channel_identity: dict[str, str] | None = None  # { text, voice_profile }


class ConversationRoutedEvent(BaseModel):
    session_id: str
    tenant_id:  str
    result:     RoutingResult
    routed_at:  str


# ─────────────────────────────────────────────
# InstanceMeta — persistent per-instance index (no TTL)
# Used by CrashDetector to recover orphaned conversations.
# Redis keys:
#   {tenant_id}:routing:instance:{instance_id}:meta          — HASH (pools, agent_type_id)
#   {tenant_id}:routing:instance:{instance_id}:conversations — SET of active conversation_ids
# ─────────────────────────────────────────────

class InstanceMeta(BaseModel):
    """
    Persistent instance metadata — no TTL.
    Populated by kafka_listener; consumed by CrashDetector.
    """
    pools:                list[str] = Field(default_factory=list)
    agent_type_id:        str       = ""
    active_conversations: list[str] = Field(default_factory=list)
