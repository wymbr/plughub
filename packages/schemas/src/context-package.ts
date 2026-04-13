/**
 * context-package.ts
 * Schema Zod para o context_package da PlugHub Platform.
 * Fonte da verdade: PlugHub spec v24.0 seções 3.4, 3.4a, 4.2
 *
 * O context_package não é construído no momento do handoff —
 * é o estado da sessão acumulado turno a turno no Redis.
 */

import { z } from "zod"

// ─────────────────────────────────────────────
// Primitivos reutilizáveis
// ─────────────────────────────────────────────

export const ChannelSchema = z.enum([
  "chat",
  "whatsapp",
  "sms",
  "voice",
  "email",
  "webrtc",
])
export type Channel = z.infer<typeof ChannelSchema>

export const OutcomeSchema = z.enum([
  "resolved",
  "escalated_human",
  "transferred_agent",
  "callback",         // exclusivo outbound
])
export type Outcome = z.infer<typeof OutcomeSchema>

export const ExecutionModelSchema = z.enum(["stateless", "stateful"])
export type ExecutionModel = z.infer<typeof ExecutionModelSchema>

// ─────────────────────────────────────────────
// Customer Profile
// ─────────────────────────────────────────────

export const CustomerTierSchema = z.enum(["platinum", "gold", "standard"])
export type CustomerTier = z.infer<typeof CustomerTierSchema>

export const CustomerProfileSchema = z.object({
  customer_id:  z.string().uuid(),
  tenant_id:    z.string(),
  tier:         CustomerTierSchema,
  ltv:          z.number().nonnegative().optional(),
  churn_risk:   z.number().min(0).max(1).optional(),
  /** Preferência de canal declarada pelo cliente */
  preferred_channel: ChannelSchema.optional(),
})
export type CustomerProfile = z.infer<typeof CustomerProfileSchema>

// ─────────────────────────────────────────────
// Conversation Insights e Pending Deliveries
// Spec 3.4a: mesmo modelo, diferenciado pela categoria
// ─────────────────────────────────────────────

export const InsightStatusSchema = z.enum([
  "pending",
  "offered",
  "accepted",
  "delivered",
  "consumed",
  "expired",
  "replaced",
])

export const InsightConfidenceSchema = z.enum([
  "confirmed",   // cliente confirmou explicitamente
  "inferred",    // agente inferiu do contexto
  "mentioned",   // cliente mencionou sem confirmar
])
export type InsightConfidence = z.infer<typeof InsightConfidenceSchema>

/**
 * Modelo unificado para conversation_insights e pending_deliveries.
 * A categoria usa notação hierárquica com separador ponto (a.b.c).
 * Prefixos reservados:
 *   insight.historico.*  — memória de longo prazo, carregada no início
 *   insight.conversa.*   — gerada na sessão atual, expira no encerramento
 *   outbound.*           — pending deliveries para Notification Agent
 */
export const SessionItemSchema = z.object({
  item_id:          z.string().uuid(),
  customer_id:      z.string().uuid(),
  tenant_id:        z.string(),
  category:         z.string().min(1),  // e.g. "insight.conversa.servico.falha"
  content:          z.unknown(),        // free-form structure defined by operator
  source:           z.string(),         // "crm" | "bpm" | "previous_agent" | etc.
  source_session_id: z.string().uuid().optional(),
  expires_at:       z.string().datetime().optional(),
  priority:         z.number().int().min(0).max(100).default(50),
  status:           InsightStatusSchema,
  /** Present only in insight.conversa.* */
  confidence:       InsightConfidenceSchema.optional(),
  /** Turn in which the insight was registered (insight.conversa.* only) */
  source_turn:      z.number().int().nonnegative().optional(),
  registered_at:    z.string().datetime().optional(),
})
export type SessionItem = z.infer<typeof SessionItemSchema>

// ─────────────────────────────────────────────
// Process Context (BPM)
// ─────────────────────────────────────────────

export const ProcessContextSchema = z.object({
  process_id:       z.string().optional(),
  process_instance: z.string().optional(),
  status:           z.string().optional(),
  /** Payload estruturado passado pelo BPM */
  payload:          z.record(z.unknown()).optional(),
})
export type ProcessContext = z.infer<typeof ProcessContextSchema>

// ─────────────────────────────────────────────
// Issue (item de issue_status)
// Spec 4.2: uma conversa pode ter múltiplos issues
// ─────────────────────────────────────────────

export const IssueStatusValueSchema = z.enum([
  "resolved",
  "unresolved",
  "transferred",
  "pending_callback",
])

export const IssueSchema = z.object({
  issue_id:    z.string(),
  description: z.string(),
  status:      IssueStatusValueSchema,
  resolved_at: z.string().datetime().optional(),
})
export type Issue = z.infer<typeof IssueSchema>

// ─────────────────────────────────────────────
// Context Package — Spec 3.4 + 3.4b
// ─────────────────────────────────────────────

export const ContextPackageSchema = z.object({
  // ── Identificadores da sessão ──
  session_id:   z.string().uuid(),
  tenant_id:    z.string(),
  channel:      ChannelSchema,

  // ── Dados do cliente ──
  customer_data: CustomerProfileSchema,

  // ── Contexto da conversa ──
  channel_context: z.object({
    /** Número de turnos até o momento */
    turn_count:       z.number().int().nonnegative(),
    started_at:       z.string().datetime(),
    /** Motivo do handoff quando contexto vem de escalação */
    handoff_reason:   z.string().optional(),
  }),

  /** Histórico de mensagens — usado por agentes stateless */
  conversation_history: z.array(z.object({
    role:      z.enum(["customer", "agent", "system"]),
    content:   z.string(),
    timestamp: z.string().datetime(),
    agent_id:  z.string().optional(),
  })),

  /** Resumo acumulado da conversa */
  conversation_summary: z.string().optional(),

  /** Histórico de intents detectados nesta sessão */
  intent_history: z.array(z.object({
    intent:     z.string(),
    confidence: z.number().min(0).max(1),
    turn:       z.number().int().nonnegative(),
    timestamp:  z.string().datetime(),
  })).default([]),

  /** Trajetória de sentiment nesta sessão */
  sentiment_trajectory: z.array(z.number().min(-1).max(1)).default([]),

  /** Resoluções tentadas antes deste handoff */
  attempted_resolutions: z.array(z.string()).default([]),

  // ── Insights e pendências ──
  /** insight.historico.* carregados no início + insight.conversa.* gerados na sessão */
  conversation_insights: z.array(SessionItemSchema).default([]),
  /** outbound.* — pendências para o Notification Agent */
  pending_deliveries:    z.array(SessionItemSchema).default([]),

  // ── Contexto de processo (BPM) ──
  process_context: ProcessContextSchema.optional(),

  // ── Pipeline state (orquestrador nativo) ──
  pipeline_state: z.record(z.unknown()).optional(),

  /**
   * Versão do schema — spec 3.4b.
   * Incrementado a cada mudança de schema. Agentes aplicam migration function
   * ao encontrar uma versão anterior à esperada.
   */
  schema_version: z.number().int().nonnegative().default(1),
})

export type ContextPackage = z.infer<typeof ContextPackageSchema>

// ─────────────────────────────────────────────
// Aliases canônicos — spec 3.4a modelo unificado
// ConversationInsight e PendingDelivery são o mesmo modelo (SessionItem)
// diferenciados pela categoria.
// ─────────────────────────────────────────────

/** insight.historico.* e insight.conversa.* — memória do cliente/sessão */
export const ConversationInsightSchema = SessionItemSchema
export type ConversationInsight = SessionItem

/** outbound.* — pendências para entrega via Notification Agent */
export const PendingDeliverySchema = SessionItemSchema
export type PendingDelivery = SessionItem

// ─────────────────────────────────────────────
// Agent Done — sinal de conclusão
// Spec 4.2: contrato de conclusão de qualquer agente
// ─────────────────────────────────────────────

/** @deprecated Use AgentDonePayloadSchema */
export const AgentDoneSchema = z.object({
  session_id:   z.string().uuid(),
  agent_id:     z.string(),
  outcome:      OutcomeSchema,
  issue_status: z.array(IssueSchema).min(1),

  /** Resumo do que foi feito neste turno de atendimento */
  resolution_summary: z.string().optional(),

  /** Context package atualizado — passado para o agente destino no handoff */
  context_package_final: ContextPackageSchema.optional(),

  /** Motivo de escalação — obrigatório quando outcome !== "resolved" */
  handoff_reason: z.string().optional(),

  /** Pipeline state atualizado (orquestrador nativo) */
  pipeline_state: z.record(z.unknown()).optional(),

  /** ID da conferência, se o agent_done encerra uma participação */
  conference_id:  z.string().uuid().optional(),
  participant_id: z.string().uuid().optional(),

  completed_at: z.string().datetime(),
}).refine(
  (data) => data.outcome === "resolved" || data.handoff_reason !== undefined,
  {
    message: "handoff_reason é obrigatório quando outcome !== 'resolved'",
    path: ["handoff_reason"],
  }
)

export type AgentDone = z.infer<typeof AgentDoneSchema>

/**
 * AgentDonePayload — contrato de conclusão de qualquer agente.
 * Spec 4.2: outcome obrigatório, issue_status obrigatório,
 * handoff_reason obrigatório quando outcome !== "resolved".
 */
export const AgentDonePayloadSchema = AgentDoneSchema
export type AgentDonePayload = AgentDone
