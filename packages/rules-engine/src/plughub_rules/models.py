"""
models.py
Pydantic models for the Rules Engine.
Spec: PlugHub v24.0 section 3.2
"""

from __future__ import annotations
from typing import Literal, Any
from pydantic import BaseModel, Field


# ─────────────────────────────────────────────
# Escalation rule
# ─────────────────────────────────────────────

RuleStatus = Literal["draft", "dry_run", "shadow", "active", "disabled"]

ObservableParameter = Literal[
    "sentiment_score",
    "intent_confidence",
    "turn_count",
    "elapsed_ms",
    "flag",
]

Operator = Literal["lt", "lte", "gt", "gte", "eq", "neq", "contains"]

RuleLogic = Literal["AND", "OR"]


class Condition(BaseModel):
    parameter:    ObservableParameter
    operator:     Operator
    value:        float | str
    window_turns: int | None = None    # moving average over N turns
    flag_name:    str | None = None    # when parameter == "flag"


class Rule(BaseModel):
    rule_id:      str
    tenant_id:    str
    name:         str
    status:       RuleStatus = "draft"
    conditions:   list[Condition] = Field(min_length=1)
    logic:        RuleLogic       = "AND"
    target_pool:  str | None      = None  # no pool = no action
    priority:     int             = Field(default=1, ge=1, le=10)
    # RUL-02 — o que o cliente lê quando a regra o transfere. Opcional: ausente, ele vê só a
    # saída do agente e o que o pool de destino disser (a plataforma não inventa texto).
    customer_notice: str | None   = Field(default=None, max_length=500)
    created_at:   str
    updated_at:   str


# ─────────────────────────────────────────────
# Evaluation context — session parameters
# ─────────────────────────────────────────────

class EvaluationContext(BaseModel):
    session_id:       str
    tenant_id:        str
    turn_count:       int   = 0
    elapsed_ms:       int   = 0
    # `None` = NÃO MEDIDO (o ai-gateway publica assim desde 2026-08-23, e 0.0 é neutro, um
    # ponto legítimo da escala). Até a RUL-02 este campo exigia float: TODA avaliação de regra
    # quebrava na validação, logada como ERROR por mais de um mês — nenhuma regra, nem em
    # shadow, avaliou nada. Condição de sentimento sem medida NÃO casa (`_apply_operator`).
    sentiment_score:  float | None = Field(default=None, ge=-1.0, le=1.0)
    intent_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    flags:            list[str] = Field(default_factory=list)
    sentiment_history: list[float] = Field(default_factory=list)


# ─────────────────────────────────────────────
# Evaluation result
# ─────────────────────────────────────────────

class ConditionResult(BaseModel):
    condition:      Condition
    matched:        bool
    observed_value: float | str | None = None


class EvaluationResult(BaseModel):
    rule:              Rule
    triggered:         bool
    condition_results: list[ConditionResult]
    context:           EvaluationContext
    evaluated_at:      str


# ─────────────────────────────────────────────
# Escalation trigger
# ─────────────────────────────────────────────

class EscalationTrigger(BaseModel):
    session_id:   str
    tenant_id:    str
    rule_id:      str
    rule_name:    str
    target_pool:  str
    shadow_mode:  bool = False
    triggered_at: str
    context:      EvaluationContext
    customer_notice: str | None = None   # RUL-02 — da regra; o engine envia antes de escalar


# ─────────────────────────────────────────────
# Dry-run
# ─────────────────────────────────────────────

class DryRunRequest(BaseModel):
    tenant_id:           str
    rule:                Rule
    history_window_days: int = Field(default=30, ge=1, le=90)


class DryRunConversationResult(BaseModel):
    session_id:   str
    would_trigger: bool
    at_turn:      int | None
    context_at_trigger: EvaluationContext | None


class DryRunResult(BaseModel):
    rule_id:              str
    tenant_id:            str
    history_window_days:  int
    total_conversations:  int
    would_trigger_count:  int
    trigger_rate:         float
    sample_triggers:      list[DryRunConversationResult]
    simulated_at:         str


# ─────────────────────────────────────────────
# Session simulator
# ─────────────────────────────────────────────

class SessionSimulatorRequest(BaseModel):
    tenant_id:        str
    rule:             Rule
    sentiment_score:  float = Field(default=0.0, ge=-1.0, le=1.0)
    intent_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    turn_count:       int   = Field(default=1, ge=0)
    elapsed_ms:       int   = Field(default=0, ge=0)
    flags:            list[str] = Field(default_factory=list)


class SessionSimulatorResult(BaseModel):
    triggered:         bool
    condition_results: list[ConditionResult]
    target_pool:       str | None


# ─────────────────────────────────────────────
# Escalation decision
# ─────────────────────────────────────────────

class EscalationDecision(BaseModel):
    should_escalate: bool
    rule_id:         str | None = None
    pool_target:     str | None = None
    reason:          str | None = None
    mode:            Literal["active", "shadow"] | None = None


# ─────────────────────────────────────────────
# Evaluation sampling models
# ─────────────────────────────────────────────

class PoolEvaluationConfig(BaseModel):
    """Configuração de amostragem por pool, propagada do Agent Registry via Kafka."""
    sampling_rate:     float = Field(default=1.0, ge=0.0, le=1.0)
    skill_id_template: str   = "eval_{pool_id}_v1"

    def resolve_skill_id(self, pool_id: str) -> str:
        return self.skill_id_template.replace("{pool_id}", pool_id)


class ContactClosedEvent(BaseModel):
    """Evento contact_closed consumido de conversations.events."""
    tenant_id:         str
    contact_id:        str
    agent_id:          str
    agent_session_id:  str
    agent_type:        str
    pool_id:           str
    transcript_id:     str | None = None
    context_package:   dict[str, Any] = {}
    contact:           dict[str, Any] = {}
    outcome:           str = "resolved"


# ─────────────────────────────────────────────
# Rule management API models
# ─────────────────────────────────────────────

class RuleCreateRequest(BaseModel):
    rule_id:      str
    tenant_id:    str
    name:         str
    conditions:   list[Condition]
    logic:        RuleLogic       = "AND"
    target_pool:  str | None      = None
    priority:     int             = Field(default=1, ge=1, le=10)
    description:  str             = ""
    customer_notice: str | None   = Field(default=None, max_length=500)   # RUL-02


class RuleUpdateRequest(BaseModel):
    """RUL-03 — substitui o que a regra DECIDE; id, tenant e status não mudam por aqui."""
    name:         str
    conditions:   list[Condition] = Field(min_length=1)
    logic:        RuleLogic       = "AND"
    target_pool:  str | None      = None
    priority:     int             = Field(default=1, ge=1, le=10)
    customer_notice: str | None   = Field(default=None, max_length=500)


class RuleStatusPatch(BaseModel):
    status: RuleStatus


class DryRunApiRequest(BaseModel):
    start_date: str          # ISO date string
    end_date:   str          # ISO date string
    tenant_id:  str


class DryRunApiResponse(BaseModel):
    sessions_evaluated:    int
    would_have_escalated:  int
    escalation_rate:       float
    sample_sessions:       list[dict]
