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

    # Camada B (pull direcionado / "ramal") — reserva do work item a um user_id
    # específico com transbordo por lease. Declarado AQUI para SOBREVIVER ao
    # model_validate/model_dump (Pydantic descarta campos não declarados) e fluir até
    # `contact_data` → {t}:queue_contact:{sid}, de onde work_task_claim os lê. Só
    # efetivo em pool `dispatch_mode: pull`; ausente = fila compartilhada (retrocompat).
    assigned_to:              str | None = None
    fallback_to_pool_after_s: int | None = None
    # Wrap-up unificado (Camada E2) — auto-atendimento (inline). Declarado AQUI para
    # SOBREVIVER ao model_validate/model_dump e fluir até `contact_data` →
    # {t}:queue_contact:{sid}, de onde o item de pull o leva ao Console (que
    # auto-reivindica). Ausente/false = pull manual (detached).
    auto_attend:              bool = False


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
    # `source` é o ÚNICO discriminador de instância humana no registro, e é
    # load-bearing em DOIS consumidores independentes:
    #   · `set_instance` decide KEEPTTL (humano, chave permanente do mcp-server)
    #     × `ex=30` (IA) lendo este campo;
    #   · `instance_bootstrap._reconcile_tenant` (bridge) pula instâncias humanas
    #     no ramo de surplus lendo este campo — sem ele, o humano é classificado
    #     como "instância não desejada e ociosa" e recebe DEL.
    #
    # Não estava declarado aqui, então o round-trip `model_validate → model_dump`
    # do `mark_busy` o APAGAVA — silenciosamente, e só quando o agente pegava um
    # contato. A partir daí a chave humana virava efêmera (`ex=30`) e o
    # reconciliador do bridge a deletava. Rastro completo no MONITOR de
    # 2026-07-28 (ver CHANGELOG). Mesma armadilha que `skill_id`/`user_*` acima já
    # documentam: campo não declarado não sobrevive ao round-trip.
    source:           str = ""
    # F5 (ADR adr-human-agent-pool-scoped-identity): o campo `pool_id` SINGULAR foi
    # removido. Ele era `pools[0]` — sem significado em multi-pool, e um convite
    # permanente a ler "o pool da instância" onde a pergunta certa é "o pool DESTA
    # sessão" (que vive em `session:{sid}:routing:{iid}`) ou "o pool em escopo"
    # (que todo leitor de decisão já tem, via `for pool in pools:`).
    # Nada consumia o campo como decisão — o único leitor de produção era um
    # fallback `pid == pool_id` ao lado de `pools.includes(pool_id)` no mcp-server.
    # Registros antigos que ainda tenham o campo seguem validando (o default do
    # Pydantic v2 é `extra="ignore"`); ele simplesmente deixa de ser reescrito, e
    # o `model_dump` do `set_instance` para de emiti-lo.
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

    @property
    def is_human(self) -> bool:
        return is_human_instance_id(self.instance_id)


# ─────────────────────────────────────────────
# Identidade de instância por-pool
# ADR: docs/adr/adr-human-agent-pool-scoped-identity.md
# ─────────────────────────────────────────────

HUMAN_INSTANCE_PREFIX = "human-"
HUMAN_LOGIN_SOURCE    = "human_login"


def is_human_instance_id(instance_id: str, source: str = "") -> bool:
    """Instância humana?

    O discriminador forte é `source == "human_login"` (escrito pelo mcp-server),
    mas ele **não sobrevive** ao round-trip `model_validate → model_dump` do
    `mark_busy` — o Pydantic descarta campos não declarados, e `AgentInstance`
    não declara `source`. Por isso o prefixo do `instance_id` é o teste primário
    aqui (mesma escolha que o `crash_detector` já fazia); `source` entra como
    reforço quando o chamador tem o dict cru em mãos.
    """
    return bool(source == HUMAN_LOGIN_SOURCE
                or instance_id.startswith(HUMAN_INSTANCE_PREFIX))


def resolve_agent_type(instance: "AgentInstance", pool_id: str) -> str:
    """Tipo de agente da instância **no escopo de um pool**.

    Para humano, `agent_type_id` é função pura do pool (`human_agent_{pool}`) —
    um mesmo humano atende N pools com a MESMA instância, então não existe um
    "tipo" único que o registro do recurso possa guardar. O campo armazenado é,
    para humano, um resíduo arbitrário (o pool do primeiro login) e **não deve
    ser propagado**: é ele que vira `conversations.routed.agent_type_id`, com o
    qual o bridge decide o que executar.

    Para IA o campo é identidade legítima (uma instância pertence a um agent
    type e a um pool) e é devolvido como está.

    Todo chamador tem o pool em escopo — o roteamento itera
    `for pool in pools: for inst in get_ready_instances(pool)`. É isso que torna
    a derivação possível sem mudar assinatura de nada.
    """
    if pool_id and instance.is_human:
        return f"human_agent_{pool_id}"
    return instance.agent_type_id


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

    # Camada C (detach de hooks): ACW como regra de agent_ready.
    #   "none" (default): não bloqueia. "soft": disponível, supervisor vê pendências.
    #   "hard": get_ready_instances pula a instância com wrap-up detached pendente
    #           (marker {t}:instance:{iid}:acw_pending). O ACW do wrap-up INLINE
    #           segue via wrap_up_pending, independente deste campo.
    acw_gate: str = "none"


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

    # Camada B (pull direcionado / "ramal"): reserva do work item a um recurso
    # específico, com transbordo pro pool por lease.
    #   assigned_to             — user_id preferido (ausente = fila compartilhada,
    #                             comportamento atual — retrocompat).
    #   fallback_to_pool_after_s — janela da reserva em segundos; após ela o item
    #                             vira claimable por qualquer um do pool. Ausente =
    #                             reserva permanente (só assigned_to; nunca transborda).
    #   assigned_at_ms          — âncora da janela (carimbada no 1º enqueue e
    #                             PRESERVADA no re-enfileiramento; fallback queued_at_ms).
    # INVARIANTE: isto é elegibilidade de claim sobre trabalho *pooled* — NUNCA
    # alvo de roteamento que bypassa o pool. O pool segue a unidade endereçável.
    assigned_to:              str | None = None
    fallback_to_pool_after_s: int | None = None
    assigned_at_ms:           int | None = None


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
