/**
 * routing.ts
 * Contratos do Routing Engine — alocação, fila e estado de agente.
 * Fonte da verdade: plughub_spec_v1.docx seção 9
 *
 * AssignmentTicket  — resultado de uma decisão de roteamento
 * QueueEntry        — entrada na fila de espera de um pool
 * AgentState        — estado em tempo real de uma instância de agente
 * RoutingScore      — componentes do score calculado pelo algoritmo
 */

import { z } from "zod"
import { ChannelSchema, SessionIdSchema, ParticipantIdSchema } from "./common"

// ─────────────────────────────────────────────
// AgentState — estado em tempo real (Redis)
// ─────────────────────────────────────────────

export const AgentStatusSchema = z.enum([
  "ready",    // disponível para receber novas sessões
  "busy",     // atendendo ao máximo de sessões simultâneas
  "paused",   // pausado manualmente — excluído do roteamento (hard filter)
  "offline",  // desconectado ou heartbeat expirado
])
export type AgentStatus = z.infer<typeof AgentStatusSchema>

/**
 * AgentState — mantido no Redis pelo Routing Engine.
 * Atualizado em cada login/logout/ready/busy do agente.
 * Instâncias com heartbeat > 90s são automaticamente marcadas como offline.
 */
export const AgentStateSchema = z.object({
  participant_id:          ParticipantIdSchema,
  agent_type_id:           z.string().min(1),
  instance_id:             z.string().min(1),
  pool_id:                 z.string().min(1),
  status:                  AgentStatusSchema,
  current_session_count:   z.number().int().nonnegative(),
  max_concurrent_sessions: z.number().int().positive(),
  /** Canais que esta instância suporta (subconjunto dos canais do pool) */
  channels:                z.array(ChannelSchema).default([]),
  gateway_id:              z.string().min(1),
  last_heartbeat:          z.string().datetime().optional(),
  updated_at:              z.string().datetime(),
})
export type AgentState = z.infer<typeof AgentStateSchema>

// ─────────────────────────────────────────────
// RoutingScore — componentes do algoritmo
// ─────────────────────────────────────────────

/**
 * RoutingScore — detalhe dos fatores de score calculados pelo Routing Engine.
 * Armazenado no AssignmentTicket para auditoria e debugging.
 *
 * score_final = Σ(weight_x × factor_x) normalizado em [0, 1]
 */
export const RoutingScoreSchema = z.object({
  /** SLA: min(wait_time / sla_target, 1.0) — avaliação lazy no head da fila */
  sla:      z.number().min(0).max(1),
  /** Tempo de espera relativo ao SLA target */
  wait:     z.number().min(0).max(1),
  /** Tier do cliente (platinum > gold > standard) */
  tier:     z.number().min(0).max(1).optional(),
  /** Risco de churn calculado externamente */
  churn:    z.number().min(0).max(1).optional(),
  /** Indicador de prioridade de negócio customizável */
  business: z.number().min(0).max(1).optional(),
  /** Score final ponderado */
  final:    z.number().min(0).max(1),
})
export type RoutingScore = z.infer<typeof RoutingScoreSchema>

// ─────────────────────────────────────────────
// QueueEntry — entrada na fila de um pool
// ─────────────────────────────────────────────

/**
 * QueueEntry — representa uma sessão aguardando alocação em um pool.
 * Armazenada em Redis (sorted set por priority_score).
 *
 * wait_key é um nonce que previne processamento duplicado quando múltiplas
 * instâncias do Routing Engine leem a fila concorrentemente.
 */
export const QueueEntrySchema = z.object({
  session_id:     SessionIdSchema,
  pool_id:        z.string().min(1),
  channel:        ChannelSchema,
  queued_at:      z.string().datetime(),
  priority_score: z.number(),             // higher = more urgent
  /** Nonce de deduplicação — gerado ao enfileirar, consumido ao dequeue */
  wait_key:       z.string().uuid(),
  /** Metadata adicional para o Queue Agent (se configurado no pool) */
  metadata:       z.record(z.unknown()).default({}),
})
export type QueueEntry = z.infer<typeof QueueEntrySchema>

// ─────────────────────────────────────────────
// AssignmentTicket — resultado de roteamento
// ─────────────────────────────────────────────

/**
 * AssignmentTicket — criado pelo Routing Engine após uma decisão de alocação.
 * Publicado em Kafka: conversations.routed
 *
 * Quando nenhum agente está disponível e o pool tem queue_config,
 * o Core ativa o Queue Agent e o ticket fica em status "queued".
 * Quando o agente é alocado da fila, o ticket é atualizado para "assigned".
 */
export const AssignmentStatusSchema = z.enum([
  "assigned",  // agente alocado imediatamente
  "queued",    // nenhum agente disponível — na fila
  "rejected",  // nenhum agente e sem fila — sessão encerrada (close_reason: no_resource)
])
export type AssignmentStatus = z.infer<typeof AssignmentStatusSchema>

export const AssignmentTicketSchema = z.object({
  ticket_id:       z.string().uuid(),
  session_id:      SessionIdSchema,
  pool_id:         z.string().min(1),
  status:          AssignmentStatusSchema,

  /** Preenchido quando status === "assigned" */
  agent_type_id:   z.string().optional(),
  participant_id:  ParticipantIdSchema.optional(),
  instance_id:     z.string().optional(),

  /** Detalhes do score para auditoria (presente quando status !== "rejected") */
  score:           RoutingScoreSchema.optional(),

  /** Posição na fila quando status === "queued" */
  queue_position:  z.number().int().positive().optional(),

  created_at:      z.string().datetime(),
  updated_at:      z.string().datetime(),
})
export type AssignmentTicket = z.infer<typeof AssignmentTicketSchema>
