/**
 * stream.ts
 * Stream canônico — fonte da verdade para todos os eventos de sessão.
 * Redis key: session:{id}:stream
 * Fonte da verdade: plughub_spec_v1.docx seção 5.5
 */

import { z } from "zod"
import { SessionIdSchema, ParticipantRoleSchema, ChannelSchema, MediumTypeSchema } from "./common"
import { MessageContentSchema, MessageVisibilitySchema } from "./message"
import { DataCategorySchema } from "./audit"
import { CustomerIdentitySchema, ParticipantSchema, SentimentEntrySchema } from "./session"

// ─────────────────────────────────────────────
// Tipos de evento
// ─────────────────────────────────────────────

export const StreamEventTypeSchema = z.enum([
  "session_opened",
  "session_closed",
  // Arc 19 — webhook (workflow) session lifecycle
  "session_suspended",       // suspend step reached — waiting for external resume signal
  "session_resumed",         // resume token received — engine re-activated
  "participant_joined",
  "participant_left",
  "customer_identified",
  "medium_transitioned",
  "channel_transitioned",    // proposta de mudança de canal aceita pelo cliente
  "message",
  "interaction_request",
  "interaction_result",
  "flow_step_completed",
])
export type StreamEventType = z.infer<typeof StreamEventTypeSchema>

// ─────────────────────────────────────────────
// Author no stream
// ─────────────────────────────────────────────

export const StreamAuthorSchema = z.object({
  participant_id: z.string().uuid(),
  role:           ParticipantRoleSchema,
  channel:        ChannelSchema.optional(),
  medium:         MediumTypeSchema.optional(),
})
export type StreamAuthor = z.infer<typeof StreamAuthorSchema>

// ─────────────────────────────────────────────
// Payloads específicos por tipo de evento
// ─────────────────────────────────────────────

const SessionOpenedPayloadSchema = z.object({
  channel:     ChannelSchema,
  medium:      MediumTypeSchema,
  origin:      z.string(),
  destination: z.string(),
  gateway_id:  z.string(),
  metadata:    z.record(z.unknown()).default({}),
})

const SessionClosedPayloadSchema = z.object({
  outcome:      z.string().optional(),
  close_reason: z.string().optional(),
  // Arc 14 Fase B: targeted close — only the listed participant_ids receive teardown.
  // null / absent = broadcast to all subscribers (existing behaviour).
  recipients:   z.array(z.string()).nullable().optional(),
})

const ParticipantJoinedPayloadSchema = z.object({
  participant: ParticipantSchema,
})

const ParticipantLeftPayloadSchema = z.object({
  participant_id: z.string().uuid(),
  reason:         z.string().optional(),
})

const CustomerIdentifiedPayloadSchema = z.object({
  identity: CustomerIdentitySchema,
})

const MediumTransitionedPayloadSchema = z.object({
  from_medium: MediumTypeSchema,
  to_medium:   MediumTypeSchema,
})

const ChannelTransitionedPayloadSchema = z.object({
  from_channel:  ChannelSchema,
  to_channel:    ChannelSchema,
  requested_by:  z.string().uuid(),   // participant_id
  reason:        z.string().optional(),
})

const MessagePayloadSchema = z.object({
  content:              MessageContentSchema,
  /** Conteúdo mascarado entregue aos agentes */
  masked_content:       MessageContentSchema.optional(),
  original_content:     MessageContentSchema.optional(), // LGPD: apenas roles autorizados
  masked:               z.boolean().default(false),
  masked_categories:    z.array(DataCategorySchema).default([]),
})

const InteractionRequestPayloadSchema = z.object({
  interaction_id:  z.string().uuid(),
  interaction_type: z.enum(["button", "list", "checklist", "form", "text"]),
  prompt:          z.string(),
  options:         z.array(z.object({
    id:    z.string(),
    label: z.string(),
  })).optional(),
  fields:          z.array(z.object({
    id:       z.string(),
    label:    z.string(),
    type:     z.string(),
    required: z.boolean().default(false),
  })).optional(),
  timeout_s:       z.number().int(),
})

const InteractionResultPayloadSchema = z.object({
  interaction_id: z.string().uuid(),
  result:         z.union([z.string(), z.array(z.string()), z.record(z.unknown())]),
})

const FlowStepCompletedPayloadSchema = z.object({
  step_id:   z.string(),
  step_type: z.string(),
  output_as: z.string().optional(),
  duration_ms: z.number().int().nonnegative().optional(),
})

// Arc 19 — webhook session suspend/resume payloads

/**
 * Published by orchestrator-bridge when the skill-flow engine returns
 * outcome: "suspended" for a webhook pool session.
 * Visibility: agents_only — no external channel delivery.
 */
const SessionSuspendedPayloadSchema = z.object({
  /** The suspend step ID that initiated the suspension. */
  step_id:           z.string(),
  /** Opaque token the caller must supply to resume. */
  resume_token:      z.string(),
  /** ISO-8601 UTC deadline. After this, the step follows on_timeout. */
  resume_expires_at: z.string(),
})

/**
 * Published by the WebhookAdapter when a valid resume_token arrives at
 * POST /v1/channels/webhook/resume/{token}.
 * Visibility: agents_only — triggers re-allocation by routing engine.
 */
const SessionResumedPayloadSchema = z.object({
  /** The suspend step ID being resumed. */
  step_id:      z.string(),
  /** The token that was consumed (single-use). */
  resume_token: z.string(),
  /** Caller-supplied payload (approved/rejected/input decision + data). */
  payload:      z.record(z.unknown()).optional(),
})

// ─────────────────────────────────────────────
// StreamEvent — evento base + payload tipado
// ─────────────────────────────────────────────

export const StreamEventSchema = z.object({
  event_id:   z.string().uuid(),
  session_id: SessionIdSchema,
  type:       StreamEventTypeSchema,
  timestamp:  z.string().datetime(),
  author:     StreamAuthorSchema.optional(),
  visibility: MessageVisibilitySchema.default("all"),

  // Payload varia por tipo — armazenado como registro genérico no stream
  // Deserialização tipada é responsabilidade do consumidor
  payload:    z.record(z.unknown()).default({}),
})
export type StreamEvent = z.infer<typeof StreamEventSchema>

// ─────────────────────────────────────────────
// Payloads exportados para deserialização tipada
// ─────────────────────────────────────────────

export const StreamPayloads = {
  session_opened:        SessionOpenedPayloadSchema,
  session_closed:        SessionClosedPayloadSchema,
  // Arc 19 — webhook session lifecycle
  session_suspended:     SessionSuspendedPayloadSchema,
  session_resumed:       SessionResumedPayloadSchema,
  participant_joined:    ParticipantJoinedPayloadSchema,
  participant_left:      ParticipantLeftPayloadSchema,
  customer_identified:   CustomerIdentifiedPayloadSchema,
  medium_transitioned:   MediumTransitionedPayloadSchema,
  channel_transitioned:  ChannelTransitionedPayloadSchema,
  message:               MessagePayloadSchema,
  interaction_request:   InteractionRequestPayloadSchema,
  interaction_result:    InteractionResultPayloadSchema,
  flow_step_completed:   FlowStepCompletedPayloadSchema,
} as const
