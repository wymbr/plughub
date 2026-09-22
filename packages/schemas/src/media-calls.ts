/**
 * media-calls.ts
 * Intervalo de CHAMADA dentro de um contato (WCH-02, relatórios — 2026-09-22).
 *
 * Tópico Kafka: media.calls — produtor channel-gateway (`call_events.py`), consumidor analytics-api
 * → ClickHouse `call_intervals`. Chave de partição = `session_id`: início e fim da mesma chamada
 * viajam em ordem (o fim sem chave podia chegar antes do início — `conference-mechanics.md`
 * § Problema 34).
 *
 * A chamada é MEIO de um contato, não contato novo (ADR `adr-chat-call-as-medium.md`): um contato
 * de chat pode ter N chamadas, e cada uma é uma linha. `call_id` é o id da entrada
 * `media.call started` no stream da sessão — o discriminador do FENÔMENO (esta chamada), nunca o
 * do contêiner (a sessão), senão a segunda chamada apagaria a primeira.
 *
 *   call_started  a sala nasceu e o cliente recebeu o token (`webrtc.ready`)
 *   call_ended    a chamada acabou; traz a duração e o motivo
 *
 * `pool_id` é o pool de quem ATENDE (procedência da política de mídia, D10), não o de entrada.
 * Hoje só a chamada presa a contato `webchat` produz linha; a coluna `channel` existe para o canal
 * `webrtc` avulso e o telefone entrarem depois sem mudar o contrato.
 */

import { z } from "zod"

export const CALL_END_REASONS = [
  "customer_hangup", "customer_disconnect", "agent_hangup", "contact_closed",
] as const

const Common = {
  event_id:   z.string().uuid(),
  tenant_id:  z.string().min(1),
  session_id: z.string().min(1),
  call_id:    z.string().min(1),
  channel:    z.string().min(1),
  pool_id:    z.string().nullable(),
  /** O que o CLIENTE publica na chamada (teto aplicado no SFU). */
  customer_publish: z.array(z.enum(["audio", "video"])),
  started_at: z.string().datetime({ offset: true }),
}

export const CallStartedEventSchema = z.object({
  ...Common,
  event_type: z.literal("call_started"),
}).strict()

export const CallEndedEventSchema = z.object({
  ...Common,
  event_type:  z.literal("call_ended"),
  ended_at:    z.string().datetime({ offset: true }),
  duration_ms: z.number().int().nonnegative(),
  /** Motivo conhecido OU outro dito pelo produtor — nunca vazio. */
  end_reason:  z.string().min(1),
}).strict()

export const MediaCallEventSchema = z.discriminatedUnion("event_type", [
  CallStartedEventSchema,
  CallEndedEventSchema,
])

export type CallStartedEvent = z.infer<typeof CallStartedEventSchema>
export type CallEndedEvent   = z.infer<typeof CallEndedEventSchema>
export type MediaCallEvent   = z.infer<typeof MediaCallEventSchema>
