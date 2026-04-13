/**
 * tools/supervisor.ts
 * Tools do Supervisor — consumidas pelo Agent Assist em pools humanos.
 * Spec: PlugHub v24.0 seção 3.2a
 *
 * Supervisor não é um agente — não tem ciclo de vida.
 * Lê o estado já disponível no Redis (gravado pelo AI Gateway a cada turno).
 * Disponível apenas para sessões em pools com supervisor_config.enabled: true.
 */

import { z }            from "zod"
import type { McpServer }     from "@modelcontextprotocol/sdk/server/mcp.js"
import type { Redis }         from "ioredis"
import type { KafkaProducer } from "../infra/kafka"

// ─────────────────────────────────────────────
// Deps
// ─────────────────────────────────────────────

export interface SupervisorDeps {
  redis: Redis
  kafka: KafkaProducer
}

// ─────────────────────────────────────────────
// Schemas de input
// ─────────────────────────────────────────────

const SupervisorStateInputSchema = z.object({
  session_id: z.string().uuid(),
})

const SupervisorCapabilitiesInputSchema = z.object({
  session_id: z.string().uuid(),
  /** Intent atual — para filtrar capabilities relevantes */
  intent:     z.string().optional(),
})

const AgentJoinConferenceInputSchema = z.object({
  session_id:    z.string().uuid(),
  agent_type_id: z.string(),
  /** Pool ao qual o agente IA pertence — fornecido pela UI com base em supervisor_capabilities */
  pool_id:       z.string(),
  /** Modelo de entrada na conferência */
  interaction_model: z.enum(["background", "conference"]),
  /** Identidade visual/vocal do agente IA na conferência */
  channel_identity: z.object({
    text:          z.string().optional(),
    voice_profile: z.string().optional(),
  }).optional(),
})

// ─────────────────────────────────────────────
// Registro das tools do Supervisor
// ─────────────────────────────────────────────

export function registerSupervisorTools(server: McpServer, deps: SupervisorDeps): void {
  const { redis, kafka } = deps

  // ── supervisor_state ────────────────────────
  server.tool(
    "supervisor_state",
    "Retorna estado atual da conversa (sentiment, intent, SLA, flags). Spec 3.2a. Apenas pools com supervisor_config.enabled.",
    SupervisorStateInputSchema.shape as any,
    async (input: Record<string, unknown>) => {
      const parsed = SupervisorStateInputSchema.parse(input)

      // TODO: validar que pool da sessão tem supervisor_config.enabled: true
      // TODO: ler diretamente do Redis da sessão — sem cálculo adicional
      // TODO: retornar is_stale: true se snapshot > 30s

      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({
            session_id: parsed.session_id,
            sentiment: {
              current:    0,        // TODO: ler do Redis
              trajectory: [],
              trend:      "stable", // improving | stable | declining
              alert:      false,
            },
            intent: {
              current:    null,     // TODO: ler do Redis
              confidence: null,
              history:    [],
            },
            flags:        [],       // ex: ["churn_signal", "high_value"]
            sla: {
              elapsed_ms:      0,
              target_ms:       480000,
              urgency:         0,
              breach_imminent: false,
            },
            turn_count:   0,
            snapshot_at:  new Date().toISOString(),
            is_stale:     false,
            customer_context: {
              history_window_days:  30,
              historical_insights:  [],
              conversation_insights: [],
            },
          }),
        }],
      }
    }
  )

  // ── supervisor_capabilities ─────────────────
  server.tool(
    "supervisor_capabilities",
    "Retorna capacidades disponíveis filtradas por intent atual. Spec 3.2a.",
    SupervisorCapabilitiesInputSchema.shape as any,
    async (input: Record<string, unknown>) => {
      const parsed = SupervisorCapabilitiesInputSchema.parse(input)

      // TODO: ler intent_capability_map do supervisor_config do pool
      // TODO: filtrar por intent atual
      // TODO: aplicar relevance_model se configurado
      // TODO: retornar apenas capacidades relevantes para o momento

      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({
            session_id:   parsed.session_id,
            intent:       parsed.intent ?? null,
            capabilities: [],  // TODO: filtrar do intent_capability_map
            agents:       [],  // agentes IA disponíveis para conferência
            escalations:  [],  // pools disponíveis para escalação
            snapshot_at:  new Date().toISOString(),
          }),
        }],
      }
    }
  )

  // ── agent_join_conference ───────────────────
  server.tool(
    "agent_join_conference",
    "Convida agente IA para conferência com agente humano e cliente. Spec 3.2a.",
    AgentJoinConferenceInputSchema.shape as any,
    async (input: Record<string, unknown>) => {
      const parsed = AgentJoinConferenceInputSchema.parse(input)

      const conference_id  = crypto.randomUUID()
      const participant_id = crypto.randomUUID()
      const joined_at      = new Date().toISOString()

      // ── Read session meta to get tenant_id, customer_id, channel ──────────
      // Written by channel-gateway on contact open.
      let tenantId   = ""
      let customerId = parsed.session_id  // fallback
      let channel    = "chat"

      try {
        const raw = await redis.get(`session:${parsed.session_id}:meta`)
        if (raw) {
          const meta = JSON.parse(raw) as Record<string, string>
          tenantId   = meta["tenant_id"]   ?? ""
          customerId = meta["customer_id"] ?? customerId
          channel    = meta["channel"]     ?? channel
        }
      } catch { /* non-fatal — routing engine will use defaults */ }

      if (!tenantId) {
        return {
          content: [{
            type: "text" as const,
            text: JSON.stringify({
              error:      "session_not_found",
              session_id: parsed.session_id,
              message:    "Session meta not found in Redis — is the session active?",
            }),
          }],
        }
      }

      // ── Publish routing invite to conversations.inbound ───────────────────
      // The Routing Engine consumes this topic and treats it as a new contact.
      // Fields agent_type_id + conference_id signal a conference invite:
      //   - Routing is restricted to the specified agent_type_id within pool_id
      //   - conference_id is propagated through RoutingResult → bridge →
      //     session_context so the AI agent knows it is in a conference
      await kafka.publish("conversations.inbound", {
        session_id:    parsed.session_id,
        tenant_id:     tenantId,
        customer_id:   customerId,
        channel,
        pool_id:       parsed.pool_id,
        agent_type_id: parsed.agent_type_id,
        conference_id,
        started_at:    joined_at,
        elapsed_ms:    0,
      })

      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({
            session_id:        parsed.session_id,
            conference_id,
            participant_id,
            agent_type_id:     parsed.agent_type_id,
            pool_id:           parsed.pool_id,
            interaction_model: parsed.interaction_model,
            status:            "joining",
            joined_at,
          }),
        }],
      }
    }
  )
}
