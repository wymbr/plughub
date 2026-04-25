/**
 * common.ts
 * Primitivos base compartilhados entre todos os módulos.
 * Sem dependências de outros arquivos locais.
 * Fonte da verdade: plughub_spec_v1.docx
 */

import { z } from "zod"

// ─────────────────────────────────────────────
// Canal e Mídia
// ─────────────────────────────────────────────

/** Canal específico — hard filter no roteamento */
export const ChannelSchema = z.enum([
  "whatsapp",
  "webchat",
  "voice",
  "email",
  "sms",
  "instagram",
  "telegram",
  "webrtc",
])
export type Channel = z.infer<typeof ChannelSchema>

/** Tipo base de mídia — fator de score no roteamento, não hard filter */
export const MediumTypeSchema = z.enum(["voice", "video", "message", "email"])
export type MediumType = z.infer<typeof MediumTypeSchema>

// ─────────────────────────────────────────────
// Session
// ─────────────────────────────────────────────

export const SessionStatusSchema = z.enum(["active", "closed", "abandoned"])
export type SessionStatus = z.infer<typeof SessionStatusSchema>

export const CloseReasonSchema = z.enum([
  "no_resource",          // nenhum agente disponível e sem fila configurada
  "max_wait_exceeded",    // tempo máximo de fila estourado
  "customer_disconnect",  // cliente desconectou (connection_lost)
  "customer_hangup",      // cliente encerrou ativamente (voz/vídeo)
  "customer_abandon",     // cliente saiu antes de ser atendido
  "flow_complete",        // step complete do Skill Flow
  "agent_transfer",       // transferido para outro pool
  "agent_hangup",         // agente encerrou ativamente
  "session_timeout",      // sessão inativa além do TTL
  "system_error",         // erro irrecuperável
])
export type CloseReason = z.infer<typeof CloseReasonSchema>

export const SessionOutcomeSchema = z.enum([
  "resolved",
  "transferred",
  "abandoned",
  "error",
])
export type SessionOutcome = z.infer<typeof SessionOutcomeSchema>

// ─────────────────────────────────────────────
// Participantes
// ─────────────────────────────────────────────

export const ParticipantRoleSchema = z.enum([
  "primary",      // agente principal
  "specialist",   // especialista convidado (task assist)
  "supervisor",   // supervisor humano ou IA
  "evaluator",    // agente de qualidade
  "reviewer",     // revisor humano do evaluator
])
export type ParticipantRole = z.infer<typeof ParticipantRoleSchema>

// ─────────────────────────────────────────────
// session_id — formato com timestamp
// ─────────────────────────────────────────────

/**
 * Formato: sess_{YYYYMMDD}T{HHMMSS}_{ulid_random}
 * Exemplo: sess_20260420T103201_01HX5K3MNJP8QVWZ4RBCD
 * Gerado pelo Core no session_open.
 */
export const SessionIdSchema = z.string().regex(
  /^sess_\d{8}T\d{6}_[A-Z0-9]{20,26}$/,
  "session_id deve ter formato sess_YYYYMMDDTHHMMSS_ULID"
)

/**
 * participant_id — UUID v4
 */
export const ParticipantIdSchema = z.string().uuid()
