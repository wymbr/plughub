/**
 * message.ts
 * Tipos de mensagem e visibilidade.
 * Fonte da verdade: plughub_spec_v1.docx seções 4.4, 5
 */

import { z } from "zod"
import { ChannelSchema, MediumTypeSchema, ParticipantRoleSchema } from "./common"
import { DataCategorySchema } from "./audit"

// ─────────────────────────────────────────────
// Conteúdo de mensagem
// ─────────────────────────────────────────────

export const MessageContentTypeSchema = z.enum([
  "text",
  "image",
  "audio",
  "video",
  "file",
  "location",
  "template",
])
export type MessageContentType = z.infer<typeof MessageContentTypeSchema>

export const MessageContentSchema = z.object({
  type:     MessageContentTypeSchema,
  text:     z.string().optional(),
  url:      z.string().url().optional(),
  filename: z.string().optional(),
  mime:     z.string().optional(),
  caption:  z.string().optional(),
  metadata: z.record(z.unknown()).default({}),
})
export type MessageContent = z.infer<typeof MessageContentSchema>

// ─────────────────────────────────────────────
// Visibilidade — três modalidades distintas
// ─────────────────────────────────────────────

/**
 * MessageVisibility — três modalidades:
 *   "all"            → todos os participantes incluindo o cliente
 *   "agents_only"    → todos os agentes, sem o cliente
 *   string[]         → lista explícita de participant_ids (comunicação direta)
 *
 * A lista permite que um supervisor envie instrução privada a um agente específico
 * sem que os outros participantes da sessão vejam.
 */
export const MessageVisibilitySchema = z.union([
  z.literal("all"),
  z.literal("agents_only"),
  z.array(z.string().uuid()),   // participant_ids explícitos
])
export type MessageVisibility = z.infer<typeof MessageVisibilitySchema>

// ─────────────────────────────────────────────
// Author
// ─────────────────────────────────────────────

export const AuthorSchema = z.object({
  participant_id: z.string().uuid(),
  role:           ParticipantRoleSchema,
  channel:        ChannelSchema.optional(),
  medium:         MediumTypeSchema.optional(),
})
export type Author = z.infer<typeof AuthorSchema>

// ─────────────────────────────────────────────
// Message
// ─────────────────────────────────────────────

export const MessageSchema = z.object({
  message_id:          z.string().uuid(),
  session_id:          z.string(),
  timestamp:           z.string().datetime(),
  author:              AuthorSchema,
  content:             MessageContentSchema,
  visibility:          MessageVisibilitySchema,

  // Mascaramento LGPD
  masked:              z.boolean().default(false),
  masked_categories:   z.array(DataCategorySchema).default([]),

  // LGPD audit trail — presente apenas para roles autorizados (evaluator, reviewer).
  // primary e specialist nunca recebem original_content — operam via tokens inline.
  original_content:    MessageContentSchema.optional(),
})
export type Message = z.infer<typeof MessageSchema>
