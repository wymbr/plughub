"""
models.py
Pydantic models for the Routing Engine.
Spec: PlugHub v24.0 sections 3.3, 3.3a, 4.6
"""

from __future__ import annotations
from typing import Literal, Any
from pydantic import BaseModel, ConfigDict, Field


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
    channel:      Literal["whatsapp", "webchat", "voice", "email", "sms", "instagram", "telegram", "webrtc", "webhook"]

    # Target pool — set by channel-gateway on contact open (entry point config)
    # or by conversation_escalate (explicit escalation target).
    # When present, routing is restricted to this pool only.
    # When absent, the router searches all candidate pools for the channel (legacy fallback).
    pool_id:      str | None = None

    # Arc 19: webhook channel endpoint (DNIS). The webhook adapter publishes
    # pool_id=None + skill_id=<endpoint>; the router resolves the pool by matching
    # this against each webhook pool's webhook_skill_id (router.route fallback).
    # Declared here so it survives model_validate (Pydantic drops undeclared fields).
    skill_id:     str = ""

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
    # Fase 3a/3b — deploy-driven instances (source=bootstrap_deploy) carry the
    # deployed skill_id. Declared here so it survives the model_validate →
    # model_dump round-trip in mark_busy (Pydantic drops undeclared fields),
    # keeping the skill identity available to the bridge after allocation.
    skill_id:         str = ""
    flow_id:          str = ""
    # C1 — human instances carry the login identity (user_id + user_login/email)
    # so it survives the agent_ready upsert and the mark_busy round-trip (Pydantic
    # drops undeclared fields). The bridge reads it to denormalize onto the segment.
    user_id:          str = ""
    user_login:       str = ""
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
    # Ignore unknown fields so the routing engine stays forward-compatible when
    # new fields are added to pool_config (e.g. mentionable_pools, supervisor_config).
    model_config = ConfigDict(extra="ignore")

    pool_id:           str
    tenant_id:         str
    channel_types:     list[str]
    sla_target_ms:     int
    # Maximum reply time per customer message (ms). None = no per-message SLA.
    max_reply_time_ms: int | None = None

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

    # Alias → pool_id map declared on the pool in agent-registry (JSONB field).
    # Populated by kafka_listener from pool.registered/pool.updated events.
    # Written to ContextStore as session.pool.mentionable_pools so skill-flows
    # can reference reachable pools by alias without hard-coding pool IDs.
    mentionable_pools: dict[str, str] | None = None

    # Agent Group IDs (Arc 9) this pool belongs to.
    # Written to ContextStore as session.pool.agent_groups[].
    agent_groups: list[str] = Field(default_factory=list)

    # ContextoTab namespace visibility config (Arc 11 / context-store-taxonomy).
    # Determines which ContextStore namespaces operator role can read in the UI.
    # Stored in pool_config Redis → read by mcp-server supervisor_state REST endpoint.
    # None = use platform default (["service", "session"]).
    context_visibility: dict | None = None

    # Arc 19: Webhook pool fields.
    # webhook_skill_id: the skill endpoint (the "DIN" of the webhook channel).
    # Required when channel_types includes "webhook".  The WebhookAdapter uses this
    # to correlate a trigger with its target pool and to record the DNIS in the
    # conversations.inbound event.
    webhook_skill_id: str | None = None
    # max_concurrent_sessions: capacity ceiling for webhook pools.
    # For webhook pools, capacity is controlled by this limit (not by logged-in
    # agent instances).  For human/AI pools, this field is informational only.
    # Included in the pool snapshot so the Monitor can display configured capacity.
    max_concurrent_sessions: int | None = None

    # Fase B (queue-attended-model): hybrid session admission.
    # session_reservation: dedicated session slots for this pool (cap AND guarantee),
    # carved out of the installation's max_session_total. None = the pool draws from
    # the shared bucket (total − Σ reservations). Billing is on the total only.
    session_reservation: int | None = None

    # Fase E (queue-attended-model): queue treatment config passthrough.
    # {agent_type_id, max_wait_s?, skill_id?} — the bridge activates the queue
    # agent; the routing engine reads max_wait_s here to enforce the retention
    # bound (close_reason=max_wait_exceeded). None = no pool-level queue config;
    # settings.queue_max_wait_default_s still bounds the wait (mute queues incl.).
    queue_config: dict | None = None

    # Capacity-governance item 2 (2026-06-05): tipagem do pool — "human" | "ai".
    # Backfilled pelo registry (deploy slot ⇒ ai; senão human). Base do gate por
    # tipo na admissão (sessões em pools 'ai' ≤ C_ai) — Etapa 2 do item 2.
    agent_kind: str | None = None

    # Frente 1 (dispatch pull): "push" (auto-aloca, default) | "pull" (agente puxa
    # da fila — route() parqueia sem alocar; agent_ready não drena; claim explícito).
    # F1.0 é só plumbing: o route() só passa a ramificar em F1.1.
    dispatch_mode: str = "push"


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
    # SLA threshold (ms) of the selected pool — forwarded to analytics so the
    # sessions table can compute sla_compliance_pct without a Redis lookup.
    sla_target_ms:    int | None = None
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
