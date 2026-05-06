/**
 * lib/write-stream-entry.ts
 *
 * Função centralizada de escrita no stream canônico Redis.
 *
 * REGRA: todo XADD em session:{id}:stream DEVE passar por writeStreamEntry.
 * Nunca chamar xadd diretamente. Isso garante:
 *   - event_id sempre presente (UUID gerado se não fornecido)
 *   - segment_id sempre presente como campo flat (string vazia quando ausente)
 *   - author_id e author_role como campos flat → analytics-api lê sem JSON.parse
 *   - author JSON mantido para backward compat com session_context_get
 *   - Validação Zod em compile-time e runtime → nenhum entry incompleto entra no stream
 *
 * Se campos obrigatórios estiverem ausentes → ZodError lançado antes do xadd.
 * Callers decidem como tratar o erro — não há catch interno.
 */

import { z }                    from "zod"
import * as crypto               from "crypto"
import { StreamEventTypeSchema } from "@plughub/schemas"
import type { RedisClient }      from "../infra/redis"

const StreamEntryVisibilitySchema = z.union([
  z.enum(["all", "agents_only"]),
  z.array(z.string().min(1)),
])

/**
 * Campos obrigatórios para writeStreamEntry.
 *
 * author_id   — identificador flat do autor (participant_id para session tools,
 *               instance_id para BPM tools). Escrito como campo flat E dentro
 *               do JSON "author" para backward compat.
 * author_role — role flat do autor ("primary" | "specialist" | "supervisor" |
 *               "customer" | "ai" | ...). Idem.
 * payload     — objeto completo serializado como JSON no stream.
 * segment_id  — opcional; escrito como "" quando ausente (campo sempre presente).
 * event_id    — opcional; gerado como UUID quando ausente.
 * timestamp   — opcional; gerado como ISO-8601 quando ausente.
 */
export const StreamEntryInputSchema = z.object({
  stream_key:  z.string().min(1),
  type:        StreamEventTypeSchema,
  author_id:   z.string().min(1),
  author_role: z.string().min(1),
  visibility:  StreamEntryVisibilitySchema,
  payload:     z.record(z.unknown()),
  segment_id:  z.string().optional(),
  event_id:    z.string().uuid().optional(),
  timestamp:   z.string().optional(),
})

export type StreamEntryInput = z.infer<typeof StreamEntryInputSchema>

// ─── Função principal ─────────────────────────────────────────────────────────

/**
 * Escreve um entry canônico no Redis Stream da sessão.
 *
 * Layout de campos no stream (flat, para parsing confiável pelo analytics-api):
 *
 *   event_id    → UUID, sempre presente
 *   type        → tipo do evento (ex: "message", "interaction_request")
 *   timestamp   → ISO-8601
 *   author_id   → identificador flat do autor  [NOVO — analytics parsing confiável]
 *   author_role → role flat do autor           [NOVO — analytics parsing confiável]
 *   author      → JSON {participant_id, instance_id, role} para backward compat
 *   visibility  → JSON serializado ("all" | "agents_only" | string[])
 *   segment_id  → string, sempre presente ("" quando ausente)
 *   payload     → JSON serializado do payload completo
 *
 * Lança ZodError se campos obrigatórios ausentes ou inválidos.
 * Lança erro Redis se o xadd falhar.
 * Nunca engole erros — callers decidem como tratar.
 *
 * @returns event_id do entry escrito
 */
export async function writeStreamEntry(
  redis: RedisClient,
  entry: StreamEntryInput,
): Promise<string> {
  // Validação em runtime — lança ZodError se inválido
  const validated = StreamEntryInputSchema.parse(entry)

  const event_id  = validated.event_id  ?? crypto.randomUUID()
  const timestamp = validated.timestamp ?? new Date().toISOString()

  // JSON "author" — backward compat com session_context_get (lê obj["author"])
  const authorJson = JSON.stringify({
    participant_id: validated.author_id,
    instance_id:    validated.author_id,
    role:           validated.author_role,
  })

  await (redis as any).xadd(
    validated.stream_key,
    "*",
    // Campos obrigatórios — sempre presentes
    "event_id",    event_id,
    "type",        validated.type,
    "timestamp",   timestamp,
    // Campos flat de autor — NOVOS, eliminam JSON.parse no analytics-api
    "author_id",   validated.author_id,
    "author_role", validated.author_role,
    // Backward compat — session_context_get lê este campo
    "author",      authorJson,
    // Visibilidade como JSON (suporta "all", "agents_only" e arrays de participant_ids)
    "visibility",  JSON.stringify(validated.visibility),
    // segment_id sempre presente — elimina null-checks no analytics-api
    "segment_id",  validated.segment_id ?? "",
    // Payload completo como JSON
    "payload",     JSON.stringify(validated.payload),
  )

  return event_id
}
