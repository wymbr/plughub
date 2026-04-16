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

      // 1. Read session metadata (tenant, started_at, pool, sla target)
      let tenantId   = ""
      let startedAt  = Date.now()
      let slaTargetMs = 480_000  // 8 min default

      try {
        const metaRaw = await redis.get(`session:${parsed.session_id}:meta`)
        if (metaRaw) {
          const meta = JSON.parse(metaRaw) as Record<string, string>
          tenantId  = meta["tenant_id"] ?? ""
          if (meta["started_at"]) startedAt = new Date(meta["started_at"]).getTime()
        }
      } catch { /* use defaults */ }

      // 2. Read AI state written by orchestrator-bridge per turn
      //    Key: session:{id}:ai → { current_turn: { partial_params, snapshot_at }, consolidated_turns: [...] }
      const raw = await redis.get(`session:${parsed.session_id}:ai`).catch(() => null)
      const ai  = raw ? JSON.parse(raw) as Record<string, unknown> : null

      const currentTurn = (ai?.["current_turn"] as Record<string, unknown>) ?? {}
      const partials    = (currentTurn["partial_params"] as Record<string, unknown>) ?? {}
      const turns       = (ai?.["consolidated_turns"] as Record<string, unknown>[]) ?? []
      const snapshotAt  = (currentTurn["snapshot_at"] as string) ?? new Date().toISOString()

      // 3. Check staleness — snapshot older than 30s means no recent AI activity
      const snapshotAge = Date.now() - new Date(snapshotAt).getTime()
      const isStale     = snapshotAge > 30_000

      // 4. Build sentiment trajectory from completed turns + current partial
      const trajectory: number[] = [
        ...turns.map((t) => Number(t["sentiment_score"] ?? 0)),
        Number(partials["sentiment_score"] ?? 0),
      ]
      const currentSentiment = Number(partials["sentiment_score"] ?? 0)

      // 5. Compute trend over last vs first window of trajectory
      let trend: "improving" | "stable" | "declining" = "stable"
      if (trajectory.length >= 3) {
        const window    = Math.min(3, Math.floor(trajectory.length / 2))
        const firstAvg  = trajectory.slice(0, window).reduce((a, b) => a + b, 0) / window
        const recentAvg = trajectory.slice(-window).reduce((a, b) => a + b, 0) / window
        const delta     = recentAvg - firstAvg
        if      (delta >  0.1) trend = "improving"
        else if (delta < -0.1) trend = "declining"
      }

      // 6. Read additional context (historical insights) if available
      //    Key: {tenant_id}:session:{id}:context — written by routing-engine insights consumer
      let ctxInsights: unknown[] = []
      if (tenantId) {
        try {
          const ctxRaw = await redis.get(`${tenantId}:session:${parsed.session_id}:context`)
          if (ctxRaw) {
            const ctx = JSON.parse(ctxRaw) as Record<string, unknown>
            ctxInsights = (ctx["historical_insights"] as unknown[]) ?? []
          }
        } catch { /* non-fatal */ }
      }

      // 7. SLA calculation
      const elapsedMs      = Date.now() - startedAt
      const urgency        = Math.min(elapsedMs / slaTargetMs, 1)
      const breachImminent = urgency > 0.85

      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({
            session_id: parsed.session_id,
            turn_count: turns.length,
            is_stale:   isStale,
            sentiment: {
              current:    currentSentiment,
              trajectory: trajectory.slice(0, -1),  // exclude current partial from history
              trend,
              alert:      currentSentiment < -0.5,
            },
            intent: {
              current:    partials["intent"]     ?? null,
              confidence: partials["confidence"] ?? 0,
              history:    turns.map((t) => t["intent"]).filter(Boolean),
            },
            flags: (partials["flags"] as string[]) ?? [],
            sla: {
              elapsed_ms:      elapsedMs,
              target_ms:       slaTargetMs,
              urgency:         Math.round(urgency * 100) / 100,
              breach_imminent: breachImminent,
            },
            snapshot_at: snapshotAt,
            customer_context: {
              historical_insights:   ctxInsights,
              conversation_insights: turns
                .flatMap((t) => (t["insights"] as unknown[]) ?? []),
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

      // 1. Read session meta to get tenant_id and pool_id
      let tenantId = ""
      let poolId   = ""

      try {
        const metaRaw = await redis.get(`session:${parsed.session_id}:meta`)
        if (metaRaw) {
          const meta = JSON.parse(metaRaw) as Record<string, string>
          tenantId = meta["tenant_id"] ?? ""
          poolId   = meta["pool_id"]   ?? ""
        }
      } catch { /* non-fatal */ }

      // If no pool_id in meta, try routing snapshot to find pool
      if (!poolId) {
        try {
          const aiAgents  = await redis.smembers(`session:${parsed.session_id}:ai_agents`)
          const humAgents = await redis.smembers(`session:${parsed.session_id}:human_agents`)
          const firstInst = aiAgents[0] ?? humAgents[0]
          if (firstInst) {
            const snapRaw = await redis.get(
              `session:${parsed.session_id}:routing:${firstInst}`
            )
            if (snapRaw) {
              const snap = JSON.parse(snapRaw) as Record<string, unknown>
              const snapshot = snap["snapshot"] as Record<string, unknown> | undefined
              poolId = (snapshot?.["pool_id"] as string) ?? ""
            }
          }
        } catch { /* non-fatal */ }
      }

      // 2. Fetch pool config from agent-registry to get supervisor_config.intent_capability_map
      const registryUrl = process.env["AGENT_REGISTRY_URL"] ?? "http://localhost:3200"
      let capabilities: unknown[] = []
      let escalations:  unknown[] = []
      let conferenceAgents: unknown[] = []

      if (poolId && tenantId) {
        try {
          const res = await fetch(`${registryUrl}/v1/pools/${poolId}`, {
            headers: { "x-tenant-id": tenantId },
          })
          if (res.ok) {
            const pool = await res.json() as Record<string, unknown>
            const supervisorCfg = pool["supervisor_config"] as Record<string, unknown> | null

            if (supervisorCfg) {
              // intent_capability_map: Record<intent, capability[]>
              const capMap = supervisorCfg["intent_capability_map"] as
                Record<string, unknown[]> | undefined

              if (capMap) {
                if (parsed.intent && capMap[parsed.intent]) {
                  // Filter to current intent only
                  capabilities = capMap[parsed.intent] ?? []
                } else {
                  // No intent filter — return all capabilities (deduplicated)
                  const allCaps = Object.values(capMap).flat()
                  const seen    = new Set<string>()
                  capabilities  = allCaps.filter((c) => {
                    const key = JSON.stringify(c)
                    if (seen.has(key)) return false
                    seen.add(key)
                    return true
                  })
                }
              }

              // escalation_pools: list of pool_ids available for escalation
              const escalPools = supervisorCfg["escalation_pools"] as string[] | undefined
              if (escalPools) {
                escalations = escalPools.map((pid) => ({ pool_id: pid }))
              }
            }

            // agent_types in this pool that can be invited to conference
            const agentTypes = pool["agent_types"] as Array<Record<string, unknown>> | undefined
            if (agentTypes) {
              conferenceAgents = agentTypes
                .filter((at) => (at["agent_type"] as Record<string, unknown>)?.["type"] === "ai")
                .map((at) => {
                  const agentType = at["agent_type"] as Record<string, unknown>
                  return {
                    agent_type_id: agentType["agent_type_id"],
                    name:          agentType["name"],
                    pool_id:       poolId,
                  }
                })
            }
          }
        } catch { /* agent-registry unavailable — return empty */ }
      }

      return {
        content: [{
          type: "text" as const,
          text: JSON.stringify({
            session_id:   parsed.session_id,
            pool_id:      poolId   || null,
            tenant_id:    tenantId || null,
            intent:       parsed.intent ?? null,
            capabilities,
            agents:       conferenceAgents,
            escalations,
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

      const SESSION_TTL = 14400  // 4h — mesma janela dos outros dados de sessão

      // ── Persistir registro do participante no Redis ───────────────────────
      // O bridge consulta estes dados para:
      //   - rotular mensagens do agente IA com channel_identity ao entregar ao cliente
      //   - espelhar mensagens em agent:events:{session_id} (humano vê tudo)
      //   - publicar conference.agent_completed ao agent_done
      //   - atribuir tier de stream (:full/:masked) baseado em data_policy
      const participantKey = `conference:${conference_id}:participant:${participant_id}`
      const identityKey    = `conference:${conference_id}:identity`
      const participantsKey = `conference:${conference_id}:participants`

      const channelIdentity = parsed.channel_identity ?? { text: "Assistente" }

      await Promise.all([
        // Registro completo do participante
        redis.set(participantKey, JSON.stringify({
          participant_id,
          conference_id,
          session_id:       parsed.session_id,
          agent_type_id:    parsed.agent_type_id,
          pool_id:          parsed.pool_id,
          interaction_model: parsed.interaction_model,
          role:             "sender",
          visibility:       "customer_visible",
          channel_identity: channelIdentity,
          joined_at,
        }), "EX", SESSION_TTL),

        // Identidade para lookup rápido no outbound_consumer (channel_identity.text)
        redis.set(identityKey, JSON.stringify(channelIdentity), "EX", SESSION_TTL),

        // SET de participant_ids — permite iterar participantes sem conhecer UUIDs
        redis.sadd(participantsKey, participant_id),
        redis.expire(participantsKey, SESSION_TTL),

        // Mapeamento inverso: session → conference_id (para bridge encontrar conferência)
        redis.set(
          `session:${parsed.session_id}:conference_id`,
          conference_id,
          "EX", SESSION_TTL,
        ),
      ])

      // ── Publicar convite de roteamento em conversations.inbound ──────────
      // O Routing Engine consome este tópico e trata como novo contato.
      // agent_type_id + conference_id + channel_identity sinalizam conferência:
      //   - Routing restrito ao agent_type_id declarado dentro do pool_id
      //   - conference_id propagado via RoutingResult → bridge → context_package
      //   - channel_identity propagado via RoutingResult → bridge → context_package
      await kafka.publish("conversations.inbound", {
        session_id:       parsed.session_id,
        tenant_id:        tenantId,
        customer_id:      customerId,
        channel,
        pool_id:          parsed.pool_id,
        agent_type_id:    parsed.agent_type_id,
        conference_id,
        channel_identity: channelIdentity,
        started_at:       joined_at,
        elapsed_ms:       0,
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
            channel_identity:  channelIdentity,
            status:            "joining",
            joined_at,
          }),
        }],
      }
    }
  )
}
