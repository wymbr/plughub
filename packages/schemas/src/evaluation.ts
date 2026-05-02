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
 * score    → numeric 0–max_score
 * boolean  → yes/no
 * choice   → one of predefined options
 * text     → free text note (no score — informational only)
 */
export const EvaluationCriterionTypeSchema = z.enum(["score", "boolean", "choice", "text"])
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
 */
export const EvaluationCriterionSchema = z.object({
  criterion_id:  z.string().min(1),
  /** Links this criterion to its parent dimension */
  dimension_id:  z.string().min(1),
  label:         z.string().min(1),
  description:   z.string().optional(),
  type:          EvaluationCriterionTypeSchema,
  /** Relative weight within its dimension (0–1) */
  weight:        z.number().min(0).max(1).default(1),
  /** Upper bound of the numeric scale. Only relevant when type = "score" */
  max_score:     z.number().positive().default(10),
  /** Predefined answer options. Only relevant when type = "choice" */
  options:       z.array(z.string()).optional(),
  /** Whether the evaluator may mark this criterion as not applicable */
  na_allowed:    z.boolean().default(false),
  required:      z.boolean().default(true),
  /** Example transcripts for evaluator calibration */
  examples: z.object({
    good: z.array(z.string()).default([]),
    bad:  z.array(z.string()).default([]),
  }).optional(),
})
export type EvaluationCriterion = z.infer<typeof EvaluationCriterionSchema>

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
  ]),
  /** 0–100 percentage. Required when mode = "percentage" */
  percentage:       z.number().min(0).max(100).optional(),
  /** Sessions per period. Required when mode = "count" */
  count_per_period: z.number().int().positive().optional(),
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
