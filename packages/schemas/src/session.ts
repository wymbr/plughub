/**
 * session.ts
 * Session, Participant, SessionContext, CustomerIdentity, SentimentEntry.
 * Fonte da verdade: plughub_spec_v1.docx seções 4, 5
 */

import { z } from "zod"
import {
  ChannelSchema,
  MediumTypeSchema,
  SessionStatusSchema,
  CloseReasonSchema,
  SessionOutcomeSchema,
  ParticipantRoleSchema,
  SessionIdSchema,
  ParticipantIdSchema,
} from "./common"
import { MessageSchema, MessageVisibilitySchema } from "./message"

// ─────────────────────────────────────────────
// Identidade do cliente
// ─────────────────────────────────────────────

export const CustomerIdentitySchema = z.object({
  customer_id: z.string().optional(),
  name:        z.string().optional(),
  document:    z.string().optional(),   // CPF, CNPJ, passaporte, etc.
  phone:       z.string().optional(),
  email:       z.string().email().optional(),
  tier:        z.string().optional(),   // "platinum", "gold", "standard"
  metadata:    z.record(z.unknown()).default({}),
})
export type CustomerIdentity = z.infer<typeof CustomerIdentitySchema>

// ─────────────────────────────────────────────
// Sentimento — score apenas, label calculado na leitura
// ─────────────────────────────────────────────

/**
 * SentimentEntry — armazenado em Redis como array.
 * Label NÃO é persistido — calculado na leitura com faixas configuráveis por tenant.
 *
 * Faixas padrão (configuráveis):
 *   [ 0.3,  1.0] → "satisfied"
 *   [-0.3,  0.3] → "neutral"
 *   [-0.6, -0.3] → "frustrated"
 *   [-1.0, -0.6] → "angry"
 */
export const SentimentEntrySchema = z.object({
  score:     z.number().min(-1).max(1),
  timestamp: z.string().datetime(),
})
export type SentimentEntry = z.infer<typeof SentimentEntrySchema>

export const SentimentRangeSchema = z.object({
  min:   z.number().min(-1).max(1),
  max:   z.number().min(-1).max(1),
  label: z.string().min(1),
})
export type SentimentRange = z.infer<typeof SentimentRangeSchema>

export const SentimentConfigSchema = z.object({
  tenant_id: z.string().min(1),
  ranges:    z.array(SentimentRangeSchema).default([
    { min: 0.3,  max: 1.0,  label: "satisfied"  },
    { min: -0.3, max: 0.3,  label: "neutral"    },
    { min: -0.6, max: -0.3, label: "frustrated" },
    { min: -1.0, max: -0.6, label: "angry"      },
  ]),
})
export type SentimentConfig = z.infer<typeof SentimentConfigSchema>

// ─────────────────────────────────────────────
// Participante
// ─────────────────────────────────────────────

export const ParticipantSchema = z.object({
  participant_id:  ParticipantIdSchema,
  session_id:      SessionIdSchema,
  agent_type_id:   z.string(),
  instance_id:     z.string(),
  role:            ParticipantRoleSchema,
  pool_id:         z.string().optional(),
  visibility:      MessageVisibilitySchema,
  joined_at:       z.string().datetime(),
  left_at:         z.string().datetime().optional(),
})
export type Participant = z.infer<typeof ParticipantSchema>

// ─────────────────────────────────────────────
// Session
// ─────────────────────────────────────────────

export const SessionSchema = z.object({
  session_id:   SessionIdSchema,
  tenant_id:    z.string().min(1),
  status:       SessionStatusSchema,
  channel:      ChannelSchema,
  medium:       MediumTypeSchema,
  origin:       z.string().min(1),        // ANI
  destination:  z.string().min(1),        // DNIS
  gateway_id:   z.string().min(1),
  metadata:     z.record(z.unknown()).default({}),
  skill_id:     z.string().optional(),
  opened_at:    z.string().datetime(),
  closed_at:    z.string().datetime().optional(),
  close_reason: CloseReasonSchema.optional(),
  outcome:      SessionOutcomeSchema.optional(),
  tags:         z.array(z.string()).default([]),
})
export type Session = z.infer<typeof SessionSchema>

// ─────────────────────────────────────────────
// SessionContext — retornado por session_context_get
// ─────────────────────────────────────────────

/**
 * SessionContext — lido uma vez pelo agente ao iniciar.
 * Evita tráfego repetido nas filas.
 * Lido via session_context_get (MCP) — nunca direto do Redis.
 */
export const SessionContextSchema = z.object({
  session_id:   SessionIdSchema,
  tenant_id:    z.string().min(1),
  status:       SessionStatusSchema,
  channel:      ChannelSchema,
  medium:       MediumTypeSchema,
  origin:       z.string(),
  destination:  z.string(),
  gateway_id:   z.string(),
  metadata:     z.record(z.unknown()).default({}),
  customer:     CustomerIdentitySchema.optional(),
  participants: z.array(ParticipantSchema).default([]),
  /** Mensagens filtradas pela visibilidade do participant_id solicitante */
  messages:     z.array(MessageSchema).default([]),
  /** Array de scores — label calculado pelo leitor com SentimentConfig */
  sentiment:    z.array(SentimentEntrySchema).default([]),
  opened_at:    z.string().datetime(),
  skill_id:     z.string().optional(),
  tags:         z.array(z.string()).default([]),
})
export type SessionContext = z.infer<typeof SessionContextSchema>

// ─────────────────────────────────────────────
// AgentDone — contrato de conclusão do agente
// ─────────────────────────────────────────────

export const AgentDoneV2Schema = z.object({
  session_id:      SessionIdSchema,
  participant_id:  ParticipantIdSchema,
  agent_type_id:   z.string(),
  outcome:         SessionOutcomeSchema,
  issue_status:    z.string().min(1),       // sempre obrigatório e nunca vazio
  handoff_reason:  z.string().optional(),   // obrigatório quando outcome !== "resolved"
  completed_at:    z.string().datetime(),
}).refine(
  (data) => data.outcome === "resolved" || data.handoff_reason !== undefined,
  {
    message: "handoff_reason é obrigatório quando outcome !== 'resolved'",
    path: ["handoff_reason"],
  }
)
export type AgentDoneV2 = z.infer<typeof AgentDoneV2Schema>
