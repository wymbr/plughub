/**
 * evaluation.ts
 * Schemas para o módulo Session Replayer / Evaluator.
 *
 * Fluxo:
 *   conversations.session_closed
 *     → Stream Persister (PostgreSQL)
 *     → Evaluation Orchestrator → evaluation.requested
 *         → Stream Hydrator (garante Redis populado)
 *         → Replayer (lê Redis, respeita timing)
 *             → evaluator agent recebe ReplayContext via evaluation_context_get
 *             → evaluator submete EvaluationResult via evaluation_submit
 *             → evaluation.completed publicado em evaluation.events
 *
 * Spec: PlugHub seção "Session Replayer" (pending)
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
