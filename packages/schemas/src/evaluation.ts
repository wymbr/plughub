/**
 * evaluation.ts
 * Schemas para o módulo Session Replayer / Evaluator + Arc 6 Evaluation Platform.
 *
 * Fluxo Arc 3 (Session Replayer):
 *   conversations.session_closed
 *     → Stream Persister (PostgreSQL)
 *     → Evaluation Orchestrator → evaluation.requested
 *         → Stream Hydrator (garante Redis populado)
 *         → Replayer (lê Redis, respeita timing)
 *             → evaluator agent recebe ReplayContext via evaluation_context_get
 *             → evaluator submete EvaluationResult via evaluation_submit
 *             → evaluation.completed publicado em evaluation.events
 *
 * Arc 6 — Plataforma de Avaliação de Qualidade:
 *   EvaluationForm / EvaluationCriterion / EvidenceRef / EvaluationCriterionResponse
 *   EvaluationCampaign / SamplingRules / ReviewerRules / CampaignSchedule
 *   EvaluationInstance / ReviewResult
 *   Extended EvaluationResult + ReplayContext
 *   Kafka events: evaluation.{submitted,review_requested,review_completed,contested,locked,...}
 *
 * Spec: PlugHub seção "Session Replayer" + Arc 6 "Plataforma de Avaliação de Qualidade"
 */

import { z } from "zod"
import { SessionIdSchema, ParticipantRoleSchema } from "./common"
import { MessageContentSchema } from "./message"
import { DataCategorySchema } from "./audit"

// ─────────────────────────────────────────────
// Dimensões de avaliação
// ─────────────────────────────────────────────

/**
 * EvaluationDimension — dimensão individual de qualidade.
 * Configurável por tenant (ex: empatia, resolução, conformidade, LGPD).
 */
export const EvaluationDimensionSchema = z.object({
  dimension_id: z.string().min(1),
  name:         z.string().min(1),
  score:        z.number().min(0).max(10),
  weight:       z.number().min(0).max(1).default(1),
  notes:        z.string().optional(),
  flags:        z.array(z.string()).default([]),
})
export type EvaluationDimension = z.infer<typeof EvaluationDimensionSchema>

// ─────────────────────────────────────────────
// Resultado de avaliação
// ─────────────────────────────────────────────

export const EvaluationResultSchema = z.object({
  evaluation_id:  z.string().uuid(),
  session_id:     SessionIdSchema,
  tenant_id:      z.string().min(1),
  evaluator_id:   z.string().min(1),     // instance_id do agente evaluator
  agent_type_id:  z.string().min(1),

  /** Score composto: média ponderada das dimensões (0–10) */
  composite_score: z.number().min(0).max(10),

  dimensions:    z.array(EvaluationDimensionSchema).default([]),

  /** Resumo narrativo gerado pelo evaluator */
  summary:       z.string().min(1),

  /** Pontos positivos detectados */
  highlights:    z.array(z.string()).default([]),

  /** Pontos de melhoria detectados */
  improvement_points: z.array(z.string()).default([]),

  /** Flags de conformidade (LGPD, script, escalação indevida, etc.) */
  compliance_flags: z.array(z.string()).default([]),

  /** Outcome alcançado na sessão original */
  session_outcome: z.string().optional(),

  /** True se o evaluator consideraria este contato como benchmark positivo */
  is_benchmark:  z.boolean().default(false),

  evaluated_at:  z.string().datetime(),

  /**
   * Relatório de comparação — presente apenas quando comparison_mode: true
   * na EvaluationRequest correspondente.
   */
  comparison:    z.lazy(() => ComparisonReportSchema).optional(),

  // ── Arc 6 — structured evaluation fields (optional, backward-compatible) ──

  /** EvaluationForm used for this evaluation (Arc 6) */
  form_id:              z.string().optional(),
  /** EvaluationCampaign that triggered this evaluation (Arc 6) */
  campaign_id:          z.string().optional(),
  /** EvaluationInstance tracking record (Arc 6) */
  instance_id:          z.string().uuid().optional(),
  /** Per-criterion structured responses (Arc 6) */
  criterion_responses:  z.array(z.lazy(() => EvaluationCriterionResponseSchema)).optional(),
  /** Lifecycle status of this evaluation result (Arc 6) */
  eval_status:          z.lazy(() => EvaluationInstanceStatusSchema).optional(),
  /** Knowledge snippets used by the evaluator agent (Arc 6 — mcp-server-knowledge RAG) */
  knowledge_snippets:   z.array(z.lazy(() => KnowledgeSnippetSchema)).optional(),
})
export type EvaluationResult = z.infer<typeof EvaluationResultSchema>

// ─────────────────────────────────────────────
// Evento de stream reconstruído (para replay)
// ─────────────────────────────────────────────

/**
 * ReplayEvent — evento do stream canônico reconstituído para o replay.
 * Inclui original_content (evaluator tem acesso completo por design).
 */
export const ReplayEventSchema = z.object({
  event_id:         z.string().uuid(),
  type:             z.string(),
  timestamp:        z.string().datetime(),
  author:           z.object({
    participant_id: z.string(),
    role:           ParticipantRoleSchema,
  }).optional(),
  visibility:       z.union([
    z.literal("all"),
    z.literal("agents_only"),
    z.array(z.string()),
  ]).optional(),
  payload:          z.record(z.unknown()).default({}),
  /** Conteúdo original (desmascarado) — visível ao evaluator */
  original_content: MessageContentSchema.optional(),
  /** Categorias LGPD presentes neste evento */
  masked_categories: z.array(DataCategorySchema).default([]),
  /** Delta em ms desde o evento anterior — usado pelo Replayer para timing fiel */
  delta_ms:         z.number().nonnegative().default(0),
})
export type ReplayEvent = z.infer<typeof ReplayEventSchema>

// ─────────────────────────────────────────────
// ReplayContext — pacote entregue ao evaluator
// ─────────────────────────────────────────────

/**
 * ReplayContext — contexto completo da sessão reconstruída para avaliação.
 * Escrito no Redis pelo Replayer antes de notificar o evaluator agent.
 * Chave: {tenant_id}:replay:{session_id}:context   TTL: 1h
 */
export const ReplayContextSchema = z.object({
  session_id:    SessionIdSchema,
  tenant_id:     z.string().min(1),
  replay_id:     z.string().uuid(),

  /** Metadados da sessão original */
  session_meta: z.object({
    channel:      z.string(),
    opened_at:    z.string().datetime(),
    closed_at:    z.string().datetime().optional(),
    outcome:      z.string().optional(),
    close_reason: z.string().optional(),
    duration_ms:  z.number().nonnegative().optional(),
  }),

  /** Eventos do stream na ordem original com delta_ms para timing fiel */
  events:        z.array(ReplayEventSchema).default([]),

  /** Sentimento registrado na sessão (array de scores) */
  sentiment:     z.array(z.object({
    score:     z.number().min(-1).max(1),
    timestamp: z.string().datetime(),
  })).default([]),

  /** Participantes da sessão original */
  participants:  z.array(z.object({
    participant_id: z.string(),
    role:           ParticipantRoleSchema,
    agent_type_id:  z.string().optional(),
    joined_at:      z.string().datetime(),
    left_at:        z.string().datetime().optional(),
  })).default([]),

  /** Fator de velocidade aplicado — 1.0 = real-time, 10.0 = 10x mais rápido */
  speed_factor:  z.number().positive().default(1.0),

  /** Fonte dos dados: "redis" (hot) ou "postgres" (cold, após hydration) */
  source:        z.enum(["redis", "postgres"]),

  created_at:    z.string().datetime(),

  // ── Arc 6 — form-aware evaluation context (optional, backward-compatible) ──

  /** EvaluationForm to evaluate against — populated by evaluation-api (Arc 6) */
  evaluation_form:  z.lazy(() => EvaluationFormSchema).optional(),
  /** Campaign context that triggered this evaluation (Arc 6) */
  campaign_id:      z.string().optional(),
  /** EvaluationInstance tracking record (Arc 6) */
  instance_id:      z.string().uuid().optional(),
  /** Whether comparison_mode is active (Arc 3 compat) */
  comparison_mode:  z.boolean().default(false),
})
export type ReplayContext = z.infer<typeof ReplayContextSchema>

// ─────────────────────────────────────────────
// EvaluationRequest — publicado em evaluation.events
// ─────────────────────────────────────────────

export const EvaluationRequestSchema = z.object({
  event_type:      z.literal("evaluation.requested"),
  evaluation_id:   z.string().uuid(),
  session_id:      SessionIdSchema,
  tenant_id:       z.string().min(1),

  /** Pool de avaliadores destino */
  evaluator_pool:  z.string().min(1),

  /** Agent type solicitado — opcional, Routing Engine seleciona se omitido */
  agent_type_id:   z.string().optional(),

  /**
   * Fator de velocidade para o replay.
   * 1.0 = real-time (avalia latência do agente fielmente)
   * 10.0 = 10x mais rápido (avaliação em batch)
   */
  speed_factor:    z.number().positive().default(10.0),

  /**
   * comparison_mode: quando true, o Replayer captura as respostas de produção
   * do stream e as compara com as respostas geradas na sessão de replay.
   * Produz ComparisonReport junto com EvaluationResult.
   * TODO: comparator — implementação na próxima iteração.
   */
  comparison_mode: z.boolean().default(false),

  /** Dimensões a avaliar — se vazio, usa dimensões padrão do tenant */
  dimensions:      z.array(z.string()).default([]),

  requested_at:    z.string().datetime(),
})
export type EvaluationRequest = z.infer<typeof EvaluationRequestSchema>

// ─────────────────────────────────────────────
// ComparisonReport — produção vs replay
// ─────────────────────────────────────────────

/**
 * ComparisonReport — resultado da comparação turn-a-turn entre a sessão de
 * produção e a sessão de replay.
 *
 * Casos de uso:
 *   - Validação de upgrade de modelo (antes de promover nova versão)
 *   - Calibração do evaluator (verifica se distingue sessões boas/ruins)
 *   - Detecção de regressão em atualizações de prompt
 *
 * Presente em EvaluationResult.comparison quando comparison_mode: true.
 * TODO: comparator — implementação na próxima iteração.
 */
export const ComparisonReportSchema = z.object({
  /** Score de similaridade semântica médio entre respostas (0–1) */
  similarity_score: z.number().min(0).max(1),

  /** Turns onde os outputs diferem além do threshold configurado */
  divergence_points: z.array(z.object({
    turn_index:        z.number().int().nonnegative(),
    production_text:   z.string(),
    replay_text:       z.string(),
    similarity:        z.number().min(0).max(1),
  })).default([]),

  /** Outcome diferiu entre produção e replay? */
  outcome_delta: z.object({
    production_outcome: z.string(),
    replay_outcome:     z.string(),
    diverged:           z.boolean(),
  }).optional(),

  /** Sentimento final diferiu? */
  sentiment_delta: z.object({
    production_final: z.number().min(-1).max(1),
    replay_final:     z.number().min(-1).max(1),
    delta:            z.number(),
  }).optional(),

  /** Latência média de resposta diferiu? */
  latency_delta: z.object({
    production_avg_ms: z.number().nonnegative(),
    replay_avg_ms:     z.number().nonnegative(),
    delta_ms:          z.number(),
  }).optional(),
})
export type ComparisonReport = z.infer<typeof ComparisonReportSchema>

// ═════════════════════════════════════════════════════════════════════════════
// Arc 6 — Plataforma de Avaliação de Qualidade
// ═════════════════════════════════════════════════════════════════════════════

// ─────────────────────────────────────────────
// Task #178 — EvaluationForm + Criterion + EvidenceRef + CriterionResponse
// ─────────────────────────────────────────────

/**
 * Type of answer expected for a criterion.
 * score         → numeric 0–max_score
 * boolean       → yes/no
 * choice        → one of predefined options
 * text          → free text note (no score — informational only)
 * auto_computed → computed automatically from session_metric.* — no LLM needed (Arc 13)
 */
export const EvaluationCriterionTypeSchema = z.enum(["score", "boolean", "choice", "text", "auto_computed"])
export type EvaluationCriterionType = z.infer<typeof EvaluationCriterionTypeSchema>

/**
 * EvaluationDimensionDef — dimension group definition within a form.
 * (Not the same as EvaluationDimension which is a scoring result.)
 */
export const EvaluationDimensionDefSchema = z.object({
  dimension_id:  z.string().min(1),
  name:          z.string().min(1),
  description:   z.string().optional(),
  /** Relative weight of this dimension in the composite score (0–1) */
  weight:        z.number().min(0).max(1).default(1),
  /** How criteria within this dimension are aggregated */
  aggregation:   z.enum(["weighted_average", "min_score"]).default("weighted_average"),
})
export type EvaluationDimensionDef = z.infer<typeof EvaluationDimensionDefSchema>

/**
 * EvaluationCriterion — a single evaluable question within a form.
 * Groups into dimensions; supports N/A answers and calibration examples.
 * Arc 13: dimension_label + auto_computed type.
 */
export const EvaluationCriterionSchema = z.object({
  criterion_id:      z.string().min(1),
  /** Links this criterion to its parent dimension */
  dimension_id:      z.string().min(1),
  /** Human-readable label of the dimension group (Arc 13) */
  dimension_label:   z.string().optional(),
  label:             z.string().min(1),
  description:       z.string().optional(),
  type:              EvaluationCriterionTypeSchema,
  /** Relative weight within its dimension (0–1) */
  weight:            z.number().min(0).max(1).default(1),
  /** Upper bound of the numeric scale. Only relevant when type = "score" */
  max_score:         z.number().positive().default(10),
  /** Predefined answer options. Only relevant when type = "choice" */
  options:           z.array(z.string()).optional(),
  /** Whether the evaluator may mark this criterion as not applicable */
  na_allowed:        z.boolean().default(false),
  required:          z.boolean().default(true),
  /** Example transcripts for evaluator calibration */
  examples: z.object({
    good: z.array(z.string()).default([]),
    bad:  z.array(z.string()).default([]),
  }).optional(),
  // ── T6 — enriched criterion model (spec §5.3; add-only, all optional) ────────
  /** The question being evaluated. Canonical going forward; falls back to `description`. */
  question:          z.string().optional(),
  /** Scale anchoring: what 0 / mid / max mean. Empty = current behavior. */
  scoring_guidance:  z.string().optional(),
  /** Lower bound of the numeric scale (type="score"); pairs with max_score. */
  min_score:         z.number().default(0),
  /** option → score map (type="choice"). Aggregation consumes this in T7. */
  choice_scores:     z.record(z.string(), z.number()).optional(),
  /** boolean → score map (type="boolean"). */
  true_score:        z.number().optional(),
  false_score:       z.number().optional(),
  /** Guidance for when N/A applies. The N/A itself is contestable. */
  na_guidance:       z.string().optional(),
  /** Conditional applicability expression; empty = always applies. */
  applies_when:      z.string().optional(),
  /** Whether citing evidence is required. Absent → deriveEvidenceRequired(type). */
  evidence_required: z.boolean().optional(),
  /** Whether the criterion can be contested. Absent → deriveContestable(type)
   *  (auto_computed → false; everything else → true). */
  contestable:       z.boolean().optional(),
  // ── Arc 13 — auto_computed fields ──────────────────────────────────────────
  /** Source metric for auto_computed type. Format: "session_metric.{metric_name}" */
  computation_source: z.string().optional(),
  /** Value at or above/below which score = 1.0 */
  threshold_pass:     z.number().optional(),
  /** Value at or above/below which score = 0.0 */
  threshold_fail:     z.number().optional(),
  /** Direction of comparison for thresholds */
  comparison:         z.enum(["lt", "gt", "lte", "gte"]).optional(),
})
export type EvaluationCriterion = z.infer<typeof EvaluationCriterionSchema>

/**
 * T6 — derivation rules for criterion fields that are computed from `type` when
 * not explicitly set on the form. Keep these the single source of truth so the
 * evaluation-api (read-time normalization), the FormsPage UI, and the aggregation
 * (T7) all derive identically.
 */
export function deriveContestable(type: EvaluationCriterionType): boolean {
  // auto_computed is a deterministic fact (SessionMetricsExtractor) → not contestable.
  return type !== "auto_computed"
}

export function deriveEvidenceRequired(type: EvaluationCriterionType): boolean {
  // Evidence is meaningful for graded judgments; text is qualitative and
  // auto_computed is deterministic, so neither requires cited evidence by default.
  return type === "score" || type === "boolean"
}

/**
 * EvaluationForm — reusable structured evaluation template.
 * Assigned to campaigns; versioned for historical traceability.
 */
export const EvaluationFormSchema = z.object({
  form_id:       z.string().min(1),
  tenant_id:     z.string().min(1),
  name:          z.string().min(1),
  description:   z.string().optional(),
  /** Monotonic version counter — incremented on any structural change */
  version:       z.number().int().positive().default(1),
  dimensions:    z.array(EvaluationDimensionDefSchema).min(1),
  criteria:      z.array(EvaluationCriterionSchema).min(1),
  /** How dimension scores are combined into the composite score */
  scoring_method: z.enum(["weighted_average", "simple_average"]).default("weighted_average"),
  /** Informational passing threshold (0–10) — not enforced by the engine */
  min_passing_score: z.number().min(0).max(10).optional(),
  active:        z.boolean().default(true),
  created_at:    z.string().datetime(),
  updated_at:    z.string().datetime(),
})
export type EvaluationForm = z.infer<typeof EvaluationFormSchema>

/**
 * EvidenceRef — pointer to a specific event in the session transcript
 * that supports or justifies a criterion score.
 */
export const EvidenceRefSchema = z.object({
  /** ReplayEvent.event_id from the session stream */
  event_id:   z.string().uuid(),
  turn_index: z.number().int().nonnegative(),
  /** Short excerpt (≤500 chars) that justifies the assessment */
  quote:      z.string().max(500).optional(),
  /** Whether the evidence supports a positive, negative, or neutral assessment */
  category:   z.enum(["positive", "negative", "neutral"]).default("neutral"),
})
export type EvidenceRef = z.infer<typeof EvidenceRefSchema>

/**
 * EvaluationCriterionResponse — the evaluator's structured answer to one criterion.
 * Exactly one of score/boolean_value/choice_value/text_value is expected
 * (depending on criterion.type), unless na = true.
 */
export const EvaluationCriterionResponseSchema = z.object({
  criterion_id:  z.string().min(1),
  /** true when the criterion is not applicable to this session */
  na:            z.boolean().default(false),
  /** Numeric score (type = "score"). Must be 0–criterion.max_score */
  score:         z.number().min(0).optional(),
  /** Boolean answer (type = "boolean") */
  boolean_value: z.boolean().optional(),
  /** Selected option id (type = "choice") */
  choice_value:  z.string().optional(),
  /** Free text note (type = "text" — informational, no score impact) */
  text_value:    z.string().optional(),
  notes:         z.string().optional(),
  /** Evidence citations from the replay transcript */
  evidence:      z.array(EvidenceRefSchema).default([]),
})
export type EvaluationCriterionResponse = z.infer<typeof EvaluationCriterionResponseSchema>

// ─────────────────────────────────────────────
// Task #179 — EvaluationCampaign + SamplingRules + ReviewerRules +
//             CampaignSchedule + EvaluationInstance + ReviewResult
// ─────────────────────────────────────────────

/**
 * SamplingRules — controls which sessions are selected for evaluation.
 */
export const SamplingRulesSchema = z.object({
  /** Session selection mode */
  mode: z.enum([
    "all",          // every session is evaluated
    "percentage",   // N% of sessions matching filters
    "count",        // N sessions per scheduling period
    "targeted",     // only sessions matching explicit filters (no volume cap)
    "quota",        // R10 — per-agent cumulative deficit quota (fair coverage)
  ]),
  /** 0–100 percentage. Required when mode = "percentage" */
  percentage:       z.number().min(0).max(100).optional(),
  /** Sessions per period. Required when mode = "count" */
  count_per_period: z.number().int().positive().optional(),
  /** R10/R11 — per-agent target coverage (0–1) when mode = "quota". Human and AI
   * keyed separately (AI typically lower; runs 24×7). Fallback: legacy rate field. */
  quota_rate_human: z.number().min(0).max(1).optional(),
  quota_rate_ai:    z.number().min(0).max(1).optional(),
  /** Optional session attribute filters */
  filters: z.object({
    pools:           z.array(z.string()).optional(),
    channels:        z.array(z.string()).optional(),
    outcomes:        z.array(z.string()).optional(),
    min_duration_ms: z.number().nonnegative().optional(),
    max_duration_ms: z.number().nonnegative().optional(),
    /** Include sessions where the final sentiment score is below this threshold */
    sentiment_below: z.number().min(-1).max(1).optional(),
    /** Include sessions that carry any of these compliance flags */
    has_flags:       z.array(z.string()).optional(),
  }).optional(),
  /** When count is limited, controls how sessions are prioritised */
  priority: z.enum([
    "random",
    "worst_sentiment",
    "longest",
    "most_recent",
    "oldest_unevaluated",
  ]).default("random"),
})
export type SamplingRules = z.infer<typeof SamplingRulesSchema>

/**
 * ReviewerRules — controls automatic vs human review routing.
 */
export const ReviewerRulesSchema = z.object({
  /** Composite score ≥ this → auto-approve (skip human review) */
  auto_approve_above:      z.number().min(0).max(10).optional(),
  /** Composite score ≤ this → always require human review */
  require_review_below:    z.number().min(0).max(10).optional(),
  /** Compliance flags that always route to human review regardless of score */
  require_review_on_flags: z.array(z.string()).default([]),
  /** Pool from which human reviewers are selected; null = platform-wide reviewer role */
  reviewer_pool:           z.string().optional(),
  /** Percentage of auto-approved evaluations to randomly audit for calibration */
  random_audit_pct:        z.number().min(0).max(100).default(0),
})
export type ReviewerRules = z.infer<typeof ReviewerRulesSchema>

/**
 * CampaignSchedule — when a campaign runs.
 */
export const CampaignScheduleSchema = z.object({
  /** Recurrence type */
  type: z.enum(["once", "daily", "weekly", "monthly", "continuous"]),
  /** ISO-8601 campaign start; null = immediately */
  start_at: z.string().datetime().optional(),
  /** ISO-8601 campaign end; null = no end date */
  end_at:   z.string().datetime().optional(),
  /** Cron expression (overrides type for complex schedules) */
  cron:     z.string().optional(),
  /** Use calendar-api for business-hours-aware scheduling */
  business_hours: z.boolean().default(false),
})
export type CampaignSchedule = z.infer<typeof CampaignScheduleSchema>

/** Campaign lifecycle */
export const EvaluationCampaignStatusSchema = z.enum([
  "draft",      // being configured — not yet active
  "active",     // running; new sessions matching sampling are enqueued
  "paused",     // temporarily halted
  "completed",  // end_at reached or manually completed
  "archived",   // soft-deleted; kept for historical reports
])
export type EvaluationCampaignStatus = z.infer<typeof EvaluationCampaignStatusSchema>

/**
 * EvaluationCampaign — a configured, scheduled quality evaluation campaign.
 */
export const EvaluationCampaignSchema = z.object({
  campaign_id:     z.string().min(1),
  tenant_id:       z.string().min(1),
  name:            z.string().min(1),
  description:     z.string().optional(),
  /** EvaluationForm used for all instances of this campaign */
  form_id:         z.string().min(1),
  /** Pool from which evaluator agents are allocated */
  evaluator_pool:  z.string().min(1),
  /**
   * Pool being evaluated — used for sampling (only sessions handled by this
   * pool are eligible). Different from evaluator_pool (the pool that runs
   * the evaluator agents). Optional: null means all pools are sampled.
   */
  evaluation_pool_id:     z.string().optional(),
  /**
   * Calendar used for scheduling evaluation windows and SLA calculations.
   * Determines business hours for deadline computation and schedule windows.
   */
  evaluation_calendar_id: z.string().optional(),
  /**
   * GatewayConfig IDs available to evaluator agents in this campaign.
   * Allows campaign-specific model selection (e.g. a stronger model for
   * high-stakes quality evaluations). Empty = use agent-type defaults.
   */
  gateway_config_ids:     z.array(z.string()).default([]),
  sampling:        SamplingRulesSchema,
  reviewer:        ReviewerRulesSchema,
  schedule:        CampaignScheduleSchema,
  status:          EvaluationCampaignStatusSchema.default("draft"),
  created_by:      z.string().min(1),
  created_at:      z.string().datetime(),
  updated_at:      z.string().datetime(),
})
export type EvaluationCampaign = z.infer<typeof EvaluationCampaignSchema>

/** Instance lifecycle — mirrors the human-review workflow */
export const EvaluationInstanceStatusSchema = z.enum([
  "pending",       // sampled, waiting for evaluator assignment
  "in_progress",   // evaluator agent working on it
  "submitted",     // evaluator submitted result; pending review routing
  "under_review",  // in human review queue
  "approved",      // approved (auto or human)
  "contested",     // evaluated agent's team contests the result
  "revised",       // reviewer updated decision after contestation
  "locked",        // final — no further changes allowed
])
export type EvaluationInstanceStatus = z.infer<typeof EvaluationInstanceStatusSchema>

/**
 * ReviewResult — outcome of a single reviewer action (AI or human).
 */
export const ReviewResultSchema = z.object({
  reviewer_type: z.enum(["ai", "human"]),
  /** participant_id (AI) or user_id (human) */
  reviewer_id:   z.string().min(1),
  decision:      z.enum(["approve", "reject", "adjust"]),
  /** Per-criterion score overrides when decision = "adjust". Key = criterion_id */
  adjusted_scores: z.record(z.string(), z.number()).optional(),
  notes:         z.string().optional(),
  reviewed_at:   z.string().datetime(),
})
export type ReviewResult = z.infer<typeof ReviewResultSchema>

/**
 * EvaluationInstance — a single evaluation job tracking record.
 * One record per (campaign, session) pair. Tracks the full lifecycle
 * from sampling through locking.
 */
export const EvaluationInstanceSchema = z.object({
  instance_id:           z.string().uuid(),
  campaign_id:           z.string().min(1),
  session_id:            z.string().min(1),
  tenant_id:             z.string().min(1),
  form_id:               z.string().min(1),
  status:                EvaluationInstanceStatusSchema.default("pending"),
  /** instance_id of the evaluator agent assigned to this job */
  evaluator_instance_id: z.string().optional(),
  /** participant_id of the human reviewer (when routed for review) */
  reviewer_id:           z.string().optional(),
  /** UUID of the persisted EvaluationResult */
  evaluation_result_id:  z.string().uuid().optional(),
  review_result:         ReviewResultSchema.optional(),
  /** Free-text contestation submitted by the evaluated pool's representative */
  contestation_notes:    z.string().optional(),
  created_at:    z.string().datetime(),
  updated_at:    z.string().datetime(),
  submitted_at:  z.string().datetime().optional(),
  reviewed_at:   z.string().datetime().optional(),
  locked_at:     z.string().datetime().optional(),
})
export type EvaluationInstance = z.infer<typeof EvaluationInstanceSchema>

// ─────────────────────────────────────────────
// Task #180 — Kafka events for the evaluation lifecycle
// ─────────────────────────────────────────────

/**
 * KnowledgeSnippet — a RAG result from mcp-server-knowledge.
 * Placeholder type; full schema lives in mcp-server-knowledge package.
 */
export const KnowledgeSnippetSchema = z.object({
  snippet_id:   z.string().uuid(),
  content:      z.string().min(1),
  /** Semantic similarity score (0–1) returned by pgvector cosine search */
  score:        z.number().min(0).max(1),
  source_ref:   z.string().optional(),
  retrieved_at: z.string().datetime(),
})
export type KnowledgeSnippet = z.infer<typeof KnowledgeSnippetSchema>

// ── Kafka event schemas for topic: evaluation.events ────────────────────────

const _evalBase = z.object({
  event_id:    z.string().uuid(),
  campaign_id: z.string().min(1),
  instance_id: z.string().uuid(),
  session_id:  z.string().min(1),
  tenant_id:   z.string().min(1),
  timestamp:   z.string().datetime(),
})

/** evaluation.instance_created — a session was sampled and an instance created */
export const EvalInstanceCreatedSchema = _evalBase.extend({
  event_type: z.literal("evaluation.instance_created"),
  form_id:    z.string().min(1),
})
export type EvalInstanceCreated = z.infer<typeof EvalInstanceCreatedSchema>

/** evaluation.submitted — evaluator agent completed and submitted the evaluation */
export const EvalSubmittedSchema = _evalBase.extend({
  event_type:           z.literal("evaluation.submitted"),
  evaluator_instance_id: z.string(),
  composite_score:      z.number().min(0).max(10),
})
export type EvalSubmitted = z.infer<typeof EvalSubmittedSchema>

/** evaluation.review_requested — routed to human or AI reviewer */
export const EvalReviewRequestedSchema = _evalBase.extend({
  event_type:    z.literal("evaluation.review_requested"),
  reviewer_type: z.enum(["ai", "human"]),
  reviewer_id:   z.string().optional(),
})
export type EvalReviewRequested = z.infer<typeof EvalReviewRequestedSchema>

/** evaluation.review_completed — reviewer issued a decision */
export const EvalReviewCompletedSchema = _evalBase.extend({
  event_type: z.literal("evaluation.review_completed"),
  decision:   z.enum(["approve", "reject", "adjust"]),
  reviewer_id: z.string(),
})
export type EvalReviewCompleted = z.infer<typeof EvalReviewCompletedSchema>

/** evaluation.contested — evaluated agent's team filed a contestation */
export const EvalContestedSchema = _evalBase.extend({
  event_type:          z.literal("evaluation.contested"),
  contestation_notes:  z.string(),
})
export type EvalContested = z.infer<typeof EvalContestedSchema>

/** evaluation.locked — instance reached final state; no further changes allowed */
export const EvalLockedSchema = _evalBase.extend({
  event_type:      z.literal("evaluation.locked"),
  final_status:    z.enum(["approved", "revised"]),
  composite_score: z.number().min(0).max(10),
})
export type EvalLocked = z.infer<typeof EvalLockedSchema>

/** evaluation.campaign_status_changed — campaign lifecycle changed */
export const EvalCampaignStatusChangedSchema = z.object({
  event_type:  z.literal("evaluation.campaign_status_changed"),
  event_id:    z.string().uuid(),
  campaign_id: z.string().min(1),
  tenant_id:   z.string().min(1),
  old_status:  EvaluationCampaignStatusSchema,
  new_status:  EvaluationCampaignStatusSchema,
  timestamp:   z.string().datetime(),
})
export type EvalCampaignStatusChanged = z.infer<typeof EvalCampaignStatusChangedSchema>

/**
 * EvaluationLifecycleEventSchema — discriminated union of all evaluation Kafka events.
 * Topic: evaluation.events
 */
export const EvaluationLifecycleEventSchema = z.discriminatedUnion("event_type", [
  EvalInstanceCreatedSchema,
  EvalSubmittedSchema,
  EvalReviewRequestedSchema,
  EvalReviewCompletedSchema,
  EvalContestedSchema,
  EvalLockedSchema,
  EvalCampaignStatusChangedSchema,
])
export type EvaluationLifecycleEvent = z.infer<typeof EvaluationLifecycleEventSchema>

// ═════════════════════════════════════════════════════════════════════════════
// Arc 13 — Review, Contestation & Calibration
// ═════════════════════════════════════════════════════════════════════════════

// ─────────────────────────────────────────────
// ContestationPolicy — updated fields
// ─────────────────────────────────────────────

/**
 * ContestationPolicy — stored as JSONB in evaluation.campaigns.contestation_policy.
 * Arc 13 adds: max_rounds, contest_deadline_hours, use_business_hours,
 * reviewer_type, pre_review_enabled, pre_review_agent_pool.
 */
export const ContestationPolicySchema = z.object({
  /** Maximum contestation rounds (padrão 3, máx 5) */
  max_rounds:              z.number().int().min(1).max(5).default(3),
  /** Hours the evaluated agent has to file a contestation after result publication */
  contest_deadline_hours:  z.number().positive().default(48),
  /** Whether deadlines respect business hours via calendar-api */
  use_business_hours:      z.boolean().default(false),
  /**
   * Who reviews after contestation.
   * "ai"          → agente_revisor_v1 always
   * "human"       → pool-based human reviewer always
   * "ai_then_human" → AI first; if contested again → human
   */
  reviewer_type:           z.enum(["ai", "human", "ai_then_human"]).default("ai"),
  /** Whether to run AI pre-publication review before publishing to evaluated agent */
  pre_review_enabled:      z.boolean().default(false),
  /** Pool of the pre-publication reviewer agent (required if pre_review_enabled=true) */
  pre_review_agent_pool:   z.string().nullable().optional(),
  /** Hours the reviewer has to respond after contestation */
  review_deadline_hours:   z.number().positive().default(72),
  /** Roles allowed to contest (legacy ReviewerRules compat) */
  contestation_roles:      z.array(z.string()).default([]),
  /** Per-round reviewer role mappings (legacy compat) */
  review_roles_by_round:   z.record(z.string(), z.string()).default({}),
})
export type ContestationPolicy = z.infer<typeof ContestationPolicySchema>

// ─────────────────────────────────────────────
// Contestation state machine
// ─────────────────────────────────────────────

/**
 * ContestationState — state machine for Fluxo 1 (human agent evaluated).
 * Stored in evaluation.results.contestation_state.
 */
export const ContestationStateSchema = z.enum([
  "pre_review_pending",    // AI pre-reviewer active (not yet visible to evaluated agent)
  "contestation_open",     // result published; agent may contest within deadline
  "under_review",          // agent contested; reviewer working
  "timeout_contestation",  // deadline passed without contestation → finalized
  "timeout_review",        // reviewer deadline passed → finalized
  "closed_upheld",         // reviewer upheld original scores
  "closed_revised",        // reviewer revised one or more scores
])
export type ContestationState = z.infer<typeof ContestationStateSchema>

// ─────────────────────────────────────────────
// EvidenceEntry — Arc 13 format
// ─────────────────────────────────────────────

/**
 * EvidenceEntry — a stream entry excerpt used as evidence in a ContestationThread.
 * Different from EvidenceRef (Arc 6): uses stream_entry_id and relevance_note.
 */
export const EvidenceEntrySchema = z.object({
  /** ID in the canonical Redis stream (e.g. "1715700000000-0") */
  stream_entry_id: z.string().min(1),
  /** Short excerpt of the message (masked content) */
  excerpt:         z.string().max(500),
  /** Why this excerpt justifies the score */
  relevance_note:  z.string().max(500),
})
export type EvidenceEntry = z.infer<typeof EvidenceEntrySchema>

// ─────────────────────────────────────────────
// CalibrationSignal
// ─────────────────────────────────────────────

/**
 * CalibrationSignal — optional output from the pre-publication AI reviewer.
 * Stored as JSONB in ContestationThread.calibration_signal.
 * Triggers a CurationReview when present.
 */
export const CalibrationSignalSchema = z.object({
  severity:      z.enum(["low", "medium", "high"]),
  dimension_id:  z.string().min(1),
  /** Free-text observation about the evaluator's behaviour pattern */
  observation:   z.string().min(1),
  /** agent_type_id of the evaluator that generated the evaluated result */
  evaluator_id:  z.string().min(1),
  /** Skill version of the evaluator at the time of evaluation */
  skill_version: z.string().min(1),
})
export type CalibrationSignal = z.infer<typeof CalibrationSignalSchema>

// ─────────────────────────────────────────────
// ContestationThread
// ─────────────────────────────────────────────

/**
 * ContestationThread — append-only record for one actor's contribution to one dimension.
 * Rounds: 1=evaluator, 1.5(stored as 1, author=pre_reviewer_ai)=pre-pub reviewer,
 *          2=human_agent contest, 3=reviewer decision, 4+ = further rounds.
 */
export const ContestationThreadSchema = z.object({
  thread_id:               z.string().uuid(),
  evaluation_instance_id:  z.string().min(1),
  /** dimension_id or criterion_id (fallback for forms without explicit dimension_id) */
  dimension_id:            z.string().min(1),
  round:                   z.number().int().positive(),
  author_type:             z.enum([
    "evaluator_ai",
    "pre_reviewer_ai",
    "human_agent",
    "reviewer_ai",
    "human_reviewer",
  ]),
  /** user_id or agent_type_id of the author */
  author_id:               z.string().min(1),
  /** Justification, contestation text, or review decision rationale */
  text:                    z.string().min(1),
  /** Decision from reviewer (upheld/revised). Only present for reviewer authors. */
  decision:                z.enum(["upheld", "revised"]).nullable().optional(),
  /** Score set by reviewer when decision=revised */
  score_override:          z.number().min(0).nullable().optional(),
  /** Evidence entries from the session stream */
  evidence_entries:        z.array(EvidenceEntrySchema).default([]),
  /** Calibration signal from pre_reviewer_ai (optional) */
  calibration_signal:      CalibrationSignalSchema.nullable().optional(),
  created_at:              z.string().datetime(),
})
export type ContestationThread = z.infer<typeof ContestationThreadSchema>

// ─────────────────────────────────────────────
// CurationReview
// ─────────────────────────────────────────────

/**
 * CurationReview — a curation queue item, created by the Sampling Engine
 * (Fluxo 2 / AI-evaluated agents) or by a calibration_signal intake (Fluxo 1).
 */
export const CurationReviewStatusSchema = z.enum([
  "pending",       // awaiting curator assignment
  "approved",      // curator approved the evaluation as-is
  "recalibrated",  // curator created a CalibrationNote
  "bias_flagged",  // curator flagged systematic evaluator bias (high severity)
])
export type CurationReviewStatus = z.infer<typeof CurationReviewStatusSchema>

export const CurationReviewSchema = z.object({
  review_id:               z.string().uuid(),
  evaluation_instance_id:  z.string().min(1),
  /**
   * Trigger source. One or more comma-separated values:
   * "sampling_rule:score_extremes" | "sampling_rule:deploy_baseline" | ... | "reviewer_signal"
   */
  trigger:                 z.string().min(1),
  /** user_id of the assigned curator (null = pending assignment) */
  curator_id:              z.string().nullable().optional(),
  status:                  CurationReviewStatusSchema.default("pending"),
  /** Curator's notes — complement to the calibration_signal observation */
  curator_notes:           z.string().nullable().optional(),
  /** FK to CalibrationNote if one was generated from this review */
  calibration_note_id:     z.string().uuid().nullable().optional(),
  created_at:              z.string().datetime(),
  resolved_at:             z.string().datetime().nullable().optional(),
})
export type CurationReview = z.infer<typeof CurationReviewSchema>

// ─────────────────────────────────────────────
// CalibrationNote
// ─────────────────────────────────────────────

/**
 * CalibrationNote — generated by curator from a CurationReview.
 * Published to the campaign's knowledge namespace → read by agente_avaliacao_v1 via RAG.
 */
export const CalibrationNoteSchema = z.object({
  note_id:        z.string().uuid(),
  campaign_id:    z.string().min(1),
  dimension_id:   z.string().min(1),
  /** agent_type_id of the evaluator to be calibrated */
  evaluator_id:   z.string().min(1),
  /** Skill version of the evaluator at detection time */
  skill_version:  z.string().min(1),
  /** Combined note: AI signal observation + curator complement */
  text:           z.string().min(1),
  severity:       z.enum(["low", "medium", "high"]),
  /** True after ingestion into the campaign knowledge namespace */
  published_to_kb: z.boolean().default(false),
  created_at:      z.string().datetime(),
})
export type CalibrationNote = z.infer<typeof CalibrationNoteSchema>

// ─────────────────────────────────────────────
// CurationSamplingRule
// ─────────────────────────────────────────────

/**
 * Rule types for the Sampling Engine (Fluxo 2 — AI-evaluated agents).
 * Evaluated in priority order; first match wins (unless multiple fire → merged trigger).
 */
export const CurationSamplingRuleTypeSchema = z.enum([
  "score_extremes",   // top/bottom N% of scores
  "deploy_baseline",  // first N evaluations after a new skill deploy
  "score_outlier",    // score deviates > X std_dev from agent_type average
  "na_excess",        // evaluator marked ≥ N criteria as na:true
  "random_baseline",  // fixed % of all evaluations
  "reviewer_signal",  // evaluator AI reviewer generated calibration_signal ≥ severity
])
export type CurationSamplingRuleType = z.infer<typeof CurationSamplingRuleTypeSchema>

export const CurationSamplingRuleSchema = z.object({
  rule_id:     z.string().uuid(),
  campaign_id: z.string().min(1),
  rule_type:   CurationSamplingRuleTypeSchema,
  /**
   * Rule-specific parameters (JSONB).
   * score_extremes:  { top_pct: float, bottom_pct: float }
   * deploy_baseline: { first_n: int }
   * score_outlier:   { std_dev_threshold: float }
   * na_excess:       { min_na_count: int }
   * random_baseline: { rate: float }  (0–1)
   * reviewer_signal: { min_severity: "low" | "medium" | "high" }
   */
  params:      z.record(z.unknown()).default({}),
  enabled:     z.boolean().default(true),
  /** Evaluation order — lower number = higher priority */
  priority:    z.number().int().nonnegative().default(10),
})
export type CurationSamplingRule = z.infer<typeof CurationSamplingRuleSchema>

// ─────────────────────────────────────────────
// Arc 13 Kafka events
// ─────────────────────────────────────────────

/** evaluation_finalized — canonical score emitted after full contestation cycle */
export const EvalFinalizedSchema = _evalBase.extend({
  event_type:               z.literal("evaluation_finalized"),
  final_score:              z.number().min(0).max(10),
  final_scores_by_dimension: z.array(z.object({
    dimension_id: z.string(),
    score:        z.number(),
  })).default([]),
  process_duration_ms: z.number().nonnegative(),
  contestation_state:  ContestationStateSchema,
})
export type EvalFinalized = z.infer<typeof EvalFinalizedSchema>

/** calibration_reviewed — curator resolved a CurationReview */
export const CalibrationReviewedSchema = z.object({
  event_type:              z.literal("calibration_reviewed"),
  event_id:                z.string().uuid(),
  review_id:               z.string().uuid(),
  campaign_id:             z.string().min(1),
  evaluation_instance_id:  z.string().min(1),
  skill_version:           z.string().min(1),
  evaluator_id:            z.string().min(1),
  decision:                CurationReviewStatusSchema,
  calibration_note_id:     z.string().uuid().nullable().optional(),
  tenant_id:               z.string().min(1),
  timestamp:               z.string().datetime(),
})
export type CalibrationReviewed = z.infer<typeof CalibrationReviewedSchema>

/** calibration_note_published — CalibrationNote ingested into knowledge namespace */
export const CalibrationNotePublishedSchema = z.object({
  event_type:   z.literal("calibration_note_published"),
  event_id:     z.string().uuid(),
  note_id:      z.string().uuid(),
  campaign_id:  z.string().min(1),
  evaluator_id: z.string().min(1),
  severity:     z.enum(["low", "medium", "high"]),
  tenant_id:    z.string().min(1),
  timestamp:    z.string().datetime(),
})
export type CalibrationNotePublished = z.infer<typeof CalibrationNotePublishedSchema>
