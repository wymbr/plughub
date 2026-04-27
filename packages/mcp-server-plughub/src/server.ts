/**
 * server.ts
 * Configuração do MCP Server da PlugHub Platform.
 * Spec: PlugHub v24.0 seções 9.4, 3.2a
 *
 * Transporte: SSE sobre HTTP (porta 3100 por padrão).
 * Múltiplos consumidores simultâneos — não usa stdio.
 */

import http                         from "http"
import express, { Request, Response } from "express"
import { WebSocketServer, WebSocket } from "ws"
import { McpServer }              from "@modelcontextprotocol/sdk/server/mcp.js"
import { SSEServerTransport }     from "@modelcontextprotocol/sdk/server/sse.js"
import { registerBpmTools }           from "./tools/bpm"
import type { BpmDeps }               from "./tools/bpm"
import { registerRuntimeTools }       from "./tools/runtime"
import type { RuntimeDeps }           from "./tools/runtime"
import { registerSessionTools }       from "./tools/session"
import type { SessionDeps }           from "./tools/session"
import { registerSupervisorTools }    from "./tools/supervisor"
import type { SupervisorDeps }        from "./tools/supervisor"
import { registerEvaluationTools }    from "./tools/evaluation"
import type { EvaluationDeps }        from "./tools/evaluation"
import { registerExternalAgentTools } from "./tools/external-agent"
import type { ExternalAgentDeps }     from "./tools/external-agent"
import { registerOperationalTools }  from "./tools/operational"
import type { OperationalDeps }      from "./tools/operational"
import { registerDelegationTools }  from "./tools/delegation"
import type { DelegationDeps }      from "./tools/delegation"
import { createRedisClient }       from "./infra/redis"
import { createKafkaProducer }     from "./infra/kafka"
import { createRegistryClient }    from "./infra/registry-client"
import { createPostgresClient }    from "./infra/postgres"
import { parseMentions }           from "./lib/mention-parser"

// ─────────────────────────────────────────────
// Configuração do servidor
// ─────────────────────────────────────────────

export interface ServerConfig {
  port:      number
  host:      string
  tenant_id?: string  // quando multi-tenant via env var
}

export interface AllDeps {
  runtime:    RuntimeDeps
  evaluation: EvaluationDeps
}

export function createServer(allDeps?: AllDeps): McpServer {
  const server = new McpServer({
    name:    "mcp-server-plughub",
    version: "1.0.0",
  })

  const kafka    = allDeps?.runtime.kafka    ?? createKafkaProducer()
  const redis    = allDeps?.runtime.redis    ?? createRedisClient()
  const registry = allDeps?.runtime.registry ?? createRegistryClient(
    process.env["AGENT_REGISTRY_URL"] ?? "http://localhost:3200"
  )

  const runtimeDeps: RuntimeDeps = { redis, kafka, registry }

  const evalDeps: EvaluationDeps = allDeps?.evaluation ?? {
    kafka,
    redis,
    postgres:         createPostgresClient(),
    proxyUrl:         process.env["MCP_PROXY_URL"]      ?? "http://localhost:7422",
    skillRegistryUrl: process.env["SKILL_REGISTRY_URL"] ?? "http://localhost:3400",
  }

  const bpmDeps: BpmDeps = { kafka, redis }

  const sessionDeps: SessionDeps = { redis, kafka }

  const supervisorDeps: SupervisorDeps = { redis, kafka }

  const externalAgentDeps: ExternalAgentDeps = { redis, kafka }

  const operationalDeps: OperationalDeps = { redis }

  const delegationDeps: DelegationDeps = {
    redis,
    skillFlowUrl: process.env["SKILL_FLOW_URL"]   ?? "http://localhost:3400",
    tenantId:     process.env["PLUGHUB_TENANT_ID"] ?? process.env["TENANT_ID"] ?? "tenant_demo",
  }

  // Registrar todas as tools
  registerBpmTools(server, bpmDeps)
  registerRuntimeTools(server, runtimeDeps)
  registerSessionTools(server, sessionDeps)
  registerSupervisorTools(server, supervisorDeps)
  registerEvaluationTools(server, evalDeps)
  registerExternalAgentTools(server, externalAgentDeps)
  registerOperationalTools(server, operationalDeps)
  registerDelegationTools(server, delegationDeps)

  return server
}

// ─────────────────────────────────────────────────────────────────────────────
// refreshPoolInstances — reset stuck instance state when an agent connects
// ─────────────────────────────────────────────────────────────────────────────
//
// In demo/dev environments agents do not publish Kafka agent_ready events, so
// the Routing Engine relies on the static seed (seed-demo.sh) for instance state.
// Problems that can leave the pool without a ready instance:
//   1. Instance key expired (TTL ran out — now fixed in seed: no TTL for instances)
//   2. mark_busy incremented current_sessions; _restore_instance never ran → key
//      may have status=busy or current_sessions > 0
//   3. Instance was removed from pool:instances set by mark_busy srem
//
// Recovery strategy (three sources for instance IDs, in priority order):
//   A. pool:instances set   — managed by routing engine
//   B. pool_roster:{poolId} — permanent set written by seed-demo.sh (no TTL)
//   C. KEYS instance:*      — full scan (last resort)
//
// For each instance found, the function:
//   - Resets current_sessions=0, status=ready
//   - Preserves the existing TTL (KEEPTTL via pre-read); if TTL=-1 (no TTL,
//     as written by the new seed), the SET is done without EX so it stays permanent
//   - If instance key is missing but a template exists (instance_template:{id},
//     also written by seed-demo.sh), recreates it from the template
//   - Ensures the ID is in pool:instances set (idempotent SADD)
//
// In production this function is harmless: lifecycle events keep state current,
// and any write here is quickly overwritten by the next agent_ready heartbeat.
async function refreshPoolInstances(
  poolId: string,
  redis: import("ioredis").default,
): Promise<void> {
  const tenantId    = process.env["PLUGHUB_TENANT_ID"] ?? "default"
  const poolInstKey = `${tenantId}:pool:${poolId}:instances`

  // ── Collect candidate instance IDs ────────────────────────────────────────
  const candidateIds = new Set<string>()

  // Source A: pool:instances set (routing engine managed)
  for (const id of await redis.smembers(poolInstKey)) candidateIds.add(id)

  // Source B: pool_roster:{poolId} (permanent, written by seed-demo.sh)
  for (const id of await redis.smembers(`${tenantId}:pool_roster:${poolId}`)) candidateIds.add(id)

  // Source C: KEYS scan — O(N) but acceptable in demo with small keyspace
  try {
    for (const key of await redis.keys(`${tenantId}:instance:*`)) {
      try {
        const raw = await redis.get(key)
        if (!raw) continue
        const inst = JSON.parse(raw) as Record<string, unknown>
        const pools = Array.isArray(inst["pools"]) ? (inst["pools"] as string[]) : []
        const pid   = typeof inst["pool_id"] === "string" ? inst["pool_id"] : ""
        if (pools.includes(poolId) || pid === poolId) {
          const iid = inst["instance_id"] as string | undefined
          if (iid) candidateIds.add(iid)
        }
      } catch { /* skip malformed key */ }
    }
  } catch { /* KEYS scan failed — continue with what we have */ }

  // ── Refresh / recreate each instance ─────────────────────────────────────
  let refreshed = 0
  for (const instanceId of candidateIds) {
    const key      = `${tenantId}:instance:${instanceId}`
    const tmplKey  = `${tenantId}:instance_template:${instanceId}`

    try {
      let raw = await redis.get(key)

      if (!raw) {
        // Instance key expired — try to recover from the permanent template
        raw = await redis.get(tmplKey)
        if (!raw) {
          console.warn(`[agent-ws] No template found for instance ${instanceId} — skipping`)
          continue
        }
        console.log(`[agent-ws] Recreating expired instance ${instanceId} from template`)
      }

      const inst = JSON.parse(raw) as Record<string, unknown>

      // Filter: only process instances that actually belong to this pool
      const pools = Array.isArray(inst["pools"]) ? (inst["pools"] as string[]) : []
      const pid   = typeof inst["pool_id"] === "string" ? inst["pool_id"] : ""
      if (!pools.includes(poolId) && pid !== poolId) continue

      inst["current_sessions"] = 0
      inst["status"]           = "ready"

      // Preserve TTL: -1 means no TTL (permanent — new seed behaviour).
      // >0 means key exists with a TTL; preserve it.
      // -2 means key was expired (we're recreating from template) → write permanent.
      const ttl = await redis.ttl(key)
      if (ttl > 0) {
        await redis.set(key, JSON.stringify(inst), "EX", ttl)
      } else {
        // No TTL (permanent) or key was expired → write without TTL
        await redis.set(key, JSON.stringify(inst))
      }
      refreshed++
    } catch (err) {
      console.error(`[agent-ws] Failed to refresh instance ${instanceId}:`, err)
    }

    // Always ensure the instance is in the routing pool set
    try {
      await redis.sadd(poolInstKey, instanceId)
    } catch { /* non-fatal */ }
  }

  if (candidateIds.size > 0) {
    console.log(`[agent-ws] Pool ${poolId}: found ${candidateIds.size} candidate(s), refreshed ${refreshed}`)
  } else {
    console.warn(`[agent-ws] Pool ${poolId}: no instances found — run seed-demo.sh`)
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// registerHumanAgent — create/refresh instance in Redis + notify routing engine
// ─────────────────────────────────────────────────────────────────────────────
//
// Called when a human agent connects to the Agent Assist UI WebSocket.
// Creates a stable instance_id for this pool (one per pool, not per browser tab)
// so the routing engine always has a consistent handle.
//
// Step 1 — Redis: upsert the instance directly so it is immediately visible to
//   the routing engine's Redis reads, even before Kafka is processed.
// Step 2 — Kafka `agent.lifecycle` / event=agent_ready: the routing engine's
//   LifecycleEventHandler.handle() picks this up and calls _drain_queue_for_agent,
//   which re-publishes any already-queued contacts back to conversations.inbound.
//
async function registerHumanAgent(
  poolId: string,
  redis:  import("ioredis").default,
  kafka:  { publish: (topic: string, payload: Record<string, unknown>) => Promise<void> },
): Promise<void> {
  const tenantId        = process.env["PLUGHUB_TENANT_ID"] ?? "tenant_demo"
  const registryUrl     = process.env["AGENT_REGISTRY_URL"] ?? "http://localhost:3300"
  const instanceId      = `human-${poolId}`   // stable per pool
  const now             = new Date().toISOString()

  // ── Step 0: ensure pool exists in Agent Registry (PostgreSQL) ──────────────
  //
  // The InstanceBootstrap reconciler (orchestrator-bridge) deletes any Redis
  // pool_config keys that are NOT present in the Agent Registry.  If the pool
  // was only written via seed-demo.ps1 (direct Redis write), the bootstrap will
  // silently wipe it on startup and every 5 minutes.
  //
  // Solution: POST the pool to the Agent Registry so it persists in PostgreSQL.
  // The Agent Registry publishes pool.registered → agent.registry.events →
  // routing-engine's RegistryEventHandler writes pool_config to Redis
  // immediately (no need to wait for the bootstrap cycle).
  // A 409 response means the pool already exists — that is fine.
  try {
    const poolPayload = {
      pool_id:       poolId,
      description:   `Human agent pool — ${poolId} (auto-registered on agent login)`,
      channel_types: ["webchat", "whatsapp"],
      sla_target_ms: 300_000,   // 5 minutes
    }
    const resp = await fetch(`${registryUrl}/v1/pools`, {
      method:  "POST",
      headers: {
        "Content-Type": "application/json",
        "x-tenant-id":  tenantId,
      },
      body: JSON.stringify(poolPayload),
    })
    if (resp.ok) {
      console.log(`[agent-ws] Pool registered in Agent Registry: pool=${poolId}`)
    } else if (resp.status === 409) {
      console.log(`[agent-ws] Pool already exists in Agent Registry: pool=${poolId}`)
    } else {
      console.warn(`[agent-ws] Pool registration returned HTTP ${resp.status}: pool=${poolId}`)
    }
  } catch (err) {
    // Non-fatal: if the Agent Registry is unreachable, fall through.
    // We still write to Redis directly below as a best-effort fallback.
    console.warn(`[agent-ws] Pool registration request failed (non-fatal): pool=${poolId}`, err)
  }

  const instance = {
    instance_id:      instanceId,
    agent_type_id:    `human_agent_${poolId}`,
    tenant_id:        tenantId,
    pool_id:          poolId,
    pools:            [poolId],
    execution_model:  "stateful",
    max_concurrent:   10,
    current_sessions: 0,
    status:           "ready",
    registered_at:    now,
    source:           "human_login",
  }

  // ── Step 1: write directly to Redis (immediate availability for routing reads)
  //
  // Even if the Agent Registry call succeeded, the routing engine's Kafka
  // consumer may not have processed pool.registered yet.  Writing the instance
  // and pool config directly to Redis ensures zero delay before the next
  // routing decision.
  await redis.set(`${tenantId}:instance:${instanceId}`, JSON.stringify(instance))
  await redis.sadd(`${tenantId}:pool:${poolId}:instances`, instanceId)
  await redis.sadd(`${tenantId}:pool_roster:${poolId}`, instanceId)

  // Ensure pool_config is present — needed by routing engine for channel
  // filtering and SLA scoring.  The RegistryEventHandler will overwrite this
  // when it processes pool.registered from Kafka, but that may take a few ms.
  const poolConfigKey = `${tenantId}:pool_config:${poolId}`
  const existingConfig = await redis.get(poolConfigKey)
  if (!existingConfig) {
    const poolConfig = {
      pool_id:       poolId,
      tenant_id:     tenantId,
      channel_types: ["webchat", "whatsapp"],
      sla_target_ms: 300_000,
      routing_expression: {
        weight_sla: 0.4, weight_wait: 0.2, weight_tier: 0.2,
        weight_churn: 0.1, weight_business: 0.1,
      },
      competency_weights: {},
      aging_factor:  0.4,
      breach_factor: 0.8,
      remote_sites:  [],
      is_human_pool: true,
    }
    await redis.set(poolConfigKey, JSON.stringify(poolConfig), "EX", 86_400)
    await redis.sadd(`${tenantId}:pools`, poolId)
    console.log(`[agent-ws] Pool config written to Redis (fallback): pool=${poolId}`)
  }

  // ── Step 2: publish agent_ready to agent.lifecycle ─────────────────────────
  //
  // The routing engine's LifecycleEventHandler calls _drain_queue_for_agent,
  // which re-publishes any already-queued contacts back to conversations.inbound.
  //
  // execution_model MUST be "stateful" here — the routing engine's kafka_listener
  // defaults missing execution_model to "stateless", which causes set_instance to
  // overwrite the Redis key with execution_model="stateless".  The orchestrator-
  // bridge reads execution_model from Redis to detect human agents (fallback 2
  // path); if it reads "stateless", it skips activate_human_agent entirely and
  // the contact is never passed to the Agent Assist UI.
  await kafka.publish("agent.lifecycle", {
    event:                    "agent_ready",
    tenant_id:                tenantId,
    instance_id:              instanceId,
    agent_type_id:            `human_agent_${poolId}`,
    status:                   "ready",
    execution_model:          "stateful",   // required: prevents stateless default in routing engine
    current_sessions:         0,
    pools:                    [poolId],
    max_concurrent_sessions:  10,
    timestamp:                now,
  })

  console.log(`[agent-ws] Human agent registered: instance=${instanceId} pool=${poolId}`)
}

// ─────────────────────────────────────────────────────────────────────────────
// unregisterHumanAgent — mark instance as logged_out when agent disconnects
// ─────────────────────────────────────────────────────────────────────────────
async function unregisterHumanAgent(
  poolId: string,
  redis:  import("ioredis").default,
  kafka:  { publish: (topic: string, payload: Record<string, unknown>) => Promise<void> },
): Promise<void> {
  const tenantId   = process.env["PLUGHUB_TENANT_ID"] ?? "tenant_demo"
  const instanceId = `human-${poolId}`
  const now        = new Date().toISOString()

  // Update status in Redis immediately
  try {
    const raw = await redis.get(`${tenantId}:instance:${instanceId}`)
    if (raw) {
      const inst = JSON.parse(raw) as Record<string, unknown>
      inst["status"] = "logged_out"
      await redis.set(`${tenantId}:instance:${instanceId}`, JSON.stringify(inst))
    }
    await redis.srem(`${tenantId}:pool:${poolId}:instances`, instanceId)
  } catch { /* non-fatal */ }

  // Notify routing engine
  await kafka.publish("agent.lifecycle", {
    event:        "agent_logout",
    tenant_id:    tenantId,
    instance_id:  instanceId,
    agent_type_id:`human_agent_${poolId}`,
    status:       "logout",
    pools:        [poolId],
    timestamp:    now,
  })

  console.log(`[agent-ws] Human agent unregistered: instance=${instanceId} pool=${poolId}`)
}

export async function startServer(config: ServerConfig): Promise<void> {
  const app = express()
  app.use(express.json())

  // Dependências compartilhadas entre todas as conexões SSE.
  // Criadas uma única vez — não por conexão — para reutilizar pools Redis/Kafka.
  const redis    = createRedisClient()
  const kafka    = createKafkaProducer()
  const registry = createRegistryClient(
    process.env["AGENT_REGISTRY_URL"] ?? "http://localhost:3300"
  )
  const postgres  = createPostgresClient()

  const sharedRuntimeDeps: RuntimeDeps         = { redis, kafka, registry }
  const sharedBpmDeps: BpmDeps                 = { kafka, redis }
  const sharedSupervisorDeps: SupervisorDeps   = { redis, kafka }
  const sharedExternalAgentDeps: ExternalAgentDeps = { redis, kafka }
  const sharedEvalDeps: EvaluationDeps         = {
    kafka,
    redis,
    postgres,
    proxyUrl:         process.env["MCP_PROXY_URL"]      ?? "http://localhost:7422",
    skillRegistryUrl: process.env["SKILL_REGISTRY_URL"] ?? "http://localhost:3400",
  }

  // Map sessionId → transport para suportar conexões simultâneas.
  // O MCP SDK não permite compartilhar uma instância McpServer entre conexões —
  // cada GET /sse cria uma instância própria, mas compartilha os deps acima.
  const transports = new Map<string, SSEServerTransport>()

  // GET /sse — cliente abre conexão SSE
  app.get("/sse", async (req: Request, res: Response) => {
    const transport = new SSEServerTransport("/messages", res)

    // Nova instância McpServer por conexão — exigência do SDK (Protocol.connect
    // lança "Already connected" se a mesma instância for reutilizada).
    const mcpServer = new McpServer({ name: "mcp-server-plughub", version: "1.0.0" })
    registerBpmTools(mcpServer, sharedBpmDeps)
    registerRuntimeTools(mcpServer, sharedRuntimeDeps)
    registerSessionTools(mcpServer, { redis, kafka })
    registerSupervisorTools(mcpServer, sharedSupervisorDeps)
    registerEvaluationTools(mcpServer, sharedEvalDeps)
    registerExternalAgentTools(mcpServer, sharedExternalAgentDeps)
    registerOperationalTools(mcpServer, { redis })
    registerDelegationTools(mcpServer, {
      redis,
      skillFlowUrl: process.env["SKILL_FLOW_URL"]    ?? "http://localhost:3400",
      tenantId:     process.env["PLUGHUB_TENANT_ID"]  ?? process.env["TENANT_ID"] ?? "tenant_demo",
    })

    transports.set(transport.sessionId, transport)

    res.on("close", () => {
      transports.delete(transport.sessionId)
    })

    await mcpServer.connect(transport)
  })

  // POST /messages — cliente envia mensagens MCP
  app.post("/messages", async (req: Request, res: Response) => {
    const sessionId = req.query["sessionId"] as string | undefined
    if (!sessionId) {
      res.status(400).json({ error: "sessionId query parameter required" })
      return
    }
    const transport = transports.get(sessionId)
    if (!transport) {
      res.status(404).json({ error: "Session not found" })
      return
    }
    await transport.handlePostMessage(req, res, req.body)
  })

  // ── Agent Assist REST bridge ─────────────────────────────────────────────
  // These endpoints are consumed by agent-assist-ui via Vite proxy /api → :3100

  // GET /supervisor_state/:sessionId
  app.get("/supervisor_state/:sessionId", async (req: Request, res: Response) => {
    const { sessionId } = req.params
    try {
      // Read live session AI state from Redis if available
      const raw = await redis.get(`session:${sessionId}:ai`)
      const ai  = raw ? JSON.parse(raw) : null
      const currentTurn = ai?.current_turn ?? {}
      const partials    = currentTurn.partial_params ?? {}
      const turns       = ai?.consolidated_turns ?? []

      // Build sentiment trajectory: completed turns + current partial as last point
      const trajectory: number[] = [
        ...turns.map((t: Record<string, unknown>) => Number(t.sentiment_score ?? 0)),
        Number(partials.sentiment_score ?? 0),
      ]
      const currentSentiment = Number(partials.sentiment_score ?? 0)

      // Compute trend by comparing the last window vs the first window of the trajectory
      let trend: "improving" | "stable" | "declining" = "stable"
      if (trajectory.length >= 3) {
        const window      = Math.min(3, Math.floor(trajectory.length / 2))
        const firstAvg    = trajectory.slice(0, window).reduce((a, b) => a + b, 0) / window
        const recentAvg   = trajectory.slice(-window).reduce((a, b) => a + b, 0) / window
        const delta       = recentAvg - firstAvg
        if      (delta >  0.1) trend = "improving"
        else if (delta < -0.1) trend = "declining"
      }

      // Read tenant_id and historical context from session meta
      let tenantId   = ""
      let ctxInsights: unknown[] = []
      try {
        const metaRaw = await redis.get(`session:${sessionId}:meta`)
        if (metaRaw) {
          const meta = JSON.parse(metaRaw) as Record<string, string>
          tenantId = meta["tenant_id"] ?? ""
        }
      } catch { /* non-fatal */ }

      if (tenantId) {
        try {
          const ctxRaw = await redis.get(`${tenantId}:session:${sessionId}:context`)
          if (ctxRaw) {
            const ctx = JSON.parse(ctxRaw) as Record<string, unknown>
            ctxInsights = (ctx["historical_insights"] as unknown[]) ?? []
          }
        } catch { /* non-fatal */ }
      }

      // Read contact_context from pipeline_state (written by agente_contexto_ia_v1)
      // Path: results.acumular_contexto.contexto_final.contact_context
      let contactContext: Record<string, unknown> | null = null
      if (tenantId) {
        try {
          const pipelineRaw = await redis.get(`${tenantId}:pipeline:${sessionId}`)
          if (pipelineRaw) {
            const pipeline = JSON.parse(pipelineRaw) as Record<string, unknown>
            const results  = pipeline["results"] as Record<string, unknown> | undefined
            if (results) {
              // 1. Top-level contact_context (direct merge)
              if (results["contact_context"] && typeof results["contact_context"] === "object") {
                contactContext = results["contact_context"] as Record<string, unknown>
              } else {
                // 2. results.acumular_contexto.contexto_final.contact_context
                const acumularCtx = results["acumular_contexto"] as Record<string, unknown> | undefined
                const contextoFinalNested = acumularCtx?.["contexto_final"] as Record<string, unknown> | undefined
                if (contextoFinalNested?.["contact_context"] && typeof contextoFinalNested["contact_context"] === "object") {
                  contactContext = contextoFinalNested["contact_context"] as Record<string, unknown>
                } else {
                  // 3. Deep search: two levels into all result values
                  outerLoop:
                  for (const val of Object.values(results)) {
                    if (val && typeof val === "object") {
                      const nested = val as Record<string, unknown>
                      if (nested["contact_context"] && typeof nested["contact_context"] === "object") {
                        contactContext = nested["contact_context"] as Record<string, unknown>
                        break
                      }
                      for (const innerVal of Object.values(nested)) {
                        if (innerVal && typeof innerVal === "object") {
                          const inner = innerVal as Record<string, unknown>
                          if (inner["contact_context"] && typeof inner["contact_context"] === "object") {
                            contactContext = inner["contact_context"] as Record<string, unknown>
                            break outerLoop
                          }
                        }
                      }
                    }
                  }
                }
              }
            }
          }
        } catch { /* non-fatal */ }
      }

      res.json({
        session_id:   sessionId,
        turn_count:   turns.length,
        is_stale:     false,
        sentiment: {
          current:    currentSentiment,
          trajectory: trajectory.slice(0, -1),
          trend,
          alert:      currentSentiment < -0.5,
        },
        intent: {
          current:    partials.intent    ?? null,
          confidence: partials.confidence ?? 0,
          history:    turns.map((t: Record<string, unknown>) => t.intent).filter(Boolean),
        },
        flags: (partials.flags ?? []) as string[],
        sla: {
          elapsed_ms:      0,
          target_ms:       480_000,
          percentage:      0,
          breach_imminent: false,
        },
        customer_context: {
          historical_insights:   ctxInsights,
          conversation_insights: turns
            .flatMap((t: Record<string, unknown>) => (t["insights"] as unknown[]) ?? []),
          contact_context: contactContext,
        },
      })
    } catch {
      res.status(500).json({ error: "state_unavailable" })
    }
  })

  // GET /conversation_history/:sessionId
  // Returns the full ordered message list for a session.
  // Written by channel-gateway (inbound via WebchatAdapter, outbound via OutboundConsumer).
  // Key: session:{sessionId}:messages — Redis List (RPUSH, LRANGE).
  // Each entry is a JSON-serialised ChatMessage { id, author, text, timestamp }.
  app.get("/conversation_history/:sessionId", async (req: Request, res: Response) => {
    const { sessionId } = req.params
    try {
      const raw      = await redis.lrange(`session:${sessionId}:messages`, 0, -1)
      const messages = raw.map(s => JSON.parse(s))
      res.json({ session_id: sessionId, messages })
    } catch {
      res.status(500).json({ error: "history_unavailable" })
    }
  })

  // GET /supervisor_capabilities/:sessionId
  app.get("/supervisor_capabilities/:sessionId", async (_req: Request, res: Response) => {
    res.json({
      suggested_agents: [],
      escalations:      [],
    })
  })

  // POST /agent_done/:sessionId — light signal for UI teardown (actual done via MCP tool)
  app.post("/agent_done/:sessionId", async (req: Request, res: Response) => {
    const { sessionId } = req.params
    try {
      const body    = req.body as Record<string, unknown>
      const outcome = (body?.outcome as string) ?? "resolved"

      // Look up contact_id and channel from session metadata so we can notify
      // the customer's WebSocket via conversations.outbound.
      let contactId = sessionId
      let channel   = "chat"
      try {
        const metaRaw = await redis.get(`session:${sessionId}:meta`)
        if (metaRaw) {
          const meta = JSON.parse(metaRaw) as Record<string, string>
          if (meta["contact_id"]) contactId = meta["contact_id"]
          if (meta["channel"])    channel   = meta["channel"]
        }
      } catch { /* use fallback */ }

      // 1. Notify the agent WebSocket so the UI transitions to closed state.
      await redis.publish(`agent:events:${sessionId}`, JSON.stringify({
        type:   "session.closed",
        reason: outcome,
      }))

      // 2. Publish contact_closed to Kafka conversations.events so the orchestrator
      //    bridge restores the agent instance to ready state in the routing engine.
      //    NOTE: must be Kafka (not Redis pub/sub) — the bridge is a Kafka consumer.
      //    instance_id is stored in session meta by the bridge on human agent activation.
      let instanceId = ""
      try {
        const metaRaw2 = await redis.get(`session:${sessionId}:meta`)
        if (metaRaw2) {
          const meta2 = JSON.parse(metaRaw2) as Record<string, string>
          instanceId = meta2["instance_id"] ?? ""
        }
      } catch { /* non-fatal */ }
      await kafka.publish("conversations.events", {
        event_type:  "contact_closed",
        session_id:  sessionId,
        instance_id: instanceId,
        reason:      "agent_closed",
      })

      // 3. Publish session.closed to Kafka conversations.outbound so the
      //    channel-gateway OutboundConsumer notifies the customer WebSocket
      //    and closes the connection immediately.
      await kafka.publish("conversations.outbound", {
        type:       "session.closed",
        contact_id: contactId,
        session_id: sessionId,
        channel,
        reason:     outcome,
      })

      res.json({ ok: true })
    } catch {
      res.status(500).json({ error: "publish_failed" })
    }
  })

  // Healthcheck
  app.get("/health", (_req: Request, res: Response) => {
    res.json({ status: "ok", service: "mcp-server-plughub", version: "1.0.0" })
  })

  // ── HTTP server + WebSocket ──────────────────────────────────────────────
  const httpServer = http.createServer(app)

  // WebSocket server for Agent Assist UI — handles /agent/ws?session_id=...
  // The UI connects via Vite proxy /agent-ws → ws://localhost:3100/agent/ws
  const wss = new WebSocketServer({ noServer: true })

  // Grace-period timers for human agent unregister.
  // React 18 StrictMode causes a rapid unmount/remount cycle in development:
  //   WS open → WS close → WS open (all within ~100ms)
  // Without a grace period, the first close triggers unregisterHumanAgent which:
  //   a) sets status=logged_out (removing from routing)
  //   b) publishes agent_logout to Kafka
  //   c) drains the queue — the re-queued contact is then lost when register #2
  //      publishes agent_ready a second time, but the queue is already empty.
  // Fix: delay the unregister by UNREGISTER_GRACE_MS. If the same pool reconnects
  // within that window, cancel the pending unregister.
  const UNREGISTER_GRACE_MS = 2_500
  const pendingUnregister = new Map<string, ReturnType<typeof setTimeout>>()

  httpServer.on("upgrade", (request, socket, head) => {
    const url = new URL(request.url ?? "", `http://${request.headers.host}`)
    console.log(`[upgrade] method=${request.method} pathname=${url.pathname} host=${request.headers.host} upgrade=${request.headers.upgrade}`)
    if (url.pathname === "/agent/ws") {
      console.log(`[upgrade] Handling WebSocket upgrade for pool=${url.searchParams.get("pool")}`)
      wss.handleUpgrade(request, socket, head, (ws) => {
        wss.emit("connection", ws, request)
      })
    } else {
      console.log(`[upgrade] Unknown path ${url.pathname} — destroying socket`)
      socket.destroy()
    }
  })

  wss.on("connection", (ws: WebSocket, request: http.IncomingMessage) => {
    const url    = new URL(request.url ?? "", `http://${request.headers.host}`)
    const poolId = url.searchParams.get("pool") ?? ""
    console.log(`[agent-ws] New WebSocket connection: pool=${poolId} from=${request.socket.remoteAddress}`)

    // All sessions currently subscribed on this WebSocket connection.
    // There is intentionally NO concept of "active session" here — every assigned
    // session is equally active from the server's perspective. The UI decides which
    // contact to display; the server just forwards events and routes outbound messages
    // to the session_id the client specifies in each message.text payload.
    const subscribedSessions = new Set<string>()

    // Seed from URL param — agent reconnecting with a known session (e.g. browser refresh).
    const initialSessionId = url.searchParams.get("session_id") ?? ""

    // instance_id for this agent connection — resolved when conversation.assigned arrives
    let agentInstanceId = ""
    let agentTenantId   = ""

    // Send connection.accepted immediately
    ws.send(JSON.stringify({ type: "connection.accepted", session_id: initialSessionId, pool_id: poolId }))

    const subscriber = redis.duplicate()

    // Participant role for this connection — resolved when conversation.assigned arrives.
    // "primary" for the first agent on a session; "specialist" if the session already
    // had a human agent when this connection was assigned (session_invite / assist mode).
    let agentRole = "primary"

    // Helper: write participant_joined / participant_left to the session stream
    const writeParticipantEvent = async (type: "participant_joined" | "participant_left", sessionId: string) => {
      if (!sessionId) return
      try {
        await (redis as any).xadd(
          `session:${sessionId}:stream`,
          "*",
          "event_id",   crypto.randomUUID(),
          "type",       type,
          "timestamp",  new Date().toISOString(),
          "author",     JSON.stringify({ participant_id: agentInstanceId || poolId, instance_id: agentInstanceId || poolId, role: agentRole }),
          "visibility", JSON.stringify("all"),
          "payload",    JSON.stringify({ participant_id: agentInstanceId || poolId, instance_id: agentInstanceId || poolId }),
        )
      } catch { /* stream not available — non-fatal */ }
    }

    const forward = (_channel: string, message: string) => {
      if (ws.readyState !== WebSocket.OPEN) return
      ws.send(message)

      try {
        const event = JSON.parse(message) as Record<string, unknown>

        // ── conversation.assigned ──────────────────────────────────────────────
        // A new contact has been routed to this agent. Add the session to the
        // subscribed set and subscribe to its Redis channel. Never remove previous
        // sessions — all assigned sessions remain subscribed simultaneously.
        if (event["type"] === "conversation.assigned" && typeof event["session_id"] === "string") {
          console.log(`[agent-ws] Forwarding conversation.assigned: session=${event["session_id"]} pool=${event["pool_id"]} instance=${event["instance_id"]}`)
          const newSessionId = event["session_id"]
          const isNew = !subscribedSessions.has(newSessionId)

          subscribedSessions.add(newSessionId)

          // Capture agent identity from the first assignment event that carries it.
          if (!agentInstanceId) {
            if (typeof event["instance_id"] === "string" && event["instance_id"]) {
              agentInstanceId = event["instance_id"]
            } else if (typeof event["participant_id"] === "string" && event["participant_id"]) {
              agentInstanceId = event["participant_id"]
            }
          }
          if (!agentTenantId && typeof event["tenant_id"] === "string") {
            agentTenantId = event["tenant_id"]
          }

          // Subscribe to session-specific channel so subsequent messages reach this socket.
          if (isNew) {
            subscriber.subscribe(`agent:events:${newSessionId}`, (err) => {
              if (err) console.error("Redis session subscribe error:", err)
            })

            // Write participant_joined. Detect specialist role: if the session already
            // has other human agents (session_invite / assist mode), this is specialist.
            redis.scard(`session:${newSessionId}:human_agents`).then((existingCount) => {
              agentRole = (existingCount !== null && existingCount > 1) ? "specialist" : "primary"
              writeParticipantEvent("participant_joined", newSessionId).catch(() => {})
            }).catch(() => {
              writeParticipantEvent("participant_joined", newSessionId).catch(() => {})
            })
          }
        }

        // ── session.closed ────────────────────────────────────────────────────
        // Session ended (customer hangup, timeout, agent_done, etc.).
        // Remove from the subscribed set and unsubscribe from the Redis channel.
        // The UI will handle the visual state transition on its own.
        if (event["type"] === "session.closed" && typeof event["session_id"] === "string") {
          const closedId = event["session_id"]
          subscribedSessions.delete(closedId)
          subscriber.unsubscribe(`agent:events:${closedId}`, (err) => {
            if (err) console.error("Redis session unsubscribe error:", err)
          })
        }
      } catch { /* ignore parse errors */ }
    }

    if (initialSessionId) {
      // Direct session connection — agent reconnecting with a known session (e.g. browser refresh).
      subscribedSessions.add(initialSessionId)
      subscriber.subscribe(`agent:events:${initialSessionId}`, (err) => {
        if (err) console.error("Redis subscribe error:", err)
      })
    }
    if (poolId) {
      // Pool-lobby connection — agent is waiting for an assignment.
      // Also subscribed even when session_id is present, so that a new assignment
      // arriving via pool:events:{poolId} is always received regardless of whether
      // the agent reconnected with a stale session_id in the URL.
      subscriber.subscribe(`pool:events:${poolId}`, (err) => {
        if (err) console.error("Redis subscribe error:", err)
      })

      // Deliver any pending assignment that was published while this agent was
      // disconnected (e.g. after a server restart / browser refresh).
      // The bridge stores `pool:pending_assignment:{poolId}` with TTL=300s when
      // activating a human agent; it is deleted on contact_closed.
      redis.get(`pool:pending_assignment:${poolId}`).then((pendingRaw) => {
        if (pendingRaw && ws.readyState === WebSocket.OPEN) {
          console.log(`[agent-ws] Delivering pending assignment to reconnecting agent pool=${poolId}`)
          forward(`pool:events:${poolId}`, pendingRaw)
        }
      }).catch((err) => console.error(`[agent-ws] Error checking pending assignment pool=${poolId}:`, err))

      // ── Human agent login — register instance + notify routing engine ───────
      //
      // When a human agent opens the Agent Assist UI we:
      //   1. Create (or refresh) their instance in Redis so the Routing Engine
      //      can allocate contacts to them — no seed script required.
      //   2. Publish `agent_ready` to the `agent.lifecycle` Kafka topic so the
      //      Routing Engine's LifecycleEventHandler runs _drain_queue_for_agent,
      //      which re-routes any contacts already waiting in this pool.
      //
      // This is the correct production behaviour: the act of opening the Agent
      // Assist UI is sufficient to become available for routing.
      //
      // Cancel any pending unregister for this pool — StrictMode in React 18
      // causes a rapid close→open cycle. Without this, the close fires
      // unregisterHumanAgent which drains the queue before the second open can
      // receive the contact.
      const existingUnregTimer = pendingUnregister.get(poolId)
      if (existingUnregTimer !== undefined) {
        clearTimeout(existingUnregTimer)
        pendingUnregister.delete(poolId)
        console.log(`[agent-ws] Cancelled pending unregister (StrictMode reconnect) pool=${poolId}`)
      }
      registerHumanAgent(poolId, redis, kafka).catch((err) =>
        console.error(`[agent-ws] registerHumanAgent pool=${poolId}:`, err)
      )
    }

    subscriber.on("message", (channel: string, message: string) => {
      console.log(`[agent-ws] pub/sub received channel=${channel} type=${(() => { try { return JSON.parse(message).type } catch { return "?" } })()}`)
      forward(channel, message)
    })

    // ── Inbound messages FROM the human agent → conversations.outbound ───────
    // The agent UI sends { type: "message.text", session_id, text, timestamp }.
    // session_id is mandatory — the UI always knows which contact the agent is
    // replying to (selectedSessionId). Without it we cannot route to the right session.
    ws.on("message", async (raw: Buffer) => {
      try {
        const msg = JSON.parse(raw.toString()) as Record<string, unknown>
        // Pong from the Agent Assist UI heartbeat loop (every 15s) — translate to
        // agent_heartbeat so the routing engine renews the instance TTL (30s).
        // Without this, the instance expires and the agent becomes invisible to routing.
        if (msg["type"] === "pong" && poolId) {
          const tenantId   = process.env["PLUGHUB_TENANT_ID"] ?? "tenant_demo"
          const instanceId = `human-${poolId}`
          kafka.publish("agent.lifecycle", {
            event:                   "agent_heartbeat",
            tenant_id:               tenantId,
            instance_id:             instanceId,
            agent_type_id:           `human_agent_${poolId}`,
            status:                  "ready",
            execution_model:         "stateful",
            current_sessions:        subscribedSessions.size,
            pools:                   [poolId],
            max_concurrent_sessions: 10,
            timestamp:               new Date().toISOString(),
          }).catch(() => {/* non-fatal */})
          return
        }

        if (msg["type"] !== "message.text") return  // ignore other unknown types

        // session_id is required in every outbound message.
        const targetSessionId = typeof msg["session_id"] === "string" ? msg["session_id"] : ""
        if (!targetSessionId) return  // drop messages with no target session

        // Verify the target session is actually subscribed on this connection —
        // prevents rogue clients from injecting messages into arbitrary sessions.
        if (!subscribedSessions.has(targetSessionId)) return

        // Look up contact_id and channel from session metadata.
        // Try two sources in order:
        //   1. session:{session_id}:meta (written by channel-gateway on connect)
        //   2. session:{session_id}:contact_id (dedicated key, also by channel-gateway)
        let contactId: string | null = null
        try {
          const metaRaw = await redis.get(`session:${targetSessionId}:meta`)
          if (metaRaw) {
            const meta = JSON.parse(metaRaw) as Record<string, string>
            if (meta["contact_id"]) contactId = meta["contact_id"]
          }
        } catch { /* try next source */ }
        if (!contactId) {
          try {
            contactId = await redis.get(`session:${targetSessionId}:contact_id`)
          } catch { /* use final fallback */ }
        }
        if (!contactId) contactId = targetSessionId  // last-resort fallback

        const msgText = typeof msg["text"] === "string" ? msg["text"] : ""
        const msgTs   = typeof msg["timestamp"] === "string"
          ? msg["timestamp"]
          : new Date().toISOString()

        // Read channel from session meta — must match the customer's channel
        // so the outbound consumer delivers it correctly.
        let msgChannel = "webchat"
        try {
          const metaForChannel = await redis.get(`session:${targetSessionId}:meta`)
          if (metaForChannel) {
            const metaObj = JSON.parse(metaForChannel) as Record<string, string>
            if (metaObj["channel"]) {
              const rawCh = metaObj["channel"]
              msgChannel = rawCh === "chat" ? "webchat" : rawCh
            }
          }
        } catch { /* use webchat fallback */ }

        // ── @mention detection ─────────────────────────────────────────────────
        // If the human agent's message contains @aliases (e.g. "@copilot ativa"),
        // the message must NOT be delivered to the customer. Instead:
        //   1. Write to session stream as agents_only (visible to all agents)
        //   2. Echo to all agents via Redis pub/sub so their UIs update
        //   3. Route each @alias → conversations.inbound with mode: "assist" so
        //      the Routing Engine invites the matching specialist pool
        // This matches the PlugHub spec: "routing is additive, not substitutive".
        const tenantIdForMentions = agentTenantId || (process.env["PLUGHUB_TENANT_ID"] ?? "tenant_demo")
        const mentionParsed = parseMentions(msgText)

        if (mentionParsed.has_mentions) {
          const messageId = crypto.randomUUID()

          // 1. Write to session stream as agents_only
          try {
            await (redis as any).xadd(
              `session:${targetSessionId}:stream`,
              "*",
              "event_id",   messageId,
              "type",       "message",
              "timestamp",  msgTs,
              "author",     JSON.stringify({
                participant_id: agentInstanceId || poolId,
                instance_id:    agentInstanceId || poolId,
                type:           "agent_human",
              }),
              "visibility", JSON.stringify("agents_only"),
              "payload",    JSON.stringify({ message_id: messageId, text: msgText }),
            )
          } catch { /* non-fatal — stream may not exist yet */ }

          // 2. Echo to all agents via Redis pub/sub (the Agent Assist UI listens here)
          try {
            await redis.publish(`agent:events:${targetSessionId}`, JSON.stringify({
              type:       "message.text",
              message_id: messageId,
              author:     {
                type: "agent_human",
                id:   agentInstanceId || poolId,
              },
              text:       msgText,
              timestamp:  msgTs,
              visibility: "agents_only",
            }))
          } catch { /* non-fatal */ }

          // 3. Route each @alias to the corresponding specialist pool.
          //    Two events per alias:
          //      a) mention_routing:true — for command dispatch to an ALREADY-ACTIVE specialist
          //         (handled by orchestrator-bridge process_mention_routing)
          //      b) Full ConversationInboundEvent with conference_id — for the Routing Engine to
          //         ALLOCATE the specialist when it is not yet running in this session
          //         (routing engine validates required fields; conference_id signals conference mode)
          try {
            // Read session metadata once to get customer_id and channel for the routing event
            let customerIdForInbound = ""
            let channelForInbound    = "webchat"
            try {
              const metaRaw = await redis.get(`session:${targetSessionId}:meta`)
              if (metaRaw) {
                const meta = JSON.parse(metaRaw) as Record<string, string>
                if (meta["customer_id"]) customerIdForInbound = meta["customer_id"]
                if (meta["channel"])     channelForInbound    = meta["channel"]
              }
              if (!customerIdForInbound) {
                const cidRaw = await redis.get(`session:${targetSessionId}:contact_id`)
                if (cidRaw) customerIdForInbound = cidRaw
              }
            } catch { /* use defaults */ }

            const poolConfigRaw = await redis.get(`${tenantIdForMentions}:pool_config:${poolId}`)
            if (poolConfigRaw) {
              const poolConfig = JSON.parse(poolConfigRaw) as Record<string, unknown>
              const mentionablePools =
                poolConfig["mentionable_pools"] &&
                typeof poolConfig["mentionable_pools"] === "object"
                  ? (poolConfig["mentionable_pools"] as Record<string, string>)
                  : {}

              for (const mention of mentionParsed.mentions) {
                const targetPoolId = mentionablePools[mention.alias]
                if (!targetPoolId) {
                  console.log(`[agent-ws] @mention alias "${mention.alias}" not in mentionable_pools of pool "${poolId}" — skipping`)
                  continue
                }
                console.log(`[agent-ws] @mention routing: alias="${mention.alias}" → pool="${targetPoolId}" session="${targetSessionId}"`)

                // (a) mention_routing event — dispatches commands to an already-active specialist
                await kafka.publish("conversations.inbound", {
                  mention_routing:     true,
                  session_id:          targetSessionId,
                  tenant_id:           tenantIdForMentions,
                  pool_id:             targetPoolId,
                  alias:               mention.alias,
                  mention_text:        mention.args_raw || "",
                  from_participant_id: agentInstanceId || poolId,
                  from_pool_id:        poolId,
                  timestamp:           msgTs,
                })

                // (b) Full ConversationInboundEvent — allocates the specialist via Routing Engine
                //     when not yet active. conference_id = session_id signals conference/assist mode.
                //     The orchestrator-bridge process_routed dedup guard prevents double-activation
                //     when the specialist is already running.
                await kafka.publish("conversations.inbound", {
                  session_id:   targetSessionId,
                  tenant_id:    tenantIdForMentions,
                  customer_id:  customerIdForInbound || targetSessionId,
                  channel:      channelForInbound,
                  pool_id:      targetPoolId,
                  conference_id: targetSessionId,  // signals conference/assist mode to routing engine
                  started_at:   new Date().toISOString(),
                  elapsed_ms:   0,
                })
              }
            } else {
              console.log(`[agent-ws] No pool_config found for pool "${poolId}" in tenant "${tenantIdForMentions}" — @mention routing skipped`)
            }
          } catch (err) {
            console.error("[agent-ws] @mention routing error (non-fatal):", err)
          }

          // Skip conversations.outbound — @mention messages are agents_only
          return
        }

        // Normal (non-@mention) message: deliver to customer via outbound consumer
        await kafka.publish("conversations.outbound", {
          type:       "message.text",
          contact_id: contactId,
          session_id: targetSessionId,
          message_id: crypto.randomUUID(),
          channel:    msgChannel,
          direction:  "outbound",
          author:     { type: "agent_human", id: agentInstanceId || poolId || "human_agent", instance_id: agentInstanceId || poolId },
          content:    { type: "text", text: msgText },
          text:       msgText,   // kept for channel-gateway backward compat
          timestamp:  msgTs,
        })
      } catch (err) {
        console.error(`Agent WS message error:`, err)
      }
    })

    // Ping every 30s to keep connection alive
    const pingInterval = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "ping" }))
      }
    }, 30_000)

    ws.on("close", () => {
      clearInterval(pingInterval)
      // Write participant_left for every session still open on this connection.
      for (const sid of subscribedSessions) {
        writeParticipantEvent("participant_left", sid).catch(() => {})
      }
      subscriber.unsubscribe()
      subscriber.quit()
      // Notify routing engine that this human agent is no longer available.
      // Use a grace period so that React 18 StrictMode's rapid close→open cycle
      // does NOT unregister the agent — the new connection will cancel this timer.
      if (poolId) {
        const timer = setTimeout(() => {
          pendingUnregister.delete(poolId)
          unregisterHumanAgent(poolId, redis, kafka).catch((err) =>
            console.error(`[agent-ws] unregisterHumanAgent pool=${poolId}:`, err)
          )
        }, UNREGISTER_GRACE_MS)
        pendingUnregister.set(poolId, timer)
      }
    })

    ws.on("error", (err) => {
      console.error(`Agent WS error pool=${poolId}:`, err)
    })
  })

  await new Promise<void>((resolve) => {
    httpServer.listen(config.port, config.host, () => {
      console.log(`✅ mcp-server-plughub iniciado`)
      console.log(`   Transporte: SSE`)
      console.log(`   Endpoint:   http://${config.host}:${config.port}/sse`)
      console.log(`   Agent WS:   ws://${config.host}:${config.port}/agent/ws`)
      console.log(`   Tools BPM:          conversation_start, conversation_status, conversation_end, rule_dry_run, notification_send, conversation_escalate`)
      console.log(`   Tools Runtime:       agent_login, agent_ready, agent_busy, agent_done, agent_pause, agent_logout, insight_register`)
      console.log(`   Tools Supervisor:    supervisor_state, supervisor_capabilities, agent_join_conference`)
      console.log(`   Tools Evaluation:    transcript_get, evaluation_context_resolve, evaluation_publish`)
      console.log(`   Tools ExternalAgent: invoke, wait_for_assignment, send_message, wait_for_message`)
      console.log(`   Tools Delegation:    agent_delegate, agent_delegate_status`)
      console.log(`   SKILL_FLOW_URL:      ${process.env["SKILL_FLOW_URL"] ?? "http://localhost:3400 (padrão — configure SKILL_FLOW_URL para Docker)"}`)
      resolve()
    })
  })
}
