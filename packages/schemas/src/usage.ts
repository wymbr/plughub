/**
 * usage.ts
 * Schemas para o módulo de Usage Metering.
 *
 * Princípio: metering ≠ pricing.
 * Este schema registra o que foi consumido — sem preço, sem plano, sem regra de negócio.
 * O módulo de pricing (a construir) lê estes dados e decide o que cobrar.
 *
 * Tópico Kafka: usage.events
 *
 * Dimensões suportadas:
 *   Plataforma : sessions, messages
 *   IA         : llm_tokens_input, llm_tokens_output
 *   Canais     : whatsapp_conversations, voice_minutes, sms_segments, email_messages
 *   Infra      : storage_gb, data_transfer_gb, compute_ms  (reservadas para o futuro)
 */

import { z } from "zod"

// ─────────────────────────────────────────────
// Dimensões
// ─────────────────────────────────────────────

export const UsageDimensionSchema = z.enum([
  // Plataforma
  "sessions",
  "messages",
  // IA
  "llm_tokens_input",
  "llm_tokens_output",
  // Canais
  "whatsapp_conversations",
  "voice_minutes",
  "sms_segments",
  "email_messages",
  // Infra (reservado)
  "storage_gb",
  "data_transfer_gb",
  "compute_ms",
])
export type UsageDimension = z.infer<typeof UsageDimensionSchema>

// ─────────────────────────────────────────────
// Metadados por dimensão
// ─────────────────────────────────────────────

/** sessions */
export const SessionUsageMetaSchema = z.object({
  channel:     z.string(),                                         // whatsapp, webchat, voice, …
  outcome:     z.string().nullable().default(null),               // resolved, abandoned, …
  duration_ms: z.number().int().nonnegative().optional(),
})
export type SessionUsageMeta = z.infer<typeof SessionUsageMetaSchema>

/** messages */
export const MessageUsageMetaSchema = z.object({
  channel:    z.string(),
  visibility: z.string(),   // all | agents_only | participant_ids[]
})
export type MessageUsageMeta = z.infer<typeof MessageUsageMetaSchema>

/** llm_tokens_input / llm_tokens_output */
export const LlmTokenUsageMetaSchema = z.object({
  model_id:       z.string(),                        // ex: claude-sonnet-4-6
  agent_type_id:  z.string().optional(),
  gateway_id:     z.string().optional(),             // ai-gateway-operational | ai-gateway-evaluation
})
export type LlmTokenUsageMeta = z.infer<typeof LlmTokenUsageMetaSchema>

/** whatsapp_conversations */
export const WhatsappConversationMetaSchema = z.object({
  conversation_type: z.enum(["user_initiated", "business_initiated"]),
  template_id:       z.string().optional(),          // para business_initiated
})
export type WhatsappConversationMeta = z.infer<typeof WhatsappConversationMetaSchema>

/** voice_minutes */
export const VoiceMinutesMetaSchema = z.object({
  direction:        z.enum(["inbound", "outbound"]),
  destination_type: z.enum(["mobile", "landline", "toll_free"]).optional(),
  carrier_id:       z.string().optional(),
})
export type VoiceMinutesMeta = z.infer<typeof VoiceMinutesMetaSchema>

/** sms_segments */
export const SmsSegmentsMetaSchema = z.object({
  direction:    z.enum(["inbound", "outbound"]),
  country_code: z.string().length(2).optional(),   // ISO 3166-1 alpha-2
})
export type SmsSegmentsMeta = z.infer<typeof SmsSegmentsMetaSchema>

/** email_messages */
export const EmailMessageMetaSchema = z.object({
  direction: z.enum(["inbound", "outbound"]),
})
export type EmailMessageMeta = z.infer<typeof EmailMessageMetaSchema>

// ─────────────────────────────────────────────
// Componentes produtores
// ─────────────────────────────────────────────

export const UsageSourceComponentSchema = z.enum([
  "core",
  "channel-gateway",
  "ai-gateway",
  "session-replayer",
])
export type UsageSourceComponent = z.infer<typeof UsageSourceComponentSchema>

// ─────────────────────────────────────────────
// Evento de consumo — usage.events
// ─────────────────────────────────────────────

export const UsageEventSchema = z.object({
  /** Identificador único — garante idempotência no aggregator. */
  event_id:         z.string().uuid(),

  /** Tenant que gerou o consumo. */
  tenant_id:        z.string(),

  /** Sessão associada quando aplicável. */
  session_id:       z.string().nullable().default(null),

  /** Dimensão de consumo. */
  dimension:        UsageDimensionSchema,

  /**
   * Quantidade consumida na unidade da dimensão.
   * Ex: 1 para sessão/mensagem/avaliação, n para tokens, minutos de voz arredondados para cima.
   */
  quantity:         z.number().positive(),

  /** Momento exato do consumo. */
  timestamp:        z.string().datetime(),

  /** Componente que originou o evento. */
  source_component: UsageSourceComponentSchema,

  /**
   * Metadados de classificação específicos da dimensão.
   * O módulo de pricing usa estes campos para aplicar tarifas corretas.
   * Não validado aqui com discriminated union para manter o schema extensível
   * sem breaking changes quando novas dimensões forem adicionadas.
   */
  metadata:         z.record(z.unknown()).default({}),
})
export type UsageEvent = z.infer<typeof UsageEventSchema>

// ─────────────────────────────────────────────
// Contador agregado (Redis + PostgreSQL)
// ─────────────────────────────────────────────

/** Estrutura dos contadores Redis: {tenant_id}:usage:current:{dimension} */
export const UsageCounterSchema = z.object({
  tenant_id:   z.string(),
  dimension:   UsageDimensionSchema,
  quantity:    z.number().nonnegative(),
  cycle_start: z.string().datetime(),
})
export type UsageCounter = z.infer<typeof UsageCounterSchema>

/** Linha da tabela usage_hourly no PostgreSQL */
export const UsageHourlySchema = z.object({
  tenant_id: z.string(),
  dimension: UsageDimensionSchema,
  hour:      z.string().datetime(),   // truncado em hora
  quantity:  z.number().nonnegative(),
})
export type UsageHourly = z.infer<typeof UsageHourlySchema>

// ─────────────────────────────────────────────
// Quota — configuração mínima sem pricing
// ─────────────────────────────────────────────

/**
 * Limite operacional de uma dimensão para um tenant.
 * Armazenado em Redis: {tenant_id}:quota:limit:{dimension}
 * Escrito pelo operador agora; pelo pricing module depois.
 * Ausente = sem limite.
 */
export const QuotaLimitSchema = z.object({
  tenant_id:    z.string(),
  dimension:    UsageDimensionSchema,
  limit:        z.number().positive(),
  /** Percentual de uso que dispara alerta antes de bloquear (0–1). */
  soft_limit_pct: z.number().min(0).max(1).default(0.8),
  /** Se true, bloqueia ao atingir o limite. Se false, permite e registra overage. */
  hard_block:   z.boolean().default(true),
})
export type QuotaLimit = z.infer<typeof QuotaLimitSchema>

// ─────────────────────────────────────────────
// Evento de reset de ciclo — usage.cycle_reset
// ─────────────────────────────────────────────

/** Publicado pelo Pricing Module para sinalizar início de novo ciclo. */
export const UsageCycleResetSchema = z.object({
  tenant_id:       z.string(),
  new_cycle_start: z.string().datetime(),
  previous_totals: z.record(UsageDimensionSchema, z.number()).optional(),
})
export type UsageCycleReset = z.infer<typeof UsageCycleResetSchema>
