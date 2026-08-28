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

# "evaluation" is an intended, isolated profile (see config.model_profiles) that
# callers pass to avoid competing with realtime agents. It must be accepted at the
# request boundary too — omitting it here makes Pydantic 422 before the mapping runs.
ModelProfile = Literal["fast", "balanced", "powerful", "evaluation"]


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
    agent_id:      str = ""   # optional — forwarded for audit, not used in inference logic
    tenant_id:     str = ""   # optional — used for session-param analytics (best-effort)
    # T2/D1 — chave de atribuição de CUSTO. É o segmento, não o pool: segmento→pool
    # é total (`segments.pool_id` é não-nulo), mas a inversa não vale, e o pool da
    # SESSÃO é o de ENTRADA — atribuir por ele creditaria ao pool errado o
    # especialista IA invocado por `@mention`. Com o segmento vêm de graça pool,
    # participante, skill e `deploy_version`, todos por JOIN em `analytics.segments`.
    #
    # Vazio = não informado (chamador legado). Nunca inventar: o emissor manda
    # `null`, e a linha fica atribuível só até a sessão.
    segment_id:    str = ""
    prompt_id:     str = Field(..., description="Ref ao Prompt Registry")
    input:         dict[str, Any]
    # A fala do cliente, NOMEADA (2026-08-23). `input` é opaco por contrato — o
    # gateway não distingue fala de cliente de `pipeline_state`, e chutar produziria
    # score sobre texto de máquina. Preenchido pelo engine a partir da referência
    # declarada no step (`ReasonStepSchema.customer_utterance`); vazio = o step não
    # pediu medição de sentimento, e nada é medido.
    #
    # Precedente: `resolve.ts:301` já passa a fala sob nome fixo, mas DENTRO do
    # `input` — convenção por prompt. Aqui vira contrato tipado, que é o que permite
    # ao gateway agir sem conhecer o skill.
    customer_utterance: str = ""
    output_schema: dict[str, OutputFieldSchema]
    # T7b — optional full JSON Schema (montado UPSTREAM a partir do form). Quando
    # presente, o reason usa tool-use nativo (tool_choice forçado) e o ai-gateway
    # NÃO monta nada — só repassa o schema. Ausente → caminho flat (compat).
    json_schema:   dict[str, Any] | None = None
    model_profile: ModelProfile = Field(default="balanced")
    # LLM Accounts — preferred account ids for this call (config-api namespace
    # `llm_accounts`). Populated from session.pool.llm_account_ids by the
    # skill-flow-engine `reason` step. Empty = no preference (normal rotation).
    preferred_config_ids: list[str] = Field(default_factory=list)
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
    """
    Reescrito em 2026-08-23. A versão anterior devolvia `anthropic: "ok"` quando a
    string da chave era não-vazia — presença de configuração, nunca credencial
    verificada. Ficou 200 verde por meses enquanto TODA chamada ao provedor levava
    401, e como o step `reason` cai no `on_failure` (ramo legítimo do fluxo), nada
    mais ficava vermelho em lugar nenhum.

    Dois defeitos menores caíram junto: `anthropic` era `Literal["ok","error"]` mas
    a linha 439 produzia `"degraded"` quando todas as contas estavam throttled — a
    `ValidationError` do Pydantic é subclasse de `ValueError`, então caía no handler
    de `ValueError` e o endpoint respondia **422 validation_error** em vez de um
    veredicto de saúde. Esse ramo nunca havia rodado. E `AccountSelector.
    health_summary()` existia documentado "for /v1/health endpoint" sem ser chamado.

    `credentials` NÃO tem `ok` por omissão: sem desfecho registrado o valor é
    `unknown`, e `unknown` não é saúde — é ausência de evidência.
    """
    status:    Literal["ok", "degraded", "unhealthy", "unknown"]
    redis:     Literal["ok", "error"]
    # Compat: consumidores antigos liam este campo. Agora ele REFLETE a credencial
    # em vez de refletir a presença da variável de ambiente.
    anthropic: Literal["ok", "error", "degraded", "unknown", "not_configured"]
    # Estado por conta — com N contas, "anthropic quebrou" não distingue "uma chave
    # revogada" de "nenhuma funciona", e as duas pedem ações diferentes.
    accounts:  list[dict] = []
    # Contagens da janela rolante: sucessos (TESTEMUNHA) ao lado dos erros por
    # código. Sem a testemunha, `errors: {}` é indistinguível de "ninguém chamou".
    counters:  dict = {}
    # Preenchido quando o veredicto NÃO julga alguma metade — um gate que não
    # exercitou um ramo tem de dizer isso em vez de sair verde.
    notes:     list[str] = []
    version:   str = "1.0.0"


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
    # Tool permission filter — populated from session_token JWT permissions[].
    # When non-empty, only tools whose 'name' appears in this list are forwarded
    # to the LLM. An empty list means no filtering (all tools visible).
    permissions:          list[str] = Field(default_factory=list)
    # GatewayConfig IDs that should be preferred for account selection.
    # Populated from EvaluationCampaign.gateway_config_ids so that evaluation
    # workloads can be steered to dedicated API keys and avoid competing with
    # realtime agent traffic. Empty list = no preference (normal rotation).
    preferred_config_ids: list[str] = Field(default_factory=list)
    # Arc 16 — Journey ContextStore namespace.
    # When set, journey context tags from {tenant}:ctx:journey:{journey_id}
    # are injected into the system message so the agent can see cross-session
    # business process state collected by previous workflow steps.
    journey_id: str | None = Field(default=None, description="Journey UUID for context injection")


class InferenceResponse(BaseModel):
    content:         str
    intent:          str | None
    confidence:      float
    sentiment_score: float
    risk_flag:       bool
    model_used:      str   # e.g. "anthropic/claude-sonnet-4-6"
    cached:          bool


# ─────────────────────────────────────────────
# POST /v1/copilot/analyze
# ─────────────────────────────────────────────

class CopilotAnalyzeRequest(BaseModel):
    session_id:       str = Field(..., description="Active session UUID")
    tenant_id:        str = Field(..., description="Tenant ID")
    customer_message: str = Field(..., description="Latest customer message text")
