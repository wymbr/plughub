/**
 * tools/bpm.ts
 * Tools de BPM — consumidores externos (sistemas de negócio, orquestradores).
 * Spec: PlugHub v24.0 seção 9.4
 *
 * Estas tools são o contrato entre a plataforma e sistemas externos.
 * Nunca implementam lógica de negócio — roteiam para os componentes internos.
 */

import { z } from "zod"
import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import type { KafkaProducer } from "../infra/kafka"
import type { RedisClient }   from "../infra/redis"

// ─── Dependências injetadas ───────────────────────────────────────────────────

export interface BpmDeps {
  kafka: KafkaProducer
  redis: RedisClient
}

// ─────────────────────────────────────────────
// Schemas de input
// ─────────────────────────────────────────────

const ConversationStartInputSchema = z.object({
  channel:      z.enum(["chat", "whatsapp", "sms", "voice", "email", "webrtc"]),
  customer_id:  z.string().uuid(),
  tenant_id:    z.string(),
  /** Contexto inicial — intent detectado pelo Channel Layer */
  intent:       z.string().optional(),
  /** Payload de processo BPM — quando acionado por um workflow */
  process_context: z.object({
    process_id:       z.string().optional(),
    process_instance: z.string().optional(),
    status:           z.string().optional(),
    payload:          z.record(z.unknown()).optional(),
  }).optional(),
})

const ConversationStatusInputSchema = z.object({
  session_id: z.string().uuid(),
  tenant_id:  z.string(),
})

const ConversationEndInputSchema = z.object({
  session_id: z.string().uuid(),
  tenant_id:  z.string(),
  reason:     z.enum(["timeout", "cancelled", "system_error", "bpm_terminated"]),
})

const RuleDryRunInputSchema = z.object({
  tenant_id:    z.string(),
  /** Definição da regra a ser simulada */
  rule: z.object({
    name:       z.string(),
    expression: z.record(z.unknown()),
    target_pool: z.string(),
  }),
  /** Janela histórica em dias para simulação */
  history_window_days: z.number().int().min(1).max(90).default(30),
})

const NotificationSendInputSchema = z.object({
  /** session_id da conversa ativa */
  session_id: z.string(),
  /** Texto da mensagem a ser entregue ao cliente */
  message:    z.string().min(1),
  /** Canal de entrega — "session" → webchat da sessão atual */
  channel:    z.enum(["session", "whatsapp", "sms", "email"]).default("session"),
})

const ConversationEscalateInputSchema = z.object({
  /** session_id da conversa a ser escalada */
  session_id:     z.string(),
  /** Pool de destino (human pool) */
  target_pool:    z.string(),
  /** Estado completo do pipeline — transferido como contexto ao agente humano */
  pipeline_state: z.record(z.unknown()).optional(),
  /** Razão da escalada (para auditoria) */
  error_reason:   z.string().optional(),
})

// ─────────────────────────────────────────────
// Registro das tools de BPM
// ─────────────────────────────────────────────

export function registerBpmTools(server: McpServer, deps?: BpmDeps): void {

  // ── conversation_start ──────────────────────
  server.tool(
    "conversation_start",
    "Inicia um atendimento na plataforma. Retorna session_id para rastreamento.",
    ConversationStartInputSchema.shape as any,
    async (input: Record<string, unknown>) => {
      const parsed = ConversationStartInputSchema.parse(input)

      // TODO: publicar evento em Kafka conversations.inbound
      // TODO: acionar Routing Engine para alocação inicial
      // TODO: criar sessão no Redis com context_package inicial

      const session_id = crypto.randomUUID()
      const started_at = new Date().toISOString()

      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({
            session_id,
            customer_id: parsed.customer_id,
            channel:     parsed.channel,
            status:      "routing",
            started_at,
          }),
        }],
      }
    }
  )

  // ── conversation_status ─────────────────────
  server.tool(
    "conversation_status",
    "Retorna o estado atual de uma conversa em andamento.",
    ConversationStatusInputSchema.shape as any,
    async (input: Record<string, unknown>) => {
      const parsed = ConversationStatusInputSchema.parse(input)

      // TODO: ler sessão do Redis
      // TODO: retornar estado atual: status, agent_type_id alocado, sentiment, SLA

      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({
            session_id:     parsed.session_id,
            status:         "in_progress",  // routing | in_progress | completed | failed
            agent_type_id:  null,           // preenchido após alocação
            sentiment:      null,
            sla: {
              elapsed_ms:       0,
              target_ms:        480000,
              urgency:          0,
              breach_imminent:  false,
            },
            snapshot_at: new Date().toISOString(),
          }),
        }],
      }
    }
  )

  // ── conversation_end ────────────────────────
  server.tool(
    "conversation_end",
    "Encerra forçado uma conversa (timeout, cancelamento, erro de sistema).",
    ConversationEndInputSchema.shape as any,
    async (input: Record<string, unknown>) => {
      const parsed = ConversationEndInputSchema.parse(input)

      // TODO: publicar evento de encerramento forçado no Kafka
      // TODO: notificar agente ativo para graceful shutdown
      // TODO: registrar no audit log

      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({
            session_id: parsed.session_id,
            terminated: true,
            reason:     parsed.reason,
            ended_at:   new Date().toISOString(),
          }),
        }],
      }
    }
  )

  // ── rule_dry_run ────────────────────────────
  server.tool(
    "rule_dry_run",
    "Simula uma regra do Rules Engine contra histórico de conversas. Spec 3.2b.",
    RuleDryRunInputSchema.shape as any,
    async (input: Record<string, unknown>) => {
      const parsed = RuleDryRunInputSchema.parse(input)

      // TODO: consultar ClickHouse com janela histórica
      // TODO: avaliar expressão da regra contra cada conversa
      // TODO: retornar: quantas disparariam, quando, e para qual pool

      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({
            rule_name:           parsed.rule.name,
            target_pool:         parsed.rule.target_pool,
            history_window_days: parsed.history_window_days,
            simulation: {
              total_conversations: 0,    // TODO: consultar ClickHouse
              would_trigger:       0,
              trigger_rate:        0,
              sample_triggers:     [],
            },
            simulated_at: new Date().toISOString(),
            status: "stub — implementação requer conexão com ClickHouse",
          }),
        }],
      }
    }
  )

  // ── notification_send ───────────────────────
  server.tool(
    "notification_send",
    "Envia mensagem de texto ao cliente via canal da sessão. Usado pelo step notify do Skill Flow. Spec 4.7.",
    NotificationSendInputSchema.shape as any,
    async (input: Record<string, unknown>) => {
      const parsed = NotificationSendInputSchema.parse(input)

      // Look up contact_id via Redis key written by channel-gateway on connect.
      // Key: session:{session_id}:contact_id → contact_id string
      let contactId = parsed.session_id  // fallback: use session_id as contact_id
      if (deps?.redis) {
        try {
          const stored = await deps.redis.get(`session:${parsed.session_id}:contact_id`)
          if (stored) contactId = stored
        } catch {
          // ignore — use fallback
        }
      }

      const outbound = {
        type:       "message.text",
        contact_id: contactId,
        session_id: parsed.session_id,
        message_id: crypto.randomUUID(),
        channel:    "chat",
        direction:  "outbound",
        author:     { type: "agent_ai", id: "orchestrator" },
        content:    { type: "text", text: parsed.message },
        text:       parsed.message,   // kept for channel-gateway backward compat
        timestamp:  new Date().toISOString(),
      }

      if (deps?.kafka) {
        await deps.kafka.publish("conversations.outbound", outbound)
      }

      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({
            delivered:  true,
            session_id: parsed.session_id,
            contact_id: contactId,
            message_id: outbound.message_id,
            sent_at:    outbound.timestamp,
          }),
        }],
      }
    }
  )

  // ── conversation_escalate ───────────────────
  server.tool(
    "conversation_escalate",
    "Escala a conversa para um pool humano via Routing Engine. Usado pelo step escalate do Skill Flow. Spec 4.7 + 9.5i.",
    ConversationEscalateInputSchema.shape as any,
    async (input: Record<string, unknown>) => {
      const parsed = ConversationEscalateInputSchema.parse(input)

      // Retrieve stored session metadata from Redis (written by channel-gateway + orchestrator bridge)
      let contactId  = parsed.session_id
      let tenantId   = "default"
      let customerId = parsed.session_id
      let channel    = "chat"

      if (deps?.redis) {
        try {
          const meta = await deps.redis.get(`session:${parsed.session_id}:meta`)
          if (meta) {
            const parsed_meta = JSON.parse(meta) as Record<string, string>
            if (parsed_meta["contact_id"])  contactId  = parsed_meta["contact_id"]
            if (parsed_meta["tenant_id"])   tenantId   = parsed_meta["tenant_id"]
            if (parsed_meta["customer_id"]) customerId = parsed_meta["customer_id"]
            if (parsed_meta["channel"])     channel    = parsed_meta["channel"]
          }
        } catch {
          // ignore — use fallbacks
        }
      }

      // Publish ConversationInboundEvent to conversations.inbound so the Routing Engine
      // re-routes the session to the target_pool (human pool).
      // pool_id is set directly — the Routing Engine restricts its search to that pool only,
      // preventing re-allocation to an AI agent and ensuring the escalation reaches humans.
      const routingEvent = {
        session_id:   parsed.session_id,
        tenant_id:    tenantId,
        customer_id:  customerId,
        channel,
        pool_id:      parsed.target_pool,  // explicit target — no pool inference
        confidence:   0.0,   // confidence=0 → Routing Engine picks supervised mode
        started_at:   new Date().toISOString(),
        elapsed_ms:   0,
        process_context: {
          escalated_from: "skill_flow",
          error_reason:   parsed.error_reason,
          pipeline_state: parsed.pipeline_state,
        },
      }

      if (deps?.kafka) {
        await deps.kafka.publish("conversations.inbound", routingEvent)
      }

      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({
            escalated:   true,
            session_id:  parsed.session_id,
            target_pool: parsed.target_pool,
            tenant_id:   tenantId,
            escalated_at: new Date().toISOString(),
          }),
        }],
      }
    }
  )
}
