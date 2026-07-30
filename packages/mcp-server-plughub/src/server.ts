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
import { registerWorkQueueTools }    from "./tools/work_queue"
import { listQueue, claimTask, releaseTask } from "./lib/work-queue"
import { registerDelegationTools }  from "./tools/delegation"
import type { DelegationDeps }      from "./tools/delegation"
import { registerDeployTools }      from "./tools/deploy"
import type { DeployDeps }          from "./tools/deploy"
import { registerOutboundTools }    from "./tools/outbound"
import type { OutboundDeps }        from "./tools/outbound"
import { registerCalendarTools }    from "./tools/calendar"
import type { CalendarDeps }        from "./tools/calendar"
import { registerAgentEventTools }  from "./tools/agent-events"
import type { AgentEventDeps }      from "./tools/agent-events"
import { registerJourneyTools, writeContextTag } from "./tools/journey"
import { registerSurveyTools }      from "./tools/survey"
import type { SurveyDeps }          from "./tools/survey"
import { registerSegmentTools }     from "./tools/segment"
import { registerWorkflowTools }    from "./tools/workflow"
import type { WorkflowDeps }        from "./tools/workflow"
import { registerDialogTools }      from "./tools/dialog"
import type { DialogDeps }          from "./tools/dialog"
import jwt                         from "jsonwebtoken"
import { createRedisClient, keys } from "./infra/redis"
import { createKafkaProducer }     from "./infra/kafka"
import { createRegistryClient }    from "./infra/registry-client"
import { createPostgresClient }    from "./infra/postgres"
import { parseMentions }           from "./lib/mention-parser"
import { routeMentions }           from "./lib/mention-routing"
import { writeStreamEntry }        from "./lib/write-stream-entry"
import { shouldDropAssignment }    from "./lib/assignment-filter"
import { MaskingService }          from "./lib/masking"
import type { ContextMaskingConfig } from "@plughub/schemas"

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
    analyticsApiUrl:  process.env["ANALYTICS_API_URL"]  ?? "http://localhost:3500",
    agentRegistryUrl: process.env["AGENT_REGISTRY_URL"] ?? "http://localhost:3300",
  }

  const bpmDeps: BpmDeps = { kafka, redis }

  const sessionDeps: SessionDeps = { redis, kafka }

  const supervisorDeps: SupervisorDeps = { redis, kafka }

  const externalAgentDeps: ExternalAgentDeps = { redis, kafka }

  const operationalDeps: OperationalDeps = { redis }

  const workQueueDeps = {
    redis,
    routingUrl: process.env["PLUGHUB_ROUTING_URL"] ?? "http://routing-engine:3550",
    adminToken: process.env["ROUTING_ADMIN_TOKEN"] || undefined,
  }

  const delegationDeps: DelegationDeps = {
    redis,
    skillFlowUrl: process.env["SKILL_FLOW_URL"]   ?? "http://localhost:3400",
    tenantId:     process.env["PLUGHUB_TENANT_ID"] ?? process.env["TENANT_ID"] ?? "tenant_demo",
  }

  const deployDeps: DeployDeps = {
    agentRegistryUrl: process.env["AGENT_REGISTRY_URL"] ?? "http://localhost:3300",
    tenantId:         process.env["PLUGHUB_TENANT_ID"]  ?? process.env["TENANT_ID"] ?? "tenant_demo",
  }

  const outboundDeps: OutboundDeps = {
    mailingApiUrl: process.env["MAILING_API_URL"]  ?? "http://localhost:3660",
    tenantId:      process.env["PLUGHUB_TENANT_ID"] ?? process.env["TENANT_ID"] ?? "tenant_demo",
  }

  const calendarDeps: CalendarDeps = {
    calendarApiUrl: process.env["CALENDAR_API_URL"] ?? "http://localhost:3700",
    tenantId:       process.env["PLUGHUB_TENANT_ID"] ?? process.env["TENANT_ID"] ?? "tenant_demo",
  }

  const agentEventDeps: AgentEventDeps = { redis, kafka }

  const surveyDeps: SurveyDeps = {
    kafka,
    dialogApiUrl:      process.env["DIALOG_API_URL"] ?? "http://localhost:3760",
    tenantId:          process.env["PLUGHUB_TENANT_ID"] ?? process.env["TENANT_ID"] ?? "tenant_demo",
    channelGatewayUrl: process.env["CHANNEL_GATEWAY_URL"] ?? process.env["CHANNEL_GATEWAY_HTTP_URL"] ?? "http://localhost:8010",
    evaluationApiUrl:  process.env["EVALUATION_API_URL"] ?? "http://localhost:3400",
    evaluationServiceToken: process.env["EVALUATION_SERVICE_TOKEN"] ?? process.env["PLUGHUB_EVALUATION_SERVICE_TOKEN"] ?? "",
  }

  const workflowDeps: WorkflowDeps = {
    channelGatewayUrl: process.env["CHANNEL_GATEWAY_HTTP_URL"] ?? "http://localhost:8010",
    tenantId:          process.env["PLUGHUB_TENANT_ID"] ?? process.env["TENANT_ID"] ?? "tenant_demo",
  }

  const dialogDeps: DialogDeps = {
    dialogApiUrl: process.env["DIALOG_API_URL"] ?? "http://localhost:3760",
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
  registerWorkQueueTools(server, workQueueDeps)
  registerDelegationTools(server, delegationDeps)
  registerDeployTools(server, deployDeps)
  registerOutboundTools(server, outboundDeps)
  registerCalendarTools(server, calendarDeps)
  registerAgentEventTools(server, agentEventDeps)
  registerJourneyTools(server, agentEventDeps)
  registerSurveyTools(server, surveyDeps)
  registerSegmentTools(server, {
    redis, kafka,
    tenantId: process.env["PLUGHUB_TENANT_ID"] ?? process.env["TENANT_ID"] ?? "tenant_demo",
  })
  registerWorkflowTools(server, workflowDeps)
  registerDialogTools(server, dialogDeps)

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
        // F5: membership é `pools[]` e só. O fallback `pool_id === poolId` saiu
        // junto com o campo — toda instância (humana ou de IA) declara `pools`.
        const pools = Array.isArray(inst["pools"]) ? (inst["pools"] as string[]) : []
        if (pools.includes(poolId)) {
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

      // Filter: only process instances that actually belong to this pool (F5:
      // membership é `pools[]`; o `pool_id` singular não existe mais).
      const pools = Array.isArray(inst["pools"]) ? (inst["pools"] as string[]) : []
      if (!pools.includes(poolId)) continue

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
//
// Instance model — PER USER (not per pool):
//   instanceId = "human-{userId}"  — one Redis key shared across all pools.
//   When the agent logs into an additional pool the existing instance is read,
//   poolId is merged into pools[], and the key is overwritten.  The routing
//   engine's LifecycleEventHandler receives agent_ready with the full merged
//   pools list and makes the instance available in every listed pool.
//
// Step 1 — Redis: upsert the instance directly so it is immediately visible to
//   the routing engine's Redis reads, even before Kafka is processed.
// Step 2 — Kafka `agent.lifecycle` / event=agent_ready: the routing engine's
//   LifecycleEventHandler.handle() picks this up and calls _drain_queue_for_agent,
//   which re-publishes any already-queued contacts back to conversations.inbound.
//
/**
 * Capacity-governance item 2 / Etapa 2 — login humano negado pelos gates:
 *   pool_kind_mismatch       — pool é agent_kind 'ai' (pool misto proibido)
 *   human_capacity_exhausted — logins concorrentes ≥ C_human (contratado)
 * O caller (WS handler) envia `login_denied` ao Console e fecha a conexão.
 */
class HumanLoginDenied extends Error {
  constructor(
    public reason:  string,
    public details: Record<string, unknown> = {},
  ) { super(reason) }
}

async function registerHumanAgent(
  poolId:               string,
  userId:               string,
  userLogin:            string,
  maxConcurrentSessions: number,
  redis:  import("ioredis").default,
  kafka:  { publish: (topic: string, payload: Record<string, unknown>) => Promise<void> },
): Promise<void> {
  const tenantId        = process.env["PLUGHUB_TENANT_ID"] ?? "tenant_demo"
  const registryUrl     = process.env["AGENT_REGISTRY_URL"] ?? "http://localhost:3300"
  // Per-user instance key — falls back to per-pool for old clients without user_id
  const instanceId      = userId ? `human-${userId}` : `human-${poolId}`
  const now             = new Date().toISOString()

  // ── Capacity-governance item 2 / Etapa 2 — gates de login humano ────────────
  // (a) Kind do pool: humano só loga em pool agent_kind='human' (pool misto
  //     proibido). Lê o pool_config cacheado pelo routing; ausente → fail-open.
  try {
    const cfgRaw = await redis.get(`${tenantId}:pool_config:${poolId}`)
    if (cfgRaw) {
      const kind = (JSON.parse(cfgRaw) as Record<string, unknown>)["agent_kind"]
      if (kind === "ai") {
        throw new HumanLoginDenied("pool_kind_mismatch", { pool_id: poolId })
      }
    }
  } catch (err) {
    if (err instanceof HumanLoginDenied) throw err
    // parse/Redis error → fail-open
  }
  // (b) C_human: logins concorrentes (instâncias human-*) ≤ quota contratada.
  //     Re-login do MESMO usuário (instância existente) nunca é bloqueado.
  try {
    const already = await redis.exists(`${tenantId}:instance:${instanceId}`)
    if (!already) {
      const limitRaw = await redis.get(`${tenantId}:quota:capacity:human_agent`)
      const limit    = limitRaw ? parseInt(limitRaw, 10) : NaN
      if (Number.isFinite(limit) && limit > 0) {
        const logged = await redis.keys(`${tenantId}:instance:human-*`)
        if (logged.length >= limit) {
          throw new HumanLoginDenied("human_capacity_exhausted", {
            limit, current: logged.length,
          })
        }
      }
    }
  } catch (err) {
    if (err instanceof HumanLoginDenied) throw err
    // Redis error → fail-open (gate nunca derruba o login por falha de infra)
  }

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
      agent_kind:    "human",   // item 2: pool auto-criado em login humano é humano por definição
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

  // Restore an active pause across reconnects: the durable marker
  // ${tenant}:agent_paused:${instanceId} survives the instance deletion that a
  // WS logout (Console navigation) performs. If present, register the agent as
  // paused so routing keeps excluding it (allocation requires state=="ready").
  let isPaused = false
  try {
    isPaused = (await redis.get(`${tenantId}:agent_paused:${instanceId}`)) !== null
  } catch { /* non-fatal — treat as not paused */ }

  // ── Entrada no pool: ATÔMICA, e simétrica à saída ─────────────────────────
  //
  // O Console abre uma conexão WS POR POOL, então esta função roda N vezes em
  // paralelo no load da página — uma por pool. A versão anterior fazia
  // read-modify-write do campo `pools`: lia `existingPools`, calculava
  // `merged = existente ∪ meuPool` e escrevia o campo INTEIRO. As N chamadas leem
  // o mesmo estado e a última escrita vence, então a membership colapsa no pool
  // de quem escreveu por último. Com registro vazio (login novo) cada uma calcula
  // `[meuPool]` e o agente termina logado em UM pool, achando que está em N.
  //
  // O log da F1 flagrou isso em produção-demo, e a linha não deixa dúvida sobre
  // ser substituição e não encolhimento:
  //
  //   membership SHRANK: dropped=['formfill_demo'] before=['formfill_demo']
  //                      after=['retencao_humano'] (evento autoritativo=agent_ready)
  //
  // A consequência não para aí: com um pool só, o `unregisterHumanAgent` daquele
  // pool calcula `remaining = []`, entra no full logout e DELeta a instância de um
  // agente conectado — que é a origem do `instance_not_found` e do "No agents
  // available for this pool" após o primeiro ciclo de atendimento.
  //
  // Mesma regra da saída, invertida: a operação não é "escreva o conjunto que eu
  // calculei", é **"adicione o MEU pool ao conjunto"**, indivisível. O evento
  // `agent_ready` publicado abaixo carrega o `pools` DEVOLVIDO pelo script — o
  // estado real pós-escrita — e não o que este chamador imaginou antes de escrever.
  const LUA_JOIN_POOL = `
    local raw = redis.call('GET', KEYS[1])
    local inst = nil
    if raw then
      local ok, decoded = pcall(cjson.decode, raw)
      if ok and type(decoded) == 'table' then inst = decoded end
    end
    if not inst then inst = {} end
    local pools = inst['pools']
    if type(pools) ~= 'table' then pools = {} end
    local found = false
    for _, p in ipairs(pools) do if p == ARGV[1] then found = true end end
    if not found then pools[#pools + 1] = ARGV[1] end
    inst['pools']            = pools
    inst['instance_id']      = ARGV[2]
    inst['user_id']          = ARGV[3]
    inst['user_login']       = ARGV[4]
    inst['tenant_id']        = ARGV[5]
    inst['max_concurrent']   = tonumber(ARGV[6])
    inst['status']           = ARGV[7]
    inst['execution_model']  = 'stateful'
    inst['source']           = 'human_login'
    inst['registered_at']    = inst['registered_at'] or ARGV[8]
    if inst['current_sessions'] == nil then inst['current_sessions'] = 0 end
    -- Resíduo por-pool: só semeia se ausente. Para humano o valor é derivado do
    -- pool em escopo (resolve_agent_type, F2) — o campo aqui não é identidade.
    if inst['agent_type_id'] == nil then
      inst['agent_type_id'] = 'human_agent_' .. ARGV[1]
    end
    -- SET SEM 'KEEPTTL', de propósito: o login é o ponto que AFIRMA a permanência
    -- da chave humana, e 'SET' sem opção de expiração remove qualquer TTL. Este
    -- serviço é o dono do ciclo de vida do registro humano; os demais escritores
    -- usam KEEPTTL justamente para não opinar. Usar KEEPTTL aqui (como esta função
    -- fez brevemente) elimina o único caminho de VOLTA para permanente: qualquer
    -- write com expiração em qualquer serviço converteria a chave para efêmera e nem
    -- relogar a curaria — o TTL decairia até o agente sumir do roteamento sem
    -- nenhum DEL. Observado em 2026-07-28: ttl decaindo monotonicamente de 86400,
    -- sem renovação, com o agente conectado.
    redis.call('SET', KEYS[1], cjson.encode(inst))
    return cjson.encode({ pools = pools, current_sessions = inst['current_sessions'] })
  `

  let mergedPools: string[] = [poolId]
  let existingCurrentSessions = 0
  try {
    const raw = await redis.eval(
      LUA_JOIN_POOL, 1, `${tenantId}:instance:${instanceId}`,
      poolId, instanceId, userId, userLogin, tenantId,
      String(maxConcurrentSessions), isPaused ? "paused" : "ready", now,
    ) as string
    const res = JSON.parse(raw) as { pools?: string[]; current_sessions?: number }
    if (Array.isArray(res.pools) && res.pools.length > 0) mergedPools = res.pools
    if (typeof res.current_sessions === "number") existingCurrentSessions = res.current_sessions
  } catch (e) {
    console.error(`[agent-ws] EVAL de entrada no pool falhou — instance=${instanceId}:`, e)
    throw e   // sem registro não há login; falhar alto é melhor que agente fantasma
  }

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
    // C1 — human login identity; must ride the agent_ready event so the routing
    // engine's _upsert_instance preserves it (it rebuilds the Redis instance from
    // this event, dropping anything not present here).
    user_id:                  userId,
    user_login:               userLogin,
    // status reflects a restored pause so the routing engine's _upsert_instance
    // keeps state="paused" (excluded) and _drain_queue_for_agent skips draining.
    status:                   isPaused ? "paused" : "ready",
    execution_model:          "stateful",   // required: prevents stateless default in routing engine
    current_sessions:         existingCurrentSessions,
    pools:                    mergedPools,
    max_concurrent_sessions:  maxConcurrentSessions,
    timestamp:                now,
  })

  console.log(`[agent-ws] Human agent registered: instance=${instanceId} pools=${mergedPools.join(",")} status=${isPaused ? "paused" : "ready"}`)
}

// ─────────────────────────────────────────────────────────────────────────────
// unregisterHumanAgent — remove poolId from the per-user instance
// ─────────────────────────────────────────────────────────────────────────────
//
// Two outcomes:
//   • Partial logout (agent still logged into other pools):
//       Remove poolId from pools[]; update Redis; publish agent_ready with
//       the remaining pools so the routing engine re-evaluates availability.
//   • Full logout (last pool):
//       DEL the instance key entirely; publish agent_logout.
//
async function unregisterHumanAgent(
  poolId: string,
  userId: string,
  redis:  import("ioredis").default,
  kafka:  { publish: (topic: string, payload: Record<string, unknown>) => Promise<void> },
): Promise<void> {
  const tenantId   = process.env["PLUGHUB_TENANT_ID"] ?? "tenant_demo"
  const instanceId = userId ? `human-${userId}` : `human-${poolId}`
  const now        = new Date().toISOString()

  console.log(`[unregister] START pool=${poolId} user=${userId} instanceId=${instanceId} tenant=${tenantId}`)

  // ── Membership REAL do recurso ────────────────────────────────────────────
  //
  // Esta função é chamada por CONEXÃO WS que fecha, e o Console abre uma conexão
  // POR POOL. O evento é, portanto, de escopo (recurso, pool): "esta conexão saiu
  // DESTE pool". A decisão de DELetar o registro é de escopo RECURSO. Derivar a
  // segunda do primeiro sem saber a membership completa é o mesmo defeito que o
  // ADR `adr-human-agent-pool-scoped-identity` descreve — aqui espelhado no lado
  // da escrita.
  //
  // O default antigo era `allPools = [poolId]`: se a leitura do registro falhasse
  // (ou a chave estivesse momentaneamente ausente), `remainingPools` ficava vazio
  // e o código concluía "último pool" — DELetando a instância de um agente ainda
  // conectado em outros N−1 pools, e publicando um `agent_logout` que só nomeava
  // um pool, deixando os demais pool sets com membro órfão apontando para chave
  // inexistente. Estado observado em 2026-07-28: `{t}:instance:human-…` ausente e
  // `{t}:pool:formfill_demo:instances` ainda listando o id.
  //
  // Regra: **não se apaga por ignorância**. Sem membership legível, o full logout
  // não acontece — degrada para saída-de-pool e loga alto.
  //
  // ── E a operação tem que ser ATÔMICA ─────────────────────────────────────
  //
  // Ler `pools`, calcular "tudo menos o meu pool" e escrever o campo inteiro é
  // read-modify-write. Um reload do Console fecha as N conexões praticamente ao
  // mesmo tempo, as N chamadas caem juntas aqui, e TODAS leem o mesmo snapshot.
  // Log real de 2026-07-28, três pools, um reload:
  //
  //   allPools=retencao,formfill  remainingPools=retencao            (fechou formfill)
  //   allPools=retencao,formfill  remainingPools=retencao,formfill   (fechou aprovacao)
  //   allPools=retencao,formfill  remainingPools=formfill            (fechou retencao)
  //
  // Cada uma escreveu o campo inteiro; venceu a última. O agente ficou declarando
  // um pool e presente em nenhum (cada chamada SREMou o seu). Pior: a perda é
  // CUMULATIVA — a cada reload sobra menos, até restar um só; aí a chamada daquele
  // pool calcula `remaining = []`, entra no full logout e DELeta a instância de um
  // agente que está conectado. Foi essa a origem do `(nil)` + `instance_not_found`.
  //
  // O erro é o mesmo do ADR mais uma vez: um evento de escopo (recurso, pool)
  // computando um valor de escopo RECURSO a partir de uma foto velha. A operação
  // correta não é "escreva o conjunto que eu calculei" e sim **"remova o MEU pool
  // do conjunto"** — set-difference, executada indivisivelmente. Daí o Lua: EVAL é
  // atômico no Redis, então a decisão "sobrou alguém?" passa a ser tomada sobre o
  // estado real no instante da escrita, não sobre o que este chamador viu antes.
  const LUA_LEAVE_POOL = `
    local raw = redis.call('GET', KEYS[1])
    if not raw then return cjson.encode({status='absent'}) end
    local ok, inst = pcall(cjson.decode, raw)
    if not ok or type(inst) ~= 'table' then return cjson.encode({status='corrupt'}) end
    local pools = inst['pools']
    if type(pools) ~= 'table' or #pools == 0 then
      return cjson.encode({status='no_membership'})
    end
    local all, remaining = {}, {}
    for _, p in ipairs(pools) do
      all[#all+1] = p
      if p ~= ARGV[1] then remaining[#remaining+1] = p end
    end
    local sessions = inst['current_sessions'] or 0
    if #remaining == 0 then
      redis.call('DEL', KEYS[1])
      return cjson.encode({status='full_logout', all=all, sessions=sessions})
    end
    inst['pools'] = remaining
    redis.call('SET', KEYS[1], cjson.encode(inst), 'KEEPTTL')
    return cjson.encode({status='partial', all=all, remaining=remaining, sessions=sessions})
  `

  type LeaveResult = {
    status:    "absent" | "corrupt" | "no_membership" | "full_logout" | "partial"
    all?:      string[]
    remaining?: string[]
    sessions?: number
  }

  let result: LeaveResult = { status: "corrupt" }
  try {
    const raw = await redis.eval(
      LUA_LEAVE_POOL, 1, `${tenantId}:instance:${instanceId}`, poolId,
    ) as string
    result = JSON.parse(raw) as LeaveResult
  } catch (e) {
    console.error(`[unregister] EVAL falhou — nenhuma alteração no registro:`, e)
    return
  }

  const allPools       = result.all ?? []
  const remainingPools = result.remaining ?? []
  const currentSessions = result.sessions ?? 0
  console.log(
    `[unregister] status=${result.status} allPools=${allPools.join(",") || "(none)"} ` +
    `remainingPools=${remainingPools.join(",") || "(none)"}`
  )

  // Sempre sai dos sets DESTE pool — é o fato que o evento realmente prova, e é
  // idempotente, então vale para qualquer status.
  try {
    const r1 = await redis.srem(`${tenantId}:pool:${poolId}:instances`,      instanceId)
    const r2 = await redis.srem(`${tenantId}:pool:${poolId}:busy_instances`, instanceId)
    const r3 = await redis.srem(`${tenantId}:pool_roster:${poolId}`,         instanceId)
    console.log(`[unregister] SREM instances=${r1} busy_instances=${r2} pool_roster=${r3}`)
  } catch (e) {
    console.error(`[unregister] SREM do pool que fechou falhou:`, e)
  }

  if (result.status !== "full_logout" && result.status !== "partial") {
    // absent / corrupt / no_membership: nenhuma dessas hipóteses autoriza apagar o
    // recurso — se a instância já não existe, não há o que deletar; se existe e não
    // deu para ler, deletar é pior. A saída deste pool (SREM acima) já foi aplicada.
    console.warn(
      `[unregister] membership NÃO utilizável (${result.status}) para instance=${instanceId} ` +
      `(pool=${poolId}) — NÃO trato como full logout e NÃO deleto o registro.`
    )
    return
  }

  if (result.status === "full_logout") {
    // Full logout — a chave JÁ foi deletada dentro do EVAL (é o que torna a
    // decisão "era o último pool?" confiável: ninguém pode ter entrado ou saído
    // entre a checagem e o DEL). O `_deactivate_instance` do routing-engine já
    // trata chave ausente e usa o `pools` do payload (kafka_listener:718-721).
    console.log(`[unregister] Full logout — publishing agent_logout for instance=${instanceId} pools=${allPools.join(",")}`)
    await kafka.publish("agent.lifecycle", {
      event:        "agent_logout",
      tenant_id:    tenantId,
      instance_id:  instanceId,
      agent_type_id:`human_agent_${poolId}`,
      status:       "logout",
      // Send the full pool list so the routing-engine can clean up every pool set
      // and refresh their snapshots, even if the instance key has already been deleted.
      pools:        allPools,
      timestamp:    now,
    })
    // Sai dos sets de TODOS os pools. O SREM lá em cima só cobre o pool desta
    // conexão; se o DEL (feito no EVAL) é global, a limpeza também tem que ser. Sem
    // isso, um pool não-fechado guarda um membro apontando para chave inexistente
    // — e `get_ready_instances` PULA membro sem chave sem evictar (decisão
    // deliberada, `registry.py:375-392`), então o órfão nunca é colhido: o pool
    // parece ter agente, o claim responde `instance_not_found`, e o Console segue
    // exibindo "Connected". Depender só do `agent_logout` para isso é depender de
    // efeito colateral em outro serviço — o mesmo padrão que a F1 já corrigiu no
    // `remove_from_pool_sets`.
    for (const p of allPools) {
      if (p === poolId) continue   // já removido acima
      try {
        await redis.srem(`${tenantId}:pool:${p}:instances`,      instanceId)
        await redis.srem(`${tenantId}:pool:${p}:busy_instances`, instanceId)
        await redis.srem(`${tenantId}:pool_roster:${p}`,         instanceId)
        console.log(`[unregister] SREM (full logout) pool=${p} instance=${instanceId}`)
      } catch (e) {
        console.error(`[unregister] SREM falhou para pool=${p}:`, e)
      }
    }
    console.log(`[agent-ws] Human agent fully unregistered: instance=${instanceId} pools=${allPools.join(",")}`)
  } else {
    // Partial logout — o registro já foi atualizado (com KEEPTTL) dentro do EVAL.
    // A escrita em JS que existia aqui era a segunda metade do read-modify-write
    // que produzia o lost update; sumiu junto com a primeira.
    // F1 (ADR adr-human-agent-pool-scoped-identity): `agent_type_id` aqui nomeava
    // o pool que está sendo DEIXADO — carimbava a identidade de um pool morto numa
    // instância ainda ativa nos outros. Passa a nomear um pool REMANESCENTE.
    // (A routing preserva o valor do registro vivo; isto conserta o que sobra nos
    // consumidores que leem o payload, como os intervalos de presença do analytics.)
    await kafka.publish("agent.lifecycle", {
      event:            "agent_ready",
      tenant_id:        tenantId,
      instance_id:      instanceId,
      agent_type_id:    `human_agent_${remainingPools[0]}`,
      status:           "ready",
      execution_model:  "stateful",
      current_sessions: currentSessions,
      pools:            remainingPools,
      timestamp:        now,
    })
    console.log(
      `[agent-ws] Human agent unregistered from pool=${poolId}, ` +
      `remaining pools=${remainingPools.join(",")} instance=${instanceId}`
    )
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// ContextStore masking helpers
// Spec: docs/guias/context-masking-rules.md
// ─────────────────────────────────────────────────────────────────────────────

const _JWT_SECRET = process.env["PLUGHUB_JWT_SECRET"] ?? ""

/**
 * Verify a Bearer JWT and return its payload.
 * Throws if the token is missing, malformed, or the signature is invalid.
 * Falls back to decode-only when PLUGHUB_JWT_SECRET is not configured (dev only).
 */
function verifyJwtPayload(authHeader: string | undefined): Record<string, unknown> {
  if (!authHeader?.startsWith("Bearer ")) throw new Error("Missing Bearer token")
  const token = authHeader.slice(7)
  if (_JWT_SECRET) {
    // Production path — full signature verification
    return jwt.verify(token, _JWT_SECRET, { algorithms: ["HS256"] }) as Record<string, unknown>
  }
  // Dev fallback — decode without verification (logs a warning at startup)
  const payloadB64 = token.split(".")[1] ?? ""
  return JSON.parse(Buffer.from(payloadB64, "base64url").toString("utf8")) as Record<string, unknown>
}

/**
 * Extract role from a verified JWT.
 * Throws 401 when the token is missing or invalid.
 */
function extractJwtRole(authHeader: string | undefined): string {
  try {
    const payload = verifyJwtPayload(authHeader)
    const role = (payload["role"] ?? (payload["roles"] as string[])?.[0]) as string | undefined
    return role ?? "operator"
  } catch {
    return "operator"   // fallback for non-UI callers (agent MCP sessions authenticated separately)
  }
}

/**
 * Guard for UI endpoints that require a valid signed JWT with a minimum role.
 * Responds 401 if the token is missing/invalid, 403 if the role is insufficient.
 */
function requireJwtRole(
  authHeader: string | undefined,
  allowedRoles: string[],
  res: Response,
): Record<string, unknown> | null {
  try {
    const payload = verifyJwtPayload(authHeader)
    const role = (payload["role"] ?? (payload["roles"] as string[])?.[0]) as string | undefined
    if (!role || !allowedRoles.includes(role)) {
      res.status(403).json({ error: "Insufficient role", required: allowedRoles })
      return null
    }
    return payload
  } catch {
    res.status(401).json({ error: "Unauthorized" })
    return null
  }
}

/** Namespaces visible to operator by default (conservative). Overridden per-pool. */
const DEFAULT_OPERATOR_NAMESPACES = ["service", "session"]
// Platform default for context_visibility.operator_allow_tags — exact tags an
// operator sees regardless of namespace. caller.customer_id is an internal id (not
// PII) the operator needs to identify the customer (C1/H4). Overridable per pool.
const DEFAULT_OPERATOR_ALLOW_TAGS = ["caller.customer_id"]

// ── ContextMaskingConfig in-process cache ─────────────────────────────────────
// TTL 60s — short enough to pick up Config API changes, long enough to be safe
// under polling loads. Keyed by tenantId.
const CONTEXT_MASKING_CACHE_TTL_MS = 60_000
interface CachedMaskingConfig { config: ContextMaskingConfig; expiresAt: number }
const contextMaskingConfigCache = new Map<string, CachedMaskingConfig>()

/** Invalidate the cached config for a tenant (called on config.changed events). */
function invalidateContextMaskingCache(tenantId: string): void {
  contextMaskingConfigCache.delete(tenantId)
}

// config-http-propagation arc: masking config comes from the Config API HTTP
// endpoint (not direct Redis reads). The 60s TTL cache above bounds fetch load.
const CONFIG_API_URL = process.env["CONFIG_API_URL"] ?? "http://localhost:3600"

/**
 * Resolve the ContextMaskingConfig for a tenant, with in-process TTL cache.
 * Delegates to MaskingService.loadContextMaskingConfig() (Config API HTTP) on miss.
 */
async function getContextMaskingConfig(
  tenantId: string,
): Promise<ContextMaskingConfig> {
  const cached = contextMaskingConfigCache.get(tenantId)
  if (cached && cached.expiresAt > Date.now()) return cached.config
  const config = await MaskingService.loadContextMaskingConfig(CONFIG_API_URL, tenantId)
  contextMaskingConfigCache.set(tenantId, { config, expiresAt: Date.now() + CONTEXT_MASKING_CACHE_TTL_MS })
  return config
}

// ── Pattern matching (specificity algorithm) ──────────────────────────────────

/**
 * Compute specificity score for a rule matching a given tag × role pair.
 *
 * Returns null if the rule does not match. Higher score = more specific.
 *
 * Pattern specificity:
 *   exact match    → 20
 *   prefix glob    → 10  ("caller.*" matches "caller.cpf")
 *   wildcard *     →  0  (matches anything)
 *
 * Role specificity added on top:
 *   matches caller's role category exactly → +2
 *   matches "*"                            → +0
 */
function ruleSpecificity(
  pattern:      string,
  ruleRole:     "operator" | "supervisor" | "*",
  tag:          string,
  callerCategory: "operator" | "supervisor",
): number | null {
  // Pattern match
  let patternScore: number
  if (pattern === tag) {
    patternScore = 20
  } else if (pattern.endsWith(".*")) {
    const prefix = pattern.slice(0, -2) // "caller.*" → "caller"
    const tagNs  = tag.split(".").slice(0, prefix.split(".").length).join(".")
    if (tagNs !== prefix) return null
    patternScore = 10
  } else if (pattern === "*") {
    patternScore = 0
  } else {
    // Non-glob pattern that isn't an exact match — no match
    return null
  }

  // Role match
  let roleScore: number
  if (ruleRole === "*") {
    roleScore = 0
  } else if (ruleRole === callerCategory) {
    roleScore = 2
  } else {
    // Rule targets a different role category — skip
    return null
  }

  return patternScore + roleScore
}

/**
 * Find the most specific ContextMaskingRule for a tag × caller role pair.
 * Returns the matching rule, or null when no rule matches.
 */
function resolveContextMaskingRule(
  tag:    string,
  callerRole: string,
  config: ContextMaskingConfig,
): import("@plughub/schemas").ContextMaskingRule | null {
  const callerCategory: "operator" | "supervisor" =
    config.supervisor_roles.includes(callerRole) ? "supervisor" : "operator"

  let bestRule: import("@plughub/schemas").ContextMaskingRule | null = null
  let bestScore = -1

  for (const rule of config.rules) {
    const score = ruleSpecificity(rule.pattern, rule.role, tag, callerCategory)
    if (score === null) continue
    if (score > bestScore) {
      bestScore = score
      bestRule  = rule
    }
  }

  return bestRule
}

// ── Visual type application ───────────────────────────────────────────────────

/**
 * Apply a ContextMaskingType to a raw value string.
 *
 * Each type is a pure visual presentation — no data-type semantics.
 */
function applyMaskingTypeToValue(raw: string, type: import("@plughub/schemas").ContextMaskingType): string {
  const digits = raw.replace(/\D/g, "")
  switch (type) {
    case "plain":
      return raw
    case "hidden":
      return ""   // signal: caller will omit field
    case "full":
      return "***"
    case "last_2":
      return digits.length >= 2
        ? `***${digits.slice(-2)}`
        : "***"
    case "last_4":
      return digits.length >= 4
        ? `***${digits.slice(-4)}`
        : digits.length > 0 ? `***${digits}` : "***"
    case "first_1":
      return raw.length > 0 ? `${raw[0]}***` : "***"
    case "first_word": {
      const word = raw.split(/\s+/)[0] ?? ""
      return word.length > 0 ? `${word} ***` : "***"
    }
    case "email_domain": {
      const atIdx = raw.indexOf("@")
      if (atIdx > 0) {
        const local  = raw.slice(0, atIdx)
        const domain = raw.slice(atIdx) // includes "@"
        return `${local[0] ?? "*"}***${domain}`
      }
      return raw.length > 0 ? `${raw[0]}***` : "***"
    }
    case "financial":
      return "R$ ****,**"
    default:
      return "***"
  }
}

// ── Main masking function (dynamic, async) ────────────────────────────────────

/**
 * Filter and mask a raw ContextStore hgetall snapshot.
 *
 * Replaces the former synchronous applyContextMasking() that relied on the
 * hardcoded TAG_PII_CATEGORY map. Rules are now loaded from Config API Redis
 * (with in-process TTL cache), falling back to DEFAULT_CONTEXT_MASKING_CONFIG.
 *
 * Spec: docs/guias/context-masking-rules.md
 */
async function applyContextMaskingDynamic(
  rawHash:      Record<string, string>,
  role:         string,
  allowedNs:    string[],
  allowTags:    string[],
  tenantId:     string,
): Promise<Record<string, unknown>> {
  const config       = await getContextMaskingConfig(tenantId)
  // "Who is a supervisor" is config-driven (masking config), not fixed in code.
  const isSupervisor = config.supervisor_roles.includes(role)
  const result: Record<string, unknown> = {}

  for (const [tag, raw] of Object.entries(rawHash)) {
    let entry: Record<string, unknown>
    try { entry = JSON.parse(raw) as Record<string, unknown> }
    catch { entry = { value: raw } }

    const ns = tag.split(".")[0] ?? ""

    // agent.* — always removed (per-participant visibility, resolved elsewhere)
    if (ns === "agent") continue

    // operator_allow_tags (config-driven, per pool: context_visibility.operator_allow_tags)
    // — exact tags the operator sees PLAIN, bypassing the namespace gate AND masking
    // rules. For internal reference ids the operator needs that are NOT PII — e.g.
    // "caller.customer_id" to identify the customer / load history / 360 (C1/H4).
    // Self-contained (no need to also add a masking rule); PII fields (caller.cpf/…)
    // are NOT listed here and stay gated + masked.
    if (!isSupervisor && allowTags.includes(tag)) {
      result[tag] = entry
      continue
    }

    // Namespace gate — operator sees only allowedNs namespaces
    if (!isSupervisor && !allowedNs.includes(ns)) continue

    // Resolve masking rule for this tag × role
    const matchedRule = resolveContextMaskingRule(tag, role, config)

    // Determine effective masking type
    let maskType: import("@plughub/schemas").ContextMaskingType
    if (matchedRule) {
      maskType = matchedRule.type
    } else if (!isSupervisor) {
      maskType = config.default_unmatched_operator
    } else {
      maskType = "plain"
    }

    // Apply type
    if (maskType === "hidden") {
      // Field omitted entirely — do not add to result
      continue
    }

    if (maskType === "plain") {
      result[tag] = entry
    } else {
      // Mask the value field; annotate entry with pii metadata for the UI
      const maskedValue = applyMaskingTypeToValue(String(entry["value"] ?? ""), maskType)
      result[tag] = {
        ...entry,
        value:    maskedValue,
        pii:      true,
        masked:   true,
        category: maskType,
      }
    }
  }

  return result
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
    analyticsApiUrl:  process.env["ANALYTICS_API_URL"]  ?? "http://localhost:3500",
    agentRegistryUrl: process.env["AGENT_REGISTRY_URL"] ?? "http://localhost:3300",
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
    registerWorkQueueTools(mcpServer, {
      redis,
      routingUrl: process.env["PLUGHUB_ROUTING_URL"] ?? "http://routing-engine:3550",
      adminToken: process.env["ROUTING_ADMIN_TOKEN"] || undefined,
    })
    registerDelegationTools(mcpServer, {
      redis,
      skillFlowUrl: process.env["SKILL_FLOW_URL"]    ?? "http://localhost:3400",
      tenantId:     process.env["PLUGHUB_TENANT_ID"]  ?? process.env["TENANT_ID"] ?? "tenant_demo",
    })
    registerDeployTools(mcpServer, {
      agentRegistryUrl: process.env["AGENT_REGISTRY_URL"] ?? "http://localhost:3300",
      tenantId:         process.env["PLUGHUB_TENANT_ID"]  ?? process.env["TENANT_ID"] ?? "tenant_demo",
    })
    registerOutboundTools(mcpServer, {
      mailingApiUrl: process.env["MAILING_API_URL"]  ?? "http://mailing-api:3660",
      tenantId:      process.env["PLUGHUB_TENANT_ID"] ?? process.env["TENANT_ID"] ?? "tenant_demo",
    })
    registerCalendarTools(mcpServer, {
      calendarApiUrl: process.env["CALENDAR_API_URL"] ?? "http://localhost:3700",
      tenantId:       process.env["PLUGHUB_TENANT_ID"] ?? process.env["TENANT_ID"] ?? "tenant_demo",
    })
    registerAgentEventTools(mcpServer, { redis, kafka })
    registerJourneyTools(mcpServer, { redis, kafka })
    registerSurveyTools(mcpServer, {
      kafka,
      dialogApiUrl:      process.env["DIALOG_API_URL"] ?? "http://localhost:3760",
      tenantId:          process.env["PLUGHUB_TENANT_ID"] ?? process.env["TENANT_ID"] ?? "tenant_demo",
      channelGatewayUrl: process.env["CHANNEL_GATEWAY_URL"] ?? "http://channel-gateway:8010",
      evaluationApiUrl:  process.env["EVALUATION_API_URL"] ?? "http://evaluation-api:3400",
      evaluationServiceToken: process.env["EVALUATION_SERVICE_TOKEN"] ?? process.env["PLUGHUB_EVALUATION_SERVICE_TOKEN"] ?? "",
    })
    // Camada E2 (wrap-up-α) — segment_outcome_record. DEVE ser registrada aqui
    // também: startServer cria seu PRÓPRIO mcpServer (não reusa createServer), e é
    // este o server que o index.ts sobe e que o skill-flow-service consulta.
    registerSegmentTools(mcpServer, {
      redis, kafka,
      tenantId: process.env["PLUGHUB_TENANT_ID"] ?? process.env["TENANT_ID"] ?? "tenant_demo",
    })
    registerWorkflowTools(mcpServer, {
      channelGatewayUrl: process.env["CHANNEL_GATEWAY_URL"] ?? "http://channel-gateway:8010",
      tenantId:          process.env["PLUGHUB_TENANT_ID"] ?? process.env["TENANT_ID"] ?? "tenant_demo",
    })
    registerDialogTools(mcpServer, {
      dialogApiUrl: process.env["DIALOG_API_URL"] ?? "http://localhost:3760",
      tenantId:     process.env["PLUGHUB_TENANT_ID"] ?? process.env["TENANT_ID"] ?? "tenant_demo",
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
  // A5.6 — trilha de decisão de aprovação (lê a STREAM canônica; o ClickHouse não é
  // alimentado pela stream). Gated por ABAC approvals.operacao (admin/supervisor
  // bypassam). Live = Redis XRANGE; fallback PG (sessão fechada) = follow-up.
  app.get("/api/approval_audit/:sessionId", async (req: Request, res: Response) => {
    let payload: Record<string, unknown>
    try {
      payload = verifyJwtPayload(req.headers.authorization)
    } catch {
      res.status(401).json({ error: "Unauthorized" })
      return
    }
    const role = (payload["role"] ?? (payload["roles"] as string[] | undefined)?.[0]) as string | undefined
    const mc   = (payload["module_config"] ?? {}) as Record<string, Record<string, { access?: string }>>
    const access = mc["approvals"]?.["operacao"]?.access ?? "none"
    const ORDER: Record<string, number> = { none: 0, read_only: 1, write_only: 1, read_write: 2 }
    const canView = role === "admin" || role === "supervisor" || (ORDER[access] ?? 0) >= 1
    if (!canView) {
      res.status(403).json({ error: "Missing approvals.operacao" })
      return
    }

    const sessionId = req.params.sessionId
    try {
      const entries = (await redis.xrange(`session:${sessionId}:stream`, "-", "+")) as [string, string[]][]
      const decisions: unknown[] = []
      for (const [entryId, fields] of entries) {
        const rec: Record<string, string> = {}
        for (let i = 0; i + 1 < fields.length; i += 2) {
          const k = fields[i]
          if (k !== undefined) rec[k] = fields[i + 1] ?? ""
        }
        if (rec["type"] !== "message") continue
        let p: { approval?: unknown }
        try { p = JSON.parse(rec["payload"] ?? "{}") } catch { continue }
        if (!p?.approval) continue
        decisions.push({
          entry_id:    entryId,
          author_id:   rec["author_id"] ?? null,
          author_role: rec["author_role"] ?? null,
          timestamp:   rec["timestamp"] ?? null,
          approval:    p.approval,
        })
      }
      res.json({ session_id: sessionId, count: decisions.length, decisions })
    } catch (err) {
      res.status(500).json({ error: String(err) })
    }
  })

  app.get("/api/supervisor_state/:sessionId", async (req: Request, res: Response) => {
    const payload = requireJwtRole(req.headers.authorization, ["operator", "supervisor", "admin", "developer"], res)
    if (!payload) return

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

      // Viewer role — taken from verified JWT payload for masking decisions
      const viewerRole = (payload["role"] as string) ?? "operator"

      // Read tenant_id, pool_id and historical context from session meta
      let tenantId = ""
      let poolId   = ""
      let ctxInsights: unknown[] = []
      try {
        const metaRaw = await redis.get(`session:${sessionId}:meta`)
        if (metaRaw) {
          const meta = JSON.parse(metaRaw) as Record<string, string>
          tenantId = meta["tenant_id"] ?? ""
          poolId   = meta["pool_id"]   ?? ""
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

      // Read pool context_visibility — determines which namespaces + exact tags the
      // operator can see (config-driven per pool; defaults when unset).
      let operatorNamespaces = DEFAULT_OPERATOR_NAMESPACES
      let operatorAllowTags  = DEFAULT_OPERATOR_ALLOW_TAGS
      if (tenantId && poolId) {
        try {
          const cfgRaw = await redis.get(`${tenantId}:pool_config:${poolId}`)
          if (cfgRaw) {
            const cfg = JSON.parse(cfgRaw) as Record<string, unknown>
            const cv  = cfg["context_visibility"] as Record<string, unknown> | undefined
            if (Array.isArray(cv?.["operator_namespaces"])) {
              operatorNamespaces = cv["operator_namespaces"] as string[]
            }
            if (Array.isArray(cv?.["operator_allow_tags"])) {
              operatorAllowTags = cv["operator_allow_tags"] as string[]
            }
          }
        } catch { /* non-fatal — use default */ }
      }

      // Read ContextStore snapshot (v2) — primary source for ContextoTab
      // Applies namespace filtering and PII masking based on viewer role.
      // Rules loaded dynamically from Config API (with in-process TTL cache).
      let contextSnapshot: Record<string, unknown> | null = null
      if (tenantId) {
        try {
          const hash = await redis.hgetall(`${tenantId}:ctx:${sessionId}`)
          if (hash && Object.keys(hash).length > 0) {
            contextSnapshot = await applyContextMaskingDynamic(hash, viewerRole, operatorNamespaces, operatorAllowTags, tenantId)
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

      // Arc 11 Fase D — AI participants + pipeline transitions for OrchestrationTab
      let aiParticipants:      unknown[] = []
      let pipelineTransitions: unknown[] = []
      try {
        const instanceIds = await redis.smembers(`session:${sessionId}:ai_agents`)
        if (instanceIds.length > 0 && tenantId) {
          let pipelineState: Record<string, unknown> | null = null
          try {
            const pipeRaw2 = await redis.get(`${tenantId}:pipeline:${sessionId}`)
            if (pipeRaw2) {
              pipelineState = JSON.parse(pipeRaw2) as Record<string, unknown>
              pipelineTransitions = (pipelineState["transitions"] as unknown[]) ?? []
            }
          } catch { /* non-fatal */ }

          let menuWaiting:    Record<string, string> = {}
          let receiveWaiting: Record<string, string> = {}
          try {
            menuWaiting    = await redis.hgetall(`menu:waiting:${sessionId}`)    ?? {}
            receiveWaiting = await redis.hgetall(`receive:waiting:${sessionId}`) ?? {}
          } catch { /* non-fatal */ }

          for (const instanceId of instanceIds) {
            let role = "primary", agentTypeId = "", poolId = "", segmentId = ""
            let joinedAt = new Date().toISOString()
            try {
              const partRaw = await redis.get(`session:${sessionId}:ai_participant:${instanceId}`)
              if (partRaw) {
                const part = JSON.parse(partRaw) as Record<string, string>
                role        = part["role"]          ?? "primary"
                agentTypeId = part["agent_type_id"] ?? ""
                poolId      = part["pool_id"]       ?? ""
                segmentId   = part["segment_id"]    ?? ""
                joinedAt    = part["joined_at"]     ?? joinedAt
              }
            } catch { /* non-fatal */ }

            let currentStep: string | null = null, stepType = "unknown"
            let stepStatus: "running" | "waiting" | "done" | "error" = "running"
            let waitingFor: string | null = null, sinceMs = 0

            if (pipelineState) {
              currentStep = (pipelineState["current_step_id"] as string) ?? null
              const pipeStatus = pipelineState["status"] as string
              const updatedAt  = (pipelineState["updated_at"] as string) ?? null
              if (updatedAt) sinceMs = Date.now() - new Date(updatedAt).getTime()
              if      (pipeStatus === "completed") stepStatus = "done"
              else if (pipeStatus === "failed")    stepStatus = "error"
              else if (pipeStatus === "suspended") { stepStatus = "waiting"; stepType = "suspend"; waitingFor = "approval" }
              else {
                if (menuWaiting[instanceId] !== undefined) {
                  stepStatus = "waiting"; stepType = "menu"; waitingFor = "menu"
                } else if (receiveWaiting[instanceId] !== undefined) {
                  stepStatus = "waiting"; stepType = "receive"; waitingFor = "receive"
                } else {
                  stepStatus = "running"
                  if (currentStep) {
                    const lower = currentStep.toLowerCase()
                    if      (lower.includes("reason"))  stepType = "reason"
                    else if (lower.includes("invoke"))  stepType = "invoke"
                    else if (lower.includes("task"))    stepType = "task"
                    else if (lower.includes("notify"))  stepType = "notify"
                    else if (lower.includes("choice"))  stepType = "choice"
                    else if (lower.includes("collect")) stepType = "collect"
                    else if (lower.includes("resolve")) stepType = "resolve"
                  }
                }
              }
            }

            aiParticipants.push({
              instance_id:   instanceId,
              agent_type_id: agentTypeId || instanceId.replace(/-\d{3}$/, ""),
              pool_id:       poolId,
              role, segment_id: segmentId, joined_at: joinedAt,
              ai_state: {
                current_step: currentStep, step_type: stepType,
                step_status:  stepStatus,  waiting_for: waitingFor,
                since_ms:     Math.max(0, sinceMs),
              },
            })
          }
        } else if (tenantId) {
          // No AI agents but fetch transitions for pipeline view
          try {
            const pipeRaw2 = await redis.get(`${tenantId}:pipeline:${sessionId}`)
            if (pipeRaw2) {
              const ps = JSON.parse(pipeRaw2) as Record<string, unknown>
              pipelineTransitions = (ps["transitions"] as unknown[]) ?? []
            }
          } catch { /* non-fatal */ }
        }
      } catch { /* non-fatal — ai_participants section */ }

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
          // context_snapshot: ContextStore data filtered and masked by viewer role (v2)
          context_snapshot: contextSnapshot,
          // contact_context: legacy pipeline_state field (v1 — present only when ContextStore absent)
          contact_context: contextSnapshot ? null : contactContext,
        },
        /** Arc 11 Fase D — AI agents active in this session with Skill-Flow state */
        ai_participants:      aiParticipants,
        /** Arc 11 Fase D — Skill-Flow step transition history */
        pipeline_transitions: pipelineTransitions,
      })
    } catch {
      res.status(500).json({ error: "state_unavailable" })
    }
  })

  // POST /api/inject-context/:sessionId
  // Arc 11 Fase D — Supervisor injects a key/value into the ContextStore for an active session.
  // Body: { key: string, value: unknown, confidence?: number, source?: string }
  // Writes to Redis hash {tenantId}:ctx:{sessionId} as a ContextEntry JSON blob.
  app.post("/api/inject-context/:sessionId", async (req: Request, res: Response) => {
    const payload = requireJwtRole(req.headers.authorization, ["operator", "supervisor", "admin", "developer"], res)
    if (!payload) return

    const { sessionId } = req.params
    const { key, value, confidence = 0.9, source = "supervisor_inject" } = req.body as {
      key?: string; value?: unknown; confidence?: number; source?: string
    }
    if (!key || value === undefined) {
      res.status(400).json({ error: "key and value are required" })
      return
    }

    // Phase 2 — namespace write permission by role. Resolve role igual ao requireJwtRole
    // (claim `role` OU primeiro de `roles[]`) — senão um JWT que use `roles[]` (admin/
    // supervisor) cai no default "operator" e é barrado até de namespaces permitidos.
    const writeRole = ((payload["role"] ?? (payload["roles"] as string[] | undefined)?.[0]) as string) ?? "operator"
    const writeNs   = (key as string).split(".")[0] ?? ""
    const OPERATOR_WRITABLE_NS = ["agent", "service"]
    // Exact tags the operator may WRITE beyond the namespaces above — the write-side
    // analog of context_visibility.operator_allow_tags (read). caller.customer_id é a
    // AÇÃO DE IDENTIFICAÇÃO/VÍNCULO (Cliente 360 C1a: corrigir/vincular o cliente),
    // um id interno (não PII); o resto de caller.* (cpf/nome/…) segue restrito.
    const OPERATOR_WRITABLE_TAGS = ["caller.customer_id"]
    if (writeRole === "operator" && !OPERATOR_WRITABLE_NS.includes(writeNs) && !OPERATOR_WRITABLE_TAGS.includes(key as string)) {
      res.status(403).json({
        error:   "forbidden_namespace",
        message: `Role 'operator' cannot write to '${key}'. Allowed namespaces: ${OPERATOR_WRITABLE_NS.join(", ")}; allowed tags: ${OPERATOR_WRITABLE_TAGS.join(", ")}.`,
      })
      return
    }

    try {
      // Resolve tenantId from session meta
      let tenantId: string | null = null
      try {
        const metaRaw = await redis.get(`session:${sessionId}:meta`)
        if (metaRaw) tenantId = (JSON.parse(metaRaw) as Record<string, string>)["tenant_id"] ?? null
      } catch { /* non-fatal */ }
      if (!tenantId) { res.status(404).json({ error: "session_not_found" }); return }

      const entry = {
        value,
        confidence: Math.min(1, Math.max(0, Number(confidence) || 0.9)),
        source,
        visibility: "agents_only",
        updated_at: new Date().toISOString(),
      }
      // J5a — mesmo roteamento do context_set: journey.* → hash do processo (raiz
      // canônica, TTL 30d), demais tags → hash da sessão. Injetar `journey.*` na sessão
      // faria o contexto do processo evaporar em 4h e não ser visto pelos outros contatos.
      const routed = await writeContextTag(redis, tenantId, sessionId as string, key as string, JSON.stringify(entry))
      res.json({ ok: true, key, session_id: sessionId, tenant_id: tenantId, scope: routed.scope, journey_root: routed.journeyRoot })
    } catch {
      res.status(500).json({ error: "inject_failed" })
    }
  })

  // POST /api/force-complete/:sessionId
  // Arc 11 Fase D — Supervisor forces a running Skill-Flow pipeline to the completed state.
  // Body: { reason?: string, outcome?: string }
  // Updates {tenantId}:pipeline:{sessionId} status → "completed" in Redis.
  app.post("/api/force-complete/:sessionId", async (req: Request, res: Response) => {
    const payload = requireJwtRole(req.headers.authorization, ["supervisor", "admin"], res)
    if (!payload) return

    const { sessionId } = req.params
    const { reason = "supervisor_force_complete", outcome = "resolved" } = req.body as {
      reason?: string; outcome?: string
    }
    try {
      // Resolve tenantId from session meta
      let tenantId: string | null = null
      try {
        const metaRaw = await redis.get(`session:${sessionId}:meta`)
        if (metaRaw) tenantId = (JSON.parse(metaRaw) as Record<string, string>)["tenant_id"] ?? null
      } catch { /* non-fatal */ }
      if (!tenantId) { res.status(404).json({ error: "session_not_found" }); return }

      const pipeKey = `${tenantId}:pipeline:${sessionId}`
      let pipeline: Record<string, unknown> = {}
      try {
        const pipeRaw = await redis.get(pipeKey)
        if (pipeRaw) pipeline = JSON.parse(pipeRaw) as Record<string, unknown>
      } catch { /* non-fatal */ }

      pipeline["status"]                = "completed"
      pipeline["force_complete_reason"] = reason
      pipeline["force_complete_outcome"] = outcome
      pipeline["force_complete_at"]     = new Date().toISOString()
      await redis.set(pipeKey, JSON.stringify(pipeline))

      res.json({ ok: true, session_id: sessionId, reason, outcome })
    } catch {
      res.status(500).json({ error: "force_complete_failed" })
    }
  })

  // GET /conversation_history/:sessionId
  // Returns the full ordered message list for a session.
  // Written by channel-gateway (inbound via WebchatAdapter, outbound via OutboundConsumer).
  // Key: session:{sessionId}:messages — Redis List (RPUSH, LRANGE).
  // Each entry is a JSON-serialised ChatMessage { id, author, text, timestamp }.
  app.get("/api/conversation_history/:sessionId", async (req: Request, res: Response) => {
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
  // Powers the Console "Transfer" combo (escalations) + suggested agents.
  // Resolves tenant_id/pool_id from session meta, then reads the pool's
  // supervisor_config.escalation_pools from the agent-registry — the same source
  // as the supervisor_capabilities MCP tool. (Was a hardcoded empty stub → Transfer
  // always showed "No destinations available" even with escalation_pools configured.)
  app.get("/api/supervisor_capabilities/:sessionId", async (req: Request, res: Response) => {
    const { sessionId } = req.params
    let tenantId = process.env["PLUGHUB_TENANT_ID"] ?? "tenant_demo"
    let poolId   = ""
    try {
      const metaRaw = await redis.get(`session:${sessionId}:meta`)
      if (metaRaw) {
        const meta = JSON.parse(metaRaw) as Record<string, string>
        if (meta["tenant_id"]) tenantId = meta["tenant_id"]
        poolId = meta["pool_id"] ?? ""
      }
    } catch { /* use fallback tenant; poolId stays "" */ }

    const escalations: Array<{ pool_id: string }> = []
    if (poolId && tenantId) {
      try {
        const registryUrl = process.env["AGENT_REGISTRY_URL"] ?? "http://agent-registry:3300"
        const r = await fetch(`${registryUrl}/v1/pools/${encodeURIComponent(poolId)}`, {
          headers: { "x-tenant-id": tenantId },
        })
        if (r.ok) {
          const pool = await r.json() as Record<string, unknown>
          const sc   = pool["supervisor_config"] as Record<string, unknown> | null
          const pools = sc?.["escalation_pools"]
          if (Array.isArray(pools)) {
            for (const pid of pools) {
              if (typeof pid === "string" && pid) escalations.push({ pool_id: pid })
            }
          }
        }
      } catch { /* agent-registry unavailable — return empty escalations */ }
    }

    res.json({ suggested_agents: [], escalations })
  })

  // GET /copilot_state/:sessionId
  // Returns the latest co-pilot suggestions written by AI Gateway (copilot_emitter.py).
  // Reads {tenantId}:ctx:{sessionId} hash fields prefixed with "session.copilot.*".
  // Called by the Agent Assist UI (useCopilotState hook) after receiving copilot.updated.
  app.get("/api/copilot_state/:sessionId", async (req: Request, res: Response) => {
    const { sessionId } = req.params

    // Resolve tenant from session meta (same pattern as supervisor_state)
    let tenantId = process.env["PLUGHUB_TENANT_ID"] ?? "tenant_demo"
    try {
      const metaRaw = await redis.get(`session:${sessionId}:meta`)
      if (metaRaw) {
        const meta = JSON.parse(metaRaw) as Record<string, string>
        if (meta["tenant_id"]) tenantId = meta["tenant_id"]
      }
    } catch { /* use env fallback */ }

    try {
      const ctxKey = `${tenantId}:ctx:${sessionId}`
      const fields = [
        "session.copilot.sugestao_resposta",
        "session.copilot.flags_risco",
        "session.copilot.acoes_recomendadas",
        "session.copilot.ultima_analise",
      ]
      const raw = await (redis as any).hmget(ctxKey, ...fields)

      const readEntry = (v: string | null): unknown => {
        if (!v) return null
        try { return JSON.parse(v) } catch { return null }
      }

      const entryValue = (v: string | null): unknown => {
        const entry = readEntry(v) as Record<string, unknown> | null
        return entry?.value ?? null
      }

      const sugestaoRaw  = entryValue(raw?.[0])
      const flagsRaw     = entryValue(raw?.[1])
      const acoesRaw     = entryValue(raw?.[2])
      const ultimaRaw    = entryValue(raw?.[3])

      res.json({
        session_id:         sessionId,
        sugestao_resposta:  typeof sugestaoRaw === "string" ? sugestaoRaw : null,
        flags_risco:        Array.isArray(flagsRaw)  ? flagsRaw  : [],
        acoes_recomendadas: Array.isArray(acoesRaw)  ? acoesRaw  : [],
        ultima_analise:     typeof ultimaRaw  === "string" ? ultimaRaw  : null,
      })
    } catch {
      res.status(500).json({ error: "copilot_state_unavailable" })
    }
  })

  // ── Frente 1 (dispatch pull) — inbox do Console ─────────────────────────────
  // GET  /api/work_queue/list?tenant_id=&pools=a,b&top_n=  → contatos claimáveis (Redis-direct)
  // POST /api/work_queue/claim/:sessionId    { pool_id, instance_id, conference_id? }
  // POST /api/work_queue/release/:sessionId  { pool_id, instance_id }
  // A escrita (claim/release) vai ao Routing Engine (único árbitro) via lib/work-queue.
  const _wqRoutingUrl = process.env["PLUGHUB_ROUTING_URL"] ?? "http://routing-engine:3550"
  const _wqAdminToken = process.env["ROUTING_ADMIN_TOKEN"] || undefined
  const _wqTenant = () => process.env["PLUGHUB_TENANT_ID"] ?? process.env["TENANT_ID"] ?? "tenant_demo"
  // I5 — o gatilho de supervisor retoma a workflow pelo channel-gateway (mesmo
  // ingress do submit humano), então precisa da URL dele aqui.
  const _wqGatewayUrl =
    process.env["CHANNEL_GATEWAY_URL"] ??
    process.env["CHANNEL_GATEWAY_HTTP_URL"] ??
    "http://channel-gateway:8010"

  app.get("/api/work_queue/list", async (req: Request, res: Response) => {
    try {
      const tenantId = (req.query["tenant_id"] as string) || _wqTenant()
      const poolsRaw = (req.query["pools"] as string) || ""
      const pools    = poolsRaw.split(",").map(s => s.trim()).filter(Boolean)
      const topN     = req.query["top_n"] ? parseInt(req.query["top_n"] as string, 10) : 20
      const contacts = await listQueue(redis, tenantId, pools, topN)
      res.json({ contacts, total: contacts.length })
    } catch (err) {
      res.status(500).json({ error: "list_failed", message: String(err) })
    }
  })

  app.post("/api/work_queue/claim/:sessionId", async (req: Request, res: Response) => {
    try {
      const sessionId  = String(req.params["sessionId"] ?? "")
      const body       = (req.body ?? {}) as Record<string, unknown>
      const tenantId   = (body["tenant_id"] as string) || _wqTenant()
      const poolId     = body["pool_id"] as string
      const instanceId = body["instance_id"] as string
      if (!sessionId || !poolId || !instanceId) {
        res.status(400).json({ error: "missing_fields", message: "session_id, pool_id e instance_id são obrigatórios" })
        return
      }
      const result = await claimTask(_wqRoutingUrl, _wqAdminToken, {
        tenant_id:        tenantId,
        pool_id:          poolId,
        session_id:       sessionId,
        instance_id:      instanceId,
        conference_id:    (body["conference_id"] as string) ?? "",
        // Camada B — claimant explícito (opcional; senão o engine deriva de instance_id).
        claimant_user_id: (body["claimant_user_id"] as string) ?? "",
      })
      res.json(result)
    } catch (err) {
      res.status(502).json({ error: "routing_unreachable", message: String(err) })
    }
  })

  app.post("/api/work_queue/release/:sessionId", async (req: Request, res: Response) => {
    try {
      const sessionId  = String(req.params["sessionId"] ?? "")
      const body       = (req.body ?? {}) as Record<string, unknown>
      const tenantId   = (body["tenant_id"] as string) || _wqTenant()
      const poolId     = body["pool_id"] as string
      const instanceId = body["instance_id"] as string
      if (!sessionId || !poolId || !instanceId) {
        res.status(400).json({ error: "missing_fields", message: "session_id, pool_id e instance_id são obrigatórios" })
        return
      }
      const result = await releaseTask(_wqRoutingUrl, _wqAdminToken, {
        tenant_id:   tenantId,
        pool_id:     poolId,
        session_id:  sessionId,
        instance_id: instanceId,
      })
      res.json(result)
    } catch (err) {
      res.status(502).json({ error: "routing_unreachable", message: String(err) })
    }
  })

  // ── I5 (D4) — supervisor encerra uma pendência de trabalho ──────────────────
  // POST /api/work_queue/expire/:sessionId  { tenant_id? }   role: supervisor|admin
  //
  // Encerra SEM disposição: o supervisor não finge ser o autor (D5). Por isso não
  // existe corpo de formulário aqui — só a decisão de encerrar.
  //
  // O caminho é o MESMO do prazo vencido (um caminho, dois gatilhos): resume do
  // workflow com decision="timeout", que faz o flow seguir seu on_timeout, o
  // handle_resume encerrar o item de trabalho e o bridge fechar o segmento. O que
  // distingue os dois é o `source`, que o bridge lê para escolher entre
  // acw_expired e acw_supervisor_closed.
  //
  // O Bearer do supervisor é REPASSADO ao channel-gateway de propósito: assim o
  // resume é autorado e auditado como dele (A5), em vez de chegar como "externo".
  app.post("/api/work_queue/expire/:sessionId", async (req: Request, res: Response) => {
    const claims = requireJwtRole(req.headers.authorization, ["supervisor", "admin"], res)
    if (!claims) return
    const sessionId = String(req.params["sessionId"] ?? "")
    const body      = (req.body ?? {}) as Record<string, unknown>
    const tenantId  = (body["tenant_id"] as string) || _wqTenant()
    if (!sessionId) {
      res.status(400).json({ error: "missing_fields", message: "session_id é obrigatório" })
      return
    }
    // O resume_token vem do ledger escrito no despacho do delegate — é o único
    // lugar que liga a sessão ao item parqueado. Ausente = nada a encerrar (ou o
    // item já foi encerrado): 404 explícito, nunca um 200 que não fez nada.
    let ledger: Record<string, unknown> | null = null
    try {
      const raw = await redis.get(keys.workTask(tenantId, sessionId))
      if (raw) ledger = JSON.parse(raw) as Record<string, unknown>
    } catch { /* tratado abaixo como ausência */ }
    const resumeToken = (ledger?.["resume_token"] as string) ?? ""
    if (!resumeToken) {
      res.status(404).json({
        error:   "no_work_task",
        message: `nenhum item de trabalho parqueado para a sessão ${sessionId}`,
      })
      return
    }
    const supervisor = String(claims["sub"] ?? "unknown")
    try {
      const r = await fetch(
        `${_wqGatewayUrl}/v1/channels/webhook/resume/${encodeURIComponent(resumeToken)}`,
        {
          method:  "POST",
          headers: {
            "Content-Type": "application/json",
            ...(req.headers.authorization ? { Authorization: req.headers.authorization } : {}),
          },
          body: JSON.stringify({
            tenant_id: tenantId,
            payload:   { decision: "timeout", source: `supervisor:${supervisor}` },
          }),
        },
      )
      const data = await r.json().catch(() => ({}))
      if (!r.ok) {
        res.status(r.status).json({ error: "expire_failed", detail: data })
        return
      }
      res.json({
        expired:    true,
        session_id: sessionId,
        pool_id:    ledger?.["pool_id"] ?? null,
        closed_by:  supervisor,
        ...(data as Record<string, unknown>),
      })
    } catch (err) {
      res.status(502).json({ error: "channel_gateway_unreachable", message: String(err) })
    }
  })

  // POST /api/agent_done/:sessionId — light signal for UI teardown (actual done via MCP tool)
  app.post("/api/agent_done/:sessionId", async (req: Request, res: Response) => {
    const { sessionId } = req.params
    try {
      const body    = req.body as Record<string, unknown>
      const outcome = (body?.outcome as string) ?? "resolved"

      // Look up contact_id, channel and pool_id from session metadata so we can
      // notify the customer's WebSocket via conversations.outbound and resolve the
      // wrap-up mode (inline vs detached) for the Console teardown.
      let contactId = sessionId
      let channel   = "chat"
      let poolId    = ""
      try {
        const metaRaw = await redis.get(`session:${sessionId}:meta`)
        if (metaRaw) {
          const meta = JSON.parse(metaRaw) as Record<string, string>
          if (meta["contact_id"]) contactId = meta["contact_id"]
          if (meta["channel"])    channel   = meta["channel"]
          if (meta["pool_id"])    poolId    = meta["pool_id"]
        }
      } catch { /* use fallback */ }

      // 0. Set session:closed marker synchronously in Redis BEFORE any async
      //    Kafka events. This allows the pending_assignment delivery path
      //    (ws reconnect) to detect the race condition immediately — even if
      //    the orchestrator-bridge hasn't yet processed the Kafka contact_closed.
      //    TTL 7 days — same as orchestrator-bridge sets it.
      try {
        await redis.setex(`session:${sessionId}:closed`, 604800, "agent_closed")
      } catch (err) {
        console.error(`[agent_done] Could not set session:closed marker session=${sessionId}:`, err)
        // Non-fatal — continue with teardown
      }

      // 1. Notify the agent WebSocket that the human part is done.
      //    We publish "session.agent_done" instead of "session.closed" so the
      //    Agent Assist UI stays in the session during on_human_end hooks
      //    (wrapup, NPS, etc.).  The orchestrator-bridge publishes the actual
      //    "session.closed" event after all hooks complete via _trigger_contact_close.
      await redis.publish(`agent:events:${sessionId}`, JSON.stringify({
        type:   "session.agent_done",
        reason: outcome,
      }))

      // 2. Publish contact_closed to Kafka conversations.events so the orchestrator
      //    bridge restores the agent instance to ready state in the routing engine,
      //    fires on_human_end hooks (if any), and handles customer WS close.
      //    NOTE: must be Kafka (not Redis pub/sub) — the bridge is a Kafka consumer.
      //    instance_id is stored in session meta by the bridge on human agent activation.
      //
      //    Fase B: the bridge now owns the customer WebSocket close (conversations.outbound).
      //    If pool.hooks.on_human_end is non-empty, the bridge fires specialist agents
      //    (e.g. agente_finalizacao_v1) before closing the customer connection.
      //    If on_human_end is empty, the bridge closes immediately — same UX as before.
      // G7 Slice 1: prefer the instance_id sent by THIS console (the participant
      // closing). meta.instance_id is session-global (last-writer-wins on each
      // activate_human_agent), so in multi-humano it misattributes the close to
      // the last-activated human. Falls back to meta for backward compat. Ver g7 §10.
      let instanceId = (body?.["instance_id"] as string) ?? ""
      if (!instanceId) {
        try {
          const metaRaw2 = await redis.get(`session:${sessionId}:meta`)
          if (metaRaw2) {
            const meta2 = JSON.parse(metaRaw2) as Record<string, string>
            instanceId = meta2["instance_id"] ?? ""
          }
        } catch { /* non-fatal */ }
      }
      await kafka.publish("conversations.events", {
        event_type:  "contact_closed",
        session_id:  sessionId,
        instance_id: instanceId,
        reason:      "agent_closed",
        // Fase A (queue-attended-model): propagate the human agent's outcome so the
        // bridge can derive the session-level outcome from the last primary segment.
        outcome,
      })

      // NOTE: conversations.outbound (session.closed → customer WS close) is now
      // published by the orchestrator-bridge in process_contact_event, after all
      // on_human_end hooks have completed.  Removing it here prevents the race
      // where the customer WebSocket was closed before the finalisation agent ran.

      // ── Teardown do Console (Camada E2 — wrap-up unificado) ──────────────────
      // No modelo unificado o contato SEMPRE fecha no agent_done — o wrap-up é sempre
      // uma sessão SEPARADA (auto-atendida quando o hook é `dispatch: inline`, ou
      // puxada da inbox quando `detached`), nunca renderizado in-session na sessão do
      // contato. Logo o Console SEMPRE limpa a sessão de atendimento aqui; a sessão de
      // wrap-up entra depois (auto-claim ou pull). `inline_wrapup: false` = sempre
      // limpa. (O campo é mantido no contrato p/ o handleClose; a distinção in-session
      // do inline-conferência ANTIGO foi aposentada.)
      void poolId  // não mais consultado (o modo de entrega é decidido no item de pull)
      res.json({ ok: true, inline_wrapup: false })
    } catch {
      res.status(500).json({ error: "publish_failed" })
    }
  })

  // POST /api/session_transfer/:sessionId
  // Console "Transfer" action (human agent → another pool). Mirrors the
  // session_escalate MCP tool (mode: transfer) but authenticated via the operator's
  // JWT instead of a session_token. Removes the current agent from the conference
  // (participant_left), re-routes the session to target_pool (conversations.inbound
  // mode=transfer), and marks the agent done (outcome=transferred).
  //
  // Stage 1 (G7): basic transfer — origin leaves + re-route. This intentionally does
  // NOT fire on_human_end (no conversations.events/contact_closed), so no wrap-up/NPS
  // and no contact close yet. The transfer-aware wrap-up (on_human_end as segment-end,
  // close_reason=agent_transfer, NPS skipped, contact kept alive) is wired in the
  // orchestrator-bridge in a follow-up stage. See docs/guias/conference-mechanics.md.
  app.post("/api/session_transfer/:sessionId", async (req: Request, res: Response) => {
    const { sessionId } = req.params
    try {
      const payload       = verifyJwtPayload(req.headers.authorization)
      const userId        = typeof payload["sub"] === "string" ? payload["sub"] : ""
      const participantId = userId ? `human-${userId}` : ""
      if (!participantId) {
        res.status(401).json({ error: "unauthorized" })
        return
      }

      const body          = req.body as Record<string, unknown>
      const targetPool     = (body?.["target_pool"] as string) ?? ""
      const handoffReason  = (body?.["handoff_reason"] as string) || "agent_transfer"
      if (!targetPool) {
        res.status(400).json({ error: "target_pool_required" })
        return
      }

      // Resolve tenant/channel/customer/instance from session meta.
      let tenantId       = process.env["PLUGHUB_TENANT_ID"] ?? "tenant_demo"
      let channel        = "webchat"
      let customerId     = ""
      let originInstance = participantId   // human-{userId}; bridge keys cleanup on this
      try {
        const metaRaw = await redis.get(`session:${sessionId}:meta`)
        if (metaRaw) {
          const meta = JSON.parse(metaRaw) as Record<string, string>
          if (meta["tenant_id"])        tenantId       = meta["tenant_id"]
          if (meta["channel"])          channel        = meta["channel"]
          if (meta["customer_id"])      customerId     = meta["customer_id"]
          else if (meta["contact_id"])  customerId     = meta["contact_id"]
          if (meta["instance_id"])      originInstance = meta["instance_id"]
        }
      } catch { /* use fallbacks */ }

      const eventId   = crypto.randomUUID()
      const timestamp = new Date().toISOString()
      // Channel must be one of the ConversationInboundEvent literals or the routing
      // request fails validation. session meta sometimes stores "chat" → normalise.
      const VALID_CHANNELS = ["whatsapp", "webchat", "voice", "email", "sms", "instagram", "telegram", "webrtc", "webhook"]
      const routeChannel = VALID_CHANNELS.includes(channel) ? channel : "webchat"

      // 1. Current agent leaves the conference (visibility: all — customer must know).
      try {
        await writeStreamEntry(redis as any, {
          stream_key:  `session:${sessionId}:stream`,
          type:        "participant_left",
          author_id:   participantId,
          author_role: "primary",
          visibility:  "all",
          payload:     { participant_id: participantId, reason: handoffReason },
          event_id:    eventId,
          timestamp,
        })
      } catch { /* non-fatal */ }

      // 1b. Tell the ORIGIN agent's Console to drop this contact — it has been handed off.
      //     session.closed with reason=agent_transfer (NOT a customer-disconnect reason)
      //     hits the Console's removal branch (unregister + delete from the contact list).
      //     Only the origin is subscribed to agent:events:{session_id} at transfer time; the
      //     target subscribes fresh after it is assigned, so it is not affected.
      try {
        await redis.publish(`agent:events:${sessionId}`, JSON.stringify({
          type:       "session.closed",
          session_id: sessionId,
          reason:     "agent_transfer",
        }))
      } catch { /* non-fatal */ }

      // 2. Remove the origin agent from the conference (segment-end). Triggers the
      //    bridge's contact_closed(reason=agent_transfer) branch (G7): restore instance,
      //    participant_left (analytics, outcome=transferred), agent_done lifecycle DECR,
      //    and SREM session:{id}:human_agents — clearing human_active so the re-route can
      //    activate the target agent. The transfer branch does NOT set session:closed,
      //    does NOT fire on_human_end (wrap-up/NPS) and does NOT close the contact.
      //    Published BEFORE the re-route so the origin is gone before the target activates.
      await kafka.publish("conversations.events", {
        event_type:  "contact_closed",
        session_id:  sessionId,
        instance_id: originInstance,
        reason:      "agent_transfer",
        outcome:     "transferred",
      })

      // 3. Re-route the session to the target pool. Must be a VALID ConversationInboundEvent
      //    (routing request) — required: session_id, tenant_id, customer_id (non-empty),
      //    channel (valid literal), started_at. pool_id restricts routing to the target pool.
      //    The router migrates the session bucket from the origin pool to target_pool.
      //    (A previous version copied the session_escalate payload without started_at → it
      //    failed Pydantic validation and the Routing Engine discarded it as "Unrecognised
      //    inbound event" — the transfer never routed.)
      await kafka.publish("conversations.inbound", {
        session_id:  sessionId,
        tenant_id:   tenantId,
        customer_id: customerId || sessionId,
        channel:     routeChannel,
        pool_id:     targetPool,
        started_at:  timestamp,
      })

      res.json({ ok: true, session_id: sessionId, target_pool: targetPool, handoff_reason: handoffReason })
    } catch {
      res.status(500).json({ error: "transfer_failed" })
    }
  })

  // Menu substitution — supervisor answers a pending menu step on behalf of the customer.
  // XADD interaction_result to the session stream so the Skill Flow engine can resume
  // the suspended menu step.  Also pub/sub notifies agents watching the stream.
  app.post("/api/menu_submit/:sessionId", async (req: Request, res: Response) => {
    const { sessionId } = req.params
    const { menu_id, interaction, result, displayText: rawDisplayText, agent_key } = req.body as Record<string, unknown>
    // G7 (c): instance de origem do menu (ecoado do source_instance do menu.render).
    const explicitAgentKey = typeof agent_key === "string" && agent_key ? agent_key : ""

    if (!menu_id || !interaction) {
      res.status(400).json({ error: "menu_id and interaction are required" })
      return
    }

    try {
      const eventId = crypto.randomUUID()
      const now     = new Date().toISOString()

      // Normalise result to string for LPUSH — same logic as bridge process_inbound:
      // plain string for button/list; JSON for checklist (array) or form (object).
      const resultText = typeof result === "string"
        ? result
        : JSON.stringify(result)

      // Display text for echo in chat — prefer the label sent by the frontend,
      // fall back to the raw result value.
      const displayText = typeof rawDisplayText === "string" && rawDisplayText
        ? rawDisplayText
        : resultText

      // 1. Resolve the human agent's participant_id so we can match against
      //    the visibility arrays in menu:waiting — same logic as the WS text
      //    handler (line 1383): vis.includes(agentPid).
      //    The bridge writes session.human_agent_participant_id to the
      //    ContextStore ({tenantId}:ctx:{sessionId}) before firing hooks.
      const menuTenantId = process.env["PLUGHUB_TENANT_ID"] ?? "tenant_demo"
      let agentPid = "human_agent"
      try {
        const ctxKey = `${menuTenantId}:ctx:${sessionId}`
        const raw = await redis.hget(ctxKey, "session.human_agent_participant_id")
        if (raw) {
          const entry = JSON.parse(raw)
          if (typeof entry.value === "string" && entry.value) {
            agentPid = entry.value
          }
        }
      } catch { /* fallback to "human_agent" */ }
      console.log(`[menu_submit] session=${sessionId} agentPid=${agentPid}`)

      // 2. Check menu:waiting hash — route to the correct agent's BLPOP key.
      //    Mirrors the WS text handler: agents_only matches any agent message;
      //    array visibility only matches if it includes this agent's participant_id.
      let pushed = false
      let targetVisibility: unknown = "agents_only"
      try {
        const waitingHash = await redis.hgetall(`menu:waiting:${sessionId}`)
        console.log(`[menu_submit] session=${sessionId} menu:waiting =`, JSON.stringify(waitingHash))
        // G7 (c): roteamento determinístico por instance de origem. O Console ecoa
        // o source_instance do menu.render como agent_key — casa direto a fila do
        // agente dono do menu, sem depender de resolução de pid (multi-humano).
        // Mantém o scan por visibility como fallback (botões legados sem agent_key
        // e substituição por supervisor).
        if (explicitAgentKey && waitingHash && waitingHash[explicitAgentKey] !== undefined) {
          try {
            const meta = JSON.parse(waitingHash[explicitAgentKey] as string)
            targetVisibility = meta.visibility ?? "agents_only"
          } catch { /* keep default visibility */ }
          const resultKey = explicitAgentKey !== "_default_"
            ? `menu:result:${sessionId}:${explicitAgentKey}`
            : `menu:result:${sessionId}`
          console.log(`[menu_submit] LPUSH ${resultKey} [value] (explicit agent_key)`)
          await redis.lpush(resultKey, resultText)
          pushed = true
        }
        if (!pushed && waitingHash && Object.keys(waitingHash).length > 0) {
          for (const [agentKey, metaJson] of Object.entries(waitingHash)) {
            try {
              const meta = JSON.parse(metaJson as string)
              // Mention-protocol standby: wake-on-interrupt-only — skip.
              if (meta.standby === true) continue
              const vis = meta.visibility
              if (vis === "agents_only") {
                targetVisibility = vis
                const resultKey = agentKey !== "_default_"
                  ? `menu:result:${sessionId}:${agentKey}`
                  : `menu:result:${sessionId}`
                console.log(`[menu_submit] LPUSH ${resultKey} [value] (agents_only match)`)
                await redis.lpush(resultKey, resultText)
                pushed = true
                break
              }
              if (Array.isArray(vis) && vis.includes(agentPid)) {
                targetVisibility = vis
                const resultKey = agentKey !== "_default_"
                  ? `menu:result:${sessionId}:${agentKey}`
                  : `menu:result:${sessionId}`
                console.log(`[menu_submit] LPUSH ${resultKey} [value] (visibility includes ${agentPid})`)
                await redis.lpush(resultKey, resultText)
                pushed = true
                break
              }
            } catch { /* skip malformed entry */ }
          }
        }
      } catch (err) {
        console.error("[menu_submit] Error checking menu:waiting:", err)
      }

      // 2. Write echo message to canonical stream via writeStreamEntry (invariant:
      //    never call redis.xadd() directly in mcp-server-plughub).
      //    Using type "message" ensures the Agent Assist UI renders it as a normal
      //    chat bubble.  The flat author_id / author_role fields are required by
      //    _parse_entry() in analytics-api for correct transcript rendering.
      try {
        await writeStreamEntry(redis as any, {
          stream_key:  `session:${sessionId}:stream`,
          type:        "message",
          author_id:   agentPid,
          author_role: "primary",
          visibility:  targetVisibility as "all" | "agents_only" | string[],
          segment_id:  undefined,
          payload: {
            message_id: eventId,
            content:    { type: "text", text: displayText },
            text:       displayText,
          },
          timestamp:   now,
          event_id:    eventId,
        })
        await redis.expire(`session:${sessionId}:stream`, 14400)
      } catch { /* non-fatal */ }

      // 3. Publish to conversations.events Kafka so the echo is persisted to
      //    ClickHouse via the analytics-api consumer.  Without this, closed-session
      //    transcripts (Redis TTL expired → ClickHouse fallback) are missing the
      //    human agent's button selection.
      try {
        await kafka.publish("conversations.events", {
          event_type:   "message_sent",
          session_id:   sessionId,
          tenant_id:    menuTenantId,
          message_id:   eventId,
          author_id:    agentPid,
          author_role:  "primary",
          content:      displayText,
          content_type: "text",
          visibility:   typeof targetVisibility === "string"
            ? targetVisibility
            : JSON.stringify(targetVisibility),
          timestamp:    now,
        })
      } catch { /* non-fatal — analytics persistence is best-effort */ }

      // 4. Publish message.text to pub/sub — the Agent Assist UI listens for this
      //    event type to show real-time chat bubbles.  This matches the WS text
      //    handler pattern (line 1397).
      try {
        await redis.publish(
          `agent:events:${sessionId}`,
          JSON.stringify({
            type:       "message.text",
            message_id: eventId,
            author:     { type: "agent_human", id: agentPid },
            text:       displayText,
            timestamp:  now,
            visibility: targetVisibility,
          }),
        )
      } catch { /* non-fatal */ }

      console.log(`[menu_submit] Done. pushed=${pushed} menu_id=${menu_id}`)
      res.json({ ok: true, event_id: eventId, pushed })
    } catch (err) {
      console.error("[menu_submit] Fatal error:", err)
      res.status(500).json({ error: "publish_failed" })
    }
  })

  // ── Arc 8 — Human agent pause / resume REST endpoints ────────────────────
  // Called by the Agent Assist UI when the human clicks "Pausar" (with a reason)
  // or "Retomar". The agent is identified by the JWT sub claim (userId) so that
  // the instance key matches the per-user model: instanceId = "human-${userId}".
  // Falls back to "human-${poolId}" for old tokens that lack a sub claim.
  // ─────────────────────────────────────────────────────────────────────────

  app.put("/api/agent-pause", async (req: Request, res: Response) => {
    const payload = requireJwtRole(req.headers.authorization, ["operator", "supervisor", "admin"], res)
    if (!payload) return

    try {
      const body = req.body as Record<string, unknown>
      const poolId     = (body["pool_id"]     as string | undefined) ?? ""
      const reasonId   = (body["reason_id"]   as string | undefined) ?? ""
      const reasonLabel = (body["reason_label"] as string | undefined) ?? ""
      const note       = (body["note"]         as string | undefined) ?? ""
      const maxMinutes = typeof body["max_minutes"] === "number" ? (body["max_minutes"] as number) : 0
      // Durable pause TTL = reason's max_minutes + 30m grace (so a forgotten pause
      // auto-expires and the next login starts ready); default 4h, capped at 16h.
      const pauseTtlSec = Math.min(maxMinutes > 0 ? maxMinutes * 60 + 1800 : 4 * 3600, 16 * 3600)

      if (!poolId) {
        res.status(400).json({ error: "pool_id is required" })
        return
      }

      const tenantId   = process.env["PLUGHUB_TENANT_ID"] ?? "tenant_demo"
      const jwtUserId  = typeof payload["sub"] === "string" ? payload["sub"] : ""
      const instanceId = jwtUserId ? `human-${jwtUserId}` : `human-${poolId}`
      // Human instances are stored as a JSON string at ${tenant}:instance:${id}
      // (registerHumanAgent), NOT as a hash under keys.agentInstance — read/write
      // the string accordingly or the lookup silently 404s and never publishes.
      const instanceKey = `${tenantId}:instance:${instanceId}`
      const raw = await redis.get(instanceKey)
      if (!raw) {
        res.status(404).json({ error: "instance_not_found", instance_id: instanceId })
        return
      }
      let inst: Record<string, unknown>
      try {
        inst = JSON.parse(raw) as Record<string, unknown>
      } catch {
        res.status(500).json({ error: "instance_corrupt", instance_id: instanceId })
        return
      }
      if ((inst["status"] as string | undefined) === "paused") {
        // Idempotent — already paused.
        res.json({ ok: true, instance_id: instanceId, state: "paused", timestamp: new Date().toISOString() })
        return
      }

      // Mark paused in the instance JSON and remove from every pool allocation set.
      inst["status"] = "paused"
      await redis.set(instanceKey, JSON.stringify(inst))
      const pools: string[] = Array.isArray(inst["pools"]) ? (inst["pools"] as string[]) : [poolId]
      for (const pid of pools) {
        await redis.srem(keys.poolInstances(tenantId, pid), instanceId)
        await redis.srem(keys.poolAvailable(tenantId, pid), instanceId)
      }

      // Durable pause marker — survives a full logout (WS disconnect on Console
      // navigation deletes the instance). registerHumanAgent and the WS heartbeat
      // read this to keep status="paused" across reconnects; resume deletes it.
      await redis.set(
        `${tenantId}:agent_paused:${instanceId}`,
        JSON.stringify({ reason_id: reasonId, reason_label: reasonLabel, note, max_minutes: maxMinutes, paused_at: new Date().toISOString() }),
        "EX", pauseTtlSec,
      )

      // Publish agent_pause to agent.lifecycle with reason fields
      await kafka.publish("agent.lifecycle", {
        event:        "agent_pause",
        tenant_id:    tenantId,
        instance_id:  instanceId,
        pool_id:      poolId,
        reason_id:    reasonId,
        reason_label: reasonLabel,
        note:         note,
        timestamp:    new Date().toISOString(),
      })

      res.json({ ok: true, instance_id: instanceId, state: "paused", timestamp: new Date().toISOString() })
    } catch (err) {
      console.error("[agent-pause] Error:", err)
      res.status(500).json({ error: "pause_failed" })
    }
  })

  app.put("/api/agent-resume", async (req: Request, res: Response) => {
    const payload = requireJwtRole(req.headers.authorization, ["operator", "supervisor", "admin"], res)
    if (!payload) return

    try {
      const body = req.body as Record<string, unknown>
      const poolId = (body["pool_id"] as string | undefined) ?? ""

      if (!poolId) {
        res.status(400).json({ error: "pool_id is required" })
        return
      }

      const tenantId   = process.env["PLUGHUB_TENANT_ID"] ?? "tenant_demo"
      const jwtUserId2 = typeof payload["sub"] === "string" ? payload["sub"] : ""
      const instanceId = jwtUserId2 ? `human-${jwtUserId2}` : `human-${poolId}`
      // Human instance is a JSON string at ${tenant}:instance:${id} (see pause).
      const instanceKey = `${tenantId}:instance:${instanceId}`
      const raw = await redis.get(instanceKey)
      if (!raw) {
        res.status(404).json({ error: "instance_not_found", instance_id: instanceId })
        return
      }
      let inst: Record<string, unknown>
      try {
        inst = JSON.parse(raw) as Record<string, unknown>
      } catch {
        res.status(500).json({ error: "instance_corrupt", instance_id: instanceId })
        return
      }

      // Mark ready and re-add to every pool allocation set.
      inst["status"] = "ready"
      await redis.set(instanceKey, JSON.stringify(inst))
      // Clear the durable pause marker so reconnects no longer restore the pause.
      await redis.del(`${tenantId}:agent_paused:${instanceId}`)
      const pools: string[] = Array.isArray(inst["pools"]) ? (inst["pools"] as string[]) : [poolId]
      for (const pid of pools) {
        await redis.sadd(keys.poolInstances(tenantId, pid), instanceId)
      }

      // Publish agent_ready carrying the FULL identity so the routing engine's
      // _upsert_instance does not wipe user_id/user_login/execution_model/pools
      // (it rebuilds the Redis instance from this event). This also closes the
      // open pause interval in analytics via the agent_ready close-check.
      await kafka.publish("agent.lifecycle", {
        event:                   "agent_ready",
        tenant_id:               tenantId,
        instance_id:             instanceId,
        agent_type_id:           (inst["agent_type_id"] as string) ?? `human_agent_${poolId}`,
        user_id:                 (inst["user_id"] as string) ?? "",
        user_login:              (inst["user_login"] as string) ?? "",
        status:                  "ready",
        execution_model:         "stateful",
        current_sessions:        (inst["current_sessions"] as number) ?? 0,
        pools:                   Array.isArray(inst["pools"]) ? inst["pools"] : [poolId],
        max_concurrent_sessions: (inst["max_concurrent"] as number) ?? 1,
        timestamp:               new Date().toISOString(),
      })

      res.json({ ok: true, instance_id: instanceId, state: "ready", timestamp: new Date().toISOString() })
    } catch (err) {
      console.error("[agent-resume] Error:", err)
      res.status(500).json({ error: "resume_failed" })
    }
  })

  // ── GET /api/agent-state ──────────────────────────────────────────────────
  // Returns whether the calling human agent is currently paused, from the durable
  // pause marker. The Agent Assist UI reads this on mount so the Pause button
  // reflects reality after a reconnect (the local React state resets to false).
  app.get("/api/agent-state", async (req: Request, res: Response) => {
    const payload = requireJwtRole(req.headers.authorization, ["operator", "supervisor", "admin"], res)
    if (!payload) return
    try {
      const tenantId  = process.env["PLUGHUB_TENANT_ID"] ?? "tenant_demo"
      const jwtUserId = typeof payload["sub"] === "string" ? payload["sub"] : ""
      const poolId    = (req.query["pool_id"] as string | undefined) ?? ""
      const instanceId = jwtUserId ? `human-${jwtUserId}` : `human-${poolId}`
      const raw = await redis.get(`${tenantId}:agent_paused:${instanceId}`)
      if (!raw) {
        res.json({ paused: false })
        return
      }
      let info: Record<string, unknown> = {}
      try { info = JSON.parse(raw) as Record<string, unknown> } catch { /* ignore */ }
      res.json({
        paused:       true,
        reason_id:    (info["reason_id"] as string) ?? "",
        reason_label: (info["reason_label"] as string) ?? "",
        paused_at:    (info["paused_at"] as string) ?? "",
      })
    } catch (err) {
      console.error("[agent-state] Error:", err)
      res.status(500).json({ error: "state_failed" })
    }
  })

  // ── POST /api/agent-clear-pause ───────────────────────────────────────────
  // Clears the durable pause marker on EXPLICIT logout (end of shift) so the next
  // login starts ready. Navigation/crash do not hit this — only the Logout flow.
  // Idempotent and best-effort; a no-op if the user is not a paused agent.
  app.post("/api/agent-clear-pause", async (req: Request, res: Response) => {
    const payload = requireJwtRole(req.headers.authorization, ["operator", "supervisor", "admin"], res)
    if (!payload) return
    try {
      const tenantId  = process.env["PLUGHUB_TENANT_ID"] ?? "tenant_demo"
      const jwtUserId = typeof payload["sub"] === "string" ? payload["sub"] : ""
      if (jwtUserId) await redis.del(`${tenantId}:agent_paused:human-${jwtUserId}`)
      res.json({ ok: true })
    } catch (err) {
      console.error("[agent-clear-pause] Error:", err)
      res.status(500).json({ error: "clear_failed" })
    }
  })

  // ── GET /api/instances ────────────────────────────────────────────────────
  // Lists all live agent instances from Redis.
  // Redis is the source of truth for runtime instance state (orchestrator-bridge
  // bootstrap writes here; PostgreSQL agent-registry only tracks registered types).
  //
  // Query params:
  //   tenant_id? — defaults to PLUGHUB_TENANT_ID env var
  //   pool_id?   — filter by pool
  //   status?    — filter by status (ready|busy|paused|draining|login|logout)
  app.get("/api/instances", async (req: Request, res: Response) => {
    try {
      const tenantId  = (req.query["tenant_id"] as string | undefined)
        ?? process.env["PLUGHUB_TENANT_ID"]
        ?? "tenant_default"
      const filterPool   = req.query["pool_id"] as string | undefined
      const filterStatus = req.query["status"]  as string | undefined

      // Collect all instance IDs from every pool's membership set
      const poolIds: string[] = filterPool
        ? [filterPool]
        : await redis.smembers(`${tenantId}:pools`)

      // Segurança Fase D — escopa ao DOMÍNIO de pools do chamador (Bearer). Filtrar a
      // lista de pools aqui já escopa as instâncias (só as de pools acessíveis). Sem
      // token / inválido / accessible_pools=[] (convenção admin) → irrestrito. Erros nunca
      // 401 (read-only, consistente com os demais snapshots operacionais).
      let accessible: string[] | null = null
      try {
        const payload = verifyJwtPayload(req.headers["authorization"] as string | undefined)
        const raw = payload["accessible_pools"]
        if (Array.isArray(raw) && raw.length > 0) accessible = raw.map(String)
      } catch { /* sem token / inválido → irrestrito */ }
      const scopedPoolIds = accessible
        ? poolIds.filter(pid => accessible!.includes(pid))
        : poolIds

      const instanceIds = new Set<string>()
      await Promise.all(
        scopedPoolIds.map(async pid => {
          const members = await redis.smembers(`${tenantId}:pool:${pid}:instances`)
          members.forEach(id => instanceIds.add(id))
        }),
      )

      // Read each instance JSON
      const results: Record<string, unknown>[] = []
      await Promise.all(
        Array.from(instanceIds).map(async iid => {
          const raw = await redis.get(`${tenantId}:instance:${iid}`)
          if (!raw) return
          try {
            const inst = JSON.parse(raw) as Record<string, unknown>
            if (filterStatus && inst["status"] !== filterStatus) return
            results.push(inst)
          } catch { /* skip malformed entry */ }
        }),
      )

      results.sort((a, b) =>
        String(a["instance_id"] ?? "").localeCompare(String(b["instance_id"] ?? ""))
      )

      res.json({ instances: results, total: results.length })
    } catch (err) {
      res.status(500).json({ error: "instances_unavailable", detail: String(err) })
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

  // ── Conexões vivas por (usuário, pool) ────────────────────────────────────
  //
  // O unregister pertence a uma CONEXÃO, não ao par (usuário, pool): "esta aba
  // fechou" não é o mesmo fato que "este agente saiu deste pool". O cancelamento
  // por `pendingUnregister` cobre só o caso em que a reconexão chega DENTRO dos
  // 2,5 s de graça; um Ctrl+Shift+R mais lento que isso derruba o agente inteiro:
  // as N conexões fecham, os N unregisters rodam em sequência e o último conclui
  // "era o último pool" e DELeta o registro de um agente que está reconectando.
  //
  // Observado em 2026-07-28, com a aritmética já correta (o Lua atômico funciona):
  //   status=partial      allPools=[3]  remaining=[2]
  //   status=partial      allPools=[2]  remaining=[1]
  //   status=full_logout  allPools=[1]  remaining=(none)   → DEL
  //
  // O dano não termina no DEL. O `agent_logout` publicado ali é assíncrono e pode
  // ser consumido DEPOIS da reregistração, marcando `logged_out` um agente que
  // acabou de logar — e aí o registro existe, mas o roteamento o ignora
  // (`get_ready_instances` exige `state == "ready"`).
  //
  // Contador de conexões vivas: o timer só age se, no instante em que dispara,
  // não houver conexão daquele agente naquele pool. Cobre reconexão de qualquer
  // duração, e não só a que cabe na janela de graça.
  const liveConnections = new Map<string, number>()
  const connKey = (user: string, pool: string) => `${user}::${pool}`
  const addConnection = (user: string, pool: string) => {
    const k = connKey(user, pool)
    liveConnections.set(k, (liveConnections.get(k) ?? 0) + 1)
  }
  const dropConnection = (user: string, pool: string) => {
    const k = connKey(user, pool)
    const next = (liveConnections.get(k) ?? 1) - 1
    if (next > 0) liveConnections.set(k, next)
    else liveConnections.delete(k)
  }
  const hasLiveConnection = (user: string, pool: string) =>
    (liveConnections.get(connKey(user, pool)) ?? 0) > 0

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
    // Per-user identity — sent by platform-ui from the JWT sub claim.
    // Falls back to poolId-based key for old clients that do not send user_id.
    const userId              = url.searchParams.get("user_id") ?? ""
    // Expected instance for THIS connection — matches registerHumanAgent's
    // instanceId format ("human-{userId}"). conversation.assigned is published to
    // the pool-wide channel pool:events:{poolId}, so without this filter EVERY
    // agent in the pool would receive (and display) a contact routed to ONE of
    // them. We accept an assignment only when its instance_id targets this user.
    // Empty userId (legacy client) → no filter, preserving old single-human-per-pool
    // behaviour. Empty event.instance_id → accept (defensive; never over-filter).
    const expectedInstanceId  = userId ? `human-${userId}` : ""
    // C1 — human login (email) for analytics identity, denormalized onto the segment.
    const userLogin           = url.searchParams.get("user_login") ?? ""
    const maxConcurrentSessions = Math.max(1, parseInt(url.searchParams.get("max_concurrent") ?? "3", 10))
    console.log(`[agent-ws] New WebSocket connection: pool=${poolId} user=${userId || "(legacy)"} max_concurrent=${maxConcurrentSessions} from=${request.socket.remoteAddress}`)

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
        await writeStreamEntry(redis as any, {
          stream_key:  `session:${sessionId}:stream`,
          type,
          author_id:   agentInstanceId || poolId,
          author_role: agentRole,
          visibility:  "all",
          payload:     { participant_id: agentInstanceId || poolId, instance_id: agentInstanceId || poolId },
        })
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
          console.log(
            `[agent-ws] subscribedSessions updated: pool=${poolId} ` +
            `sessions=[${[...subscribedSessions].join(",")}]`
          )

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
        // Session ended — unsubscribe and tear down the session view.
        //
        // Three sources (Arc 14 Fase B):
        //
        //  reason="posatt_segment_complete" + recipients=[...]
        //    A single posatt segment (wrap-up OR NPS) finished.
        //    Only tear down if this agent's participant_id is in recipients.
        //    The human agent's participant_id = agentInstanceId (set from the
        //    conversation.assigned event, typically "human-{poolId}").
        //    NPS completion → recipients contains customer + NPS agent IDs,
        //    NOT the human agent → human WS stays open during wrap-up.
        //    Wrap-up completion → recipients contains human agent + wrap-up agent
        //    → human WS tears down.
        //
        //  reason="conference_destroyed"
        //    Broadcast: all posatt segments done, conference infrastructure torn
        //    down.  Always tear down regardless of recipients.
        //
        //  reason="agent_done"  (legacy / backward compat)
        //    Broadcast from _trigger_contact_close (non-Arc-14 path). Always tear down.
        //
        if (event["type"] === "session.closed" && typeof event["session_id"] === "string") {
          const closedId    = event["session_id"]
          const closeReason = typeof event["reason"] === "string" ? event["reason"] : ""
          const recipients  = Array.isArray(event["recipients"]) ? event["recipients"] as string[] : null

          let shouldTearDown = false

          if (closeReason === "posatt_segment_complete") {
            // Targeted close: only tear down if this agent is in the recipients list.
            // agentInstanceId is set from the conversation.assigned event (line ~1700).
            if (recipients !== null && agentInstanceId && recipients.includes(agentInstanceId)) {
              shouldTearDown = true
            }
          } else if (closeReason === "conference_destroyed" || closeReason === "agent_done") {
            // Broadcast close — always tear down.
            shouldTearDown = true
          }
          // Any other reason: keep the session channel open (e.g. "client_disconnect",
          // "timeout" — posatt hooks may still be running).

          if (shouldTearDown) {
            subscribedSessions.delete(closedId)
            subscriber.unsubscribe(`agent:events:${closedId}`, (err) => {
              if (err) console.error("Redis session unsubscribe error:", err)
            })
          }
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
      //
      // Race-condition guard: the agent may reconnect (Ctrl+Shift+R) immediately
      // after clicking "Encerrar". At that instant, the orchestrator-bridge may
      // not yet have processed the Kafka contact_closed event that deletes the
      // pool:pending_assignment key. We therefore validate the session:closed
      // marker (set synchronously by the /agent_done REST handler) before
      // re-delivering the assignment, and delete the stale key if found.
      redis.get(`pool:pending_assignment:${poolId}`).then(async (pendingRaw) => {
        if (pendingRaw && ws.readyState === WebSocket.OPEN) {
          try {
            const assignment = JSON.parse(pendingRaw)
            const assignedSessionId: string | undefined = assignment.session_id
            if (assignedSessionId) {
              const closedMarker = await redis.get(`session:${assignedSessionId}:closed`)
              if (closedMarker) {
                // Session was already closed — remove stale key and skip delivery
                await redis.del(`pool:pending_assignment:${poolId}`)
                console.log(
                  `[agent-ws] Skipped stale pending assignment (session closed): ` +
                  `pool=${poolId} session=${assignedSessionId} reason=${closedMarker}`
                )
                return
              }
            }
          } catch (err) {
            console.error(`[agent-ws] Error validating pending assignment session:`, err)
            // On error, fall through and deliver — better to deliver a possibly stale
            // assignment than to silently drop a live one.
          }
          // Targeted-assignment filter (same as the live pub/sub path): a pool has
          // a single pending_assignment key, so a reconnecting agent must not pick
          // up an assignment routed to a DIFFERENT user in the same pool.
          let pendingTarget = ""
          try { pendingTarget = String(JSON.parse(pendingRaw)?.instance_id ?? "") } catch { /* ignore */ }
          if (shouldDropAssignment("conversation.assigned", pendingTarget, expectedInstanceId)) {
            console.log(
              `[agent-ws] Skipped pending assignment for another agent: ` +
              `pool=${poolId} target=${pendingTarget} expected=${expectedInstanceId}`
            )
            return
          }
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
      // Conexão viva registrada ANTES do register: se um timer agendado por um
      // fechamento anterior disparar durante o await abaixo, ele vê o contador e
      // aborta, em vez de deslogar o agente que está entrando.
      addConnection(userId, poolId)
      registerHumanAgent(poolId, userId, userLogin, maxConcurrentSessions, redis, kafka).catch((err) => {
        // Item 2 / Etapa 2: gate de login negou — informa o Console e encerra.
        if (err instanceof HumanLoginDenied) {
          console.warn(
            `[agent-ws] login denied pool=${poolId} user=${userId} reason=${err.reason}`,
            err.details,
          )
          try {
            ws.send(JSON.stringify({ type: "login_denied", reason: err.reason, ...err.details }))
          } catch { /* ws já fechado */ }
          try { ws.close() } catch { /* idem */ }
          return
        }
        console.error(`[agent-ws] registerHumanAgent pool=${poolId} user=${userId}:`, err)
      })
    }

    subscriber.on("message", (channel: string, message: string) => {
      console.log(`[agent-ws] pub/sub received channel=${channel} type=${(() => { try { return JSON.parse(message).type } catch { return "?" } })()}`)

      // Arc 14 Fase B — recipient filter for posatt_segment_complete events.
      // forward() calls ws.send() BEFORE checking recipients, so the frontend
      // would receive every posatt_segment_complete and remove the contact
      // regardless of whether this agent is the intended target.
      // We apply the same recipients check here, before forwarding, so that:
      //   • NPS completion (recipients = [customer, nps_agent]) is NOT sent to
      //     the human agent's Console → wrap-up session stays open.
      //   • Wrap-up completion (recipients = [wrapup_agent, human_agent]) IS
      //     sent to the human agent's Console → session closes correctly.
      // When agentInstanceId is not yet known (assignment hasn't arrived), we
      // forward conservatively so the agent doesn't miss critical events.
      try {
        const _ev = JSON.parse(message) as Record<string, unknown>
        if (_ev["type"] === "session.closed" && _ev["reason"] === "posatt_segment_complete") {
          const _recip = Array.isArray(_ev["recipients"]) ? (_ev["recipients"] as string[]) : null
          if (_recip !== null && agentInstanceId && !_recip.includes(agentInstanceId)) {
            // This posatt segment close is not for this agent — skip forwarding.
            // Do NOT unsubscribe: the session is still active (other segments running).
            console.log(
              `[agent-ws] posatt_segment_complete filtered (agent=${agentInstanceId} not in recipients=[${_recip.join(",")}]) — not forwarded`
            )
            return
          }
        }
        // ── G7 (b) — outbound delivery isolation by array visibility ──────────
        // forward() does ws.send() unconditionally, so in a multi-human conference
        // EVERY Console subscribed to agent:events:{session} receives a wrap-up
        // menu.render / message.text addressed (visibility array) to ONE specific
        // human — the other human's Console would render it too. Drop array-vis
        // events that do not include this connection's own identity. String
        // visibilities ("all", "agents_only") pass through unchanged. When the
        // identity is unknown (pool-fallback, no userId) we forward conservatively.
        {
          const _selfId = expectedInstanceId || agentInstanceId || ""
          const _vis = _ev["visibility"]
          if (Array.isArray(_vis) && _selfId && !(_vis as string[]).includes(_selfId)) {
            console.log(
              `[agent-ws] array-visibility ${String(_ev["type"])} filtered ` +
              `(self=${_selfId} not in vis=[${(_vis as string[]).join(",")}]) — not forwarded`
            )
            return
          }
        }
        // ── G7 Slice 3 — self-skip do fan-out humano↔humano ───────────────────
        // O ramo normal do agent-WS publica a msg do humano em agent:events para os
        // OUTROS humanos. O remetente já a exibe via echo otimista local (id local-…,
        // que não casa com o message_id real → o dedup-por-id do Console não pega),
        // então pulamos o forward da própria msg de volta pra ele. Outros humanos
        // (instance ≠ self) recebem normalmente.
        {
          const _selfId2  = expectedInstanceId || agentInstanceId || ""
          const _author   = _ev["author"] as Record<string, unknown> | undefined
          const _authInst = _author && typeof _author["instance_id"] === "string"
            ? (_author["instance_id"] as string) : ""
          if (_ev["type"] === "message.text" && _selfId2 && _authInst && _authInst === _selfId2) {
            console.log(`[agent-ws] message.text self-skip (author=${_authInst} == self) — not forwarded`)
            return
          }
        }
        // ── Targeted-assignment filter ────────────────────────────────────────
        // conversation.assigned is published to the pool-wide channel; only the
        // agent the contact was routed to should receive it. Drop assignments
        // whose instance_id targets a DIFFERENT user in the same pool — otherwise
        // two agents (e.g. admin + operator) would both see the same contact.
        if (shouldDropAssignment(_ev["type"], _ev["instance_id"], expectedInstanceId)) {
          console.log(
            `[agent-ws] conversation.assigned filtered (target=${_ev["instance_id"]} != expected=${expectedInstanceId}) — not forwarded`
          )
          return
        }
      } catch { /* ignore parse errors — fall through to forward */ }

      forward(channel, message)

      // ── Co-pilot Phase 2 — fire-and-forget background analysis ────────────
      // When a customer message arrives in a session where a human agent is
      // present, trigger AI Gateway to analyze and write co-pilot suggestions.
      // The AI Gateway responds 202 immediately; analysis is async on its side.
      try {
        const event = JSON.parse(message) as Record<string, unknown>
        const author = event["author"] as Record<string, unknown> | undefined
        if (
          event["type"] === "message.text" &&
          author?.["type"] === "customer" &&
          typeof event["session_id"] === "string" &&
          typeof event["text"] === "string" &&
          agentTenantId
        ) {
          const aiGatewayUrl = process.env["PLUGHUB_AI_GATEWAY_URL"] ?? "http://ai-gateway:3200"
          const copilotPayload = JSON.stringify({
            session_id:       event["session_id"],
            tenant_id:        agentTenantId,
            customer_message: (event["text"] as string).slice(0, 1000),
          })
          // Fire-and-forget — never awaited, errors suppressed
          fetch(`${aiGatewayUrl}/v1/copilot/analyze`, {
            method:  "POST",
            headers: { "Content-Type": "application/json" },
            body:    copilotPayload,
          }).catch((err: unknown) => {
            console.warn(`[agent-ws] copilot analyze fire failed session=${event["session_id"]}:`, err)
          })
        }
      } catch { /* never block the forward path */ }
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
          const instanceId = userId ? `human-${userId}` : `human-${poolId}`
          // Carry the real status so a paused agent stays paused in routing across
          // heartbeats (otherwise the default "ready" silently resumes them).
          let hbStatus = "ready"
          try {
            if ((await redis.get(`${tenantId}:agent_paused:${instanceId}`)) !== null) hbStatus = "paused"
          } catch { /* non-fatal */ }
          // F1 do ADR `adr-human-agent-pool-scoped-identity` — liveness NUNCA
          // carrega identidade nem membership.
          //
          // Este pong vem de UMA das N conexões WS do humano (o Console abre uma
          // por pool selecionado). `agent_type_id: human_agent_${poolId}` e
          // `pools: [poolId]` descreviam APENAS esta conexão; como o
          // `_upsert_instance` da routing reconstruía o registro a partir do
          // evento, a identidade da instância oscilava a cada 15 s entre os pools
          // logados — e já roteou contato com o agent_type_id errado.
          //
          // `heartbeat_pool` diz de qual conexão veio o sinal SEM se passar por
          // membership: a routing só o usa na reconstrução degradada (registro
          // ausente), e loga quando o faz. `current_sessions` também sai — conta
          // só as sessões desta conexão, e a verdade é o SCARD do semáforo.
          kafka.publish("agent.lifecycle", {
            event:                   "agent_heartbeat",
            tenant_id:               tenantId,
            instance_id:             instanceId,
            heartbeat_pool:          poolId,
            // C1 — user_id/user_login são fatos do RECURSO e podem viajar; a
            // routing os preserva do registro vivo, isto é só belt-and-braces
            // para a reconstrução degradada.
            user_id:                 userId,
            user_login:              userLogin,
            status:                  hbStatus,
            execution_model:         "stateful",
            max_concurrent_sessions: maxConcurrentSessions,
            timestamp:               new Date().toISOString(),
          }).catch(() => {/* non-fatal */})
          return
        }

        if (msg["type"] !== "message.text") return  // ignore other unknown types

        // session_id is required in every outbound message.
        const targetSessionId = typeof msg["session_id"] === "string" ? msg["session_id"] : ""
        if (!targetSessionId) {
          console.warn(`[human-msg] dropped: no session_id in message from pool=${poolId}`)
          return
        }

        // Verify the target session is actually subscribed on this connection —
        // prevents rogue clients from injecting messages into arbitrary sessions.
        if (!subscribedSessions.has(targetSessionId)) {
          console.warn(
            `[human-msg] dropped: session=${targetSessionId} not in subscribedSessions ` +
            `(pool=${poolId}, subscribed=[${[...subscribedSessions].join(",")}])`
          )
          return
        }

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
            await writeStreamEntry(redis as any, {
              stream_key:  `session:${targetSessionId}:stream`,
              type:        "message",
              author_id:   agentInstanceId || poolId,
              author_role: agentRole,
              visibility:  "agents_only",
              event_id:    messageId,
              timestamp:   msgTs,
              payload:     { message_id: messageId, text: msgText },
            })
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
          //    Implementação compartilhada com a tool MCP `message_send` —
          //    ver lib/mention-routing.ts (F5 do ADR de identidade por-pool).
          //
          //    `poolId` aqui é o pool DESTA conexão WebSocket (query-param), e há
          //    uma conexão por pool selecionado no Console. É a resolução no
          //    escopo certo por construção — o que o outro chamador precisa
          //    reconstruir a partir do registro por-(sessão, instância).
          await routeMentions({
            text:              msgText,
            tenantId:          tenantIdForMentions,
            sessionId:         targetSessionId,
            senderPoolId:      poolId,
            fromParticipantId: agentInstanceId || poolId,
            redis,
            kafka,
            timestamp:         msgTs,
            logPrefix:         "[agent-ws]",
          })

          // Skip conversations.outbound — @mention messages are agents_only
          return
        }

        // ── Check if a hook agent is waiting for this agent's input ──────────
        // menu:waiting:{sessionId} is now a HASH with one field per waiting agent:
        //   field = instanceId, value = JSON({ visibility, masked })
        //
        // Agent messages are routed to agents whose visibility is:
        //   - "agents_only" → always receives agent messages
        //   - array containing this agent's participant_id → targeted visibility
        //
        // The matched visibility is used for the stream entry, ensuring the
        // message is only visible to the intended participants.
        let targetAgentKey:  string | null = null
        let targetVisibility: unknown = null
        try {
          const waitingHash = await redis.hgetall(`menu:waiting:${targetSessionId}`)
          if (waitingHash && Object.keys(waitingHash).length > 0) {
            // Resolve the human agent's participant_id from ContextStore so that
            // array-visibility menu steps (e.g. wrap-up using
            // ["@ctx.session.human_agent_participant_id"]) match correctly.
            // The bridge writes session.human_agent_participant_id before firing hooks.
            // Mirrors the same lookup in the menu_submit handler.
            // G7 (c): resolver o remetente pela identidade da PRÓPRIA conexão
            // (expectedInstanceId = human-${userId}). Com a visibility do wrap-up
            // agora por-segmento ([served_human_pid] = human-${userId}), isso
            // desambigua N humanos. O campo de SESSÃO global colapsava em
            // multi-humano. Fallback no global só quando a conexão é desconhecida
            // (pool-fallback legado sem userId).
            let agentPid = expectedInstanceId || agentInstanceId || poolId || "human_agent"
            if (!expectedInstanceId) {
              try {
                const ctxTenantId = agentTenantId || (process.env["PLUGHUB_TENANT_ID"] ?? "tenant_demo")
                const rawPid = await redis.hget(`${ctxTenantId}:ctx:${targetSessionId}`, "session.human_agent_participant_id")
                if (rawPid) {
                  const pidEntry = JSON.parse(rawPid)
                  if (typeof pidEntry.value === "string" && pidEntry.value) {
                    agentPid = pidEntry.value
                  }
                }
              } catch { /* fallback to instance/pool id */ }
            }
            for (const [aKey, metaJson] of Object.entries(waitingHash)) {
              try {
                const meta = JSON.parse(metaJson as string)
                // Mention-protocol standby (ex.: co-pilot aguardando @mention):
                // NUNCA recebe mensagens comuns — só interrupts do dispatch.
                // Sem este skip, qualquer texto do humano (inclusive o próprio
                // "@copilot ...") estourava o BLPOP do standby → segmento 0s.
                if (meta.standby === true) continue
                const vis = meta.visibility
                if (vis === "agents_only") {
                  targetAgentKey   = aKey
                  targetVisibility = vis
                  break
                }
                if (Array.isArray(vis) && vis.includes(agentPid)) {
                  targetAgentKey   = aKey
                  targetVisibility = vis
                  break
                }
              } catch { /* skip malformed entry */ }
            }
          }
        } catch { /* assume normal message on error */ }

        const outMsgId = crypto.randomUUID()
        const outAuthor = { type: "agent_human", id: agentInstanceId || poolId || "human_agent", instance_id: agentInstanceId || poolId }

        if (targetAgentKey) {
          // ── Hook agent response path ──────────────────────────────────────
          // The agent's message is a response to a hook agent (wrap-up, NPS, etc.).
          // Write to stream with the SAME visibility the hook agent used in its
          // question — ensures the response is only visible to the intended audience.
          const streamVis = (targetVisibility ?? "agents_only") as string[] | "all" | "agents_only"

          // 1. Write to stream with matched visibility
          try {
            await writeStreamEntry(redis as any, {
              stream_key:  `session:${targetSessionId}:stream`,
              type:        "message",
              author_id:   agentInstanceId || poolId || "human_agent",
              author_role: agentRole,
              visibility:  streamVis,
              event_id:    outMsgId,
              timestamp:   msgTs,
              payload:     {
                message_id: outMsgId,
                content:    { type: "text", text: msgText },
                text:       msgText,
              },
            })
            await redis.expire(`session:${targetSessionId}:stream`, 14400)
          } catch { /* non-fatal */ }

          // 2. Echo to agents via pub/sub so the Agent Assist UI shows it
          try {
            await redis.publish(`agent:events:${targetSessionId}`, JSON.stringify({
              type:       "message.text",
              message_id: outMsgId,
              author:     { type: "agent_human", id: agentInstanceId || poolId },
              text:       msgText,
              timestamp:  msgTs,
              visibility: streamVis,
            }))
          } catch { /* non-fatal */ }

          // 3. Feed the hook agent's isolated BLPOP key
          try {
            const resultKey = targetAgentKey !== "_default_"
              ? `menu:result:${targetSessionId}:${targetAgentKey}`
              : `menu:result:${targetSessionId}`
            await redis.lpush(resultKey, msgText)
          } catch { /* non-fatal — hook agent will timeout instead */ }

          // 4. Publish to analytics (ClickHouse persistence) for hook agent responses too
          try {
            await kafka.publish("conversations.events", {
              event_type:   "message_sent",
              message_id:   outMsgId,
              session_id:   targetSessionId,
              tenant_id:    agentTenantId || process.env["PLUGHUB_TENANT_ID"] || "tenant_demo",
              author_id:    agentInstanceId || poolId || "human_agent",
              author_role:  agentRole,
              content_type: "text",
              content:      msgText,
              visibility:   typeof streamVis === "string" ? streamVis : JSON.stringify(streamVis),
              timestamp:    msgTs,
            })
          } catch { /* non-fatal — analytics persistence is best-effort */ }
        } else {
          // ── Normal message path — deliver to customer ─────────────────────
          await kafka.publish("conversations.outbound", {
            type:       "message.text",
            contact_id: contactId,
            session_id: targetSessionId,
            message_id: outMsgId,
            channel:    msgChannel,
            direction:  "outbound",
            author:     outAuthor,
            content:    { type: "text", text: msgText },
            text:       msgText,   // kept for channel-gateway backward compat
            timestamp:  msgTs,
          })

          // G7 Slice 3 — fan-out humano↔humano: além do cliente (outbound), publica a
          // mensagem em agent:events:{session} para os OUTROS humanos da conferência a
          // receberem. O ramo normal não fazia isso (só @mention/hook publicavam aqui),
          // então peers humanos não viam as msgs uns dos outros. O próprio remetente é
          // filtrado no forward por author.instance_id == self (já tem o echo otimista
          // local). Cliente não assina agent:events; agentes IA leem o stream (escrito
          // abaixo) — sem duplicação. Ver conference-mechanics §Mudança 15.
          try {
            await redis.publish(`agent:events:${targetSessionId}`, JSON.stringify({
              type:       "message.text",
              message_id: outMsgId,
              author:     { type: "agent_human", id: agentInstanceId || poolId, instance_id: agentInstanceId || poolId },
              text:       msgText,
              timestamp:  msgTs,
              // session_id é OBRIGATÓRIO: o handler message.text do Console dropa o
              // evento sem ele (`if (!sid) return`). contact_id por paridade com o
              // evento do cliente (bridge). Ver conference-mechanics §Mudança 15.
              session_id: targetSessionId,
              contact_id: contactId,
              visibility: "all",
            }))
          } catch { /* non-fatal */ }

          // Write to canonical stream so supervision SSE and analytics can see the message.
          try {
            await writeStreamEntry(redis as any, {
              stream_key:  `session:${targetSessionId}:stream`,
              type:        "message",
              author_id:   agentInstanceId || poolId || "human_agent",
              author_role: agentRole,
              visibility:  "all",
              event_id:    outMsgId,
              timestamp:   msgTs,
              payload:     {
                message_id: outMsgId,
                content:    { type: "text", text: msgText },
                text:       msgText,
              },
            })
            await redis.expire(`session:${targetSessionId}:stream`, 14400)
            console.log(
              `[human-msg] XADD ok session=${targetSessionId} msg=${outMsgId} ` +
              `pool=${poolId} instance=${agentInstanceId || "none"}`
            )
          } catch (xaddErr) {
            console.error(`[human-msg] XADD failed session=${targetSessionId}:`, xaddErr)
          }

          // Publish to analytics (ClickHouse persistence) so messages survive Redis stream TTL
          try {
            await kafka.publish("conversations.events", {
              event_type:   "message_sent",
              message_id:   outMsgId,
              session_id:   targetSessionId,
              tenant_id:    agentTenantId || process.env["PLUGHUB_TENANT_ID"] || "tenant_demo",
              author_id:    agentInstanceId || poolId || "human_agent",
              author_role:  agentRole,
              content_type: "text",
              content:      msgText,
              visibility:   "all",
              timestamp:    msgTs,
            })
          } catch { /* non-fatal — analytics persistence is best-effort */ }
        }
      } catch (err) {
        console.error(`Agent WS message error:`, err)
      }
    })

    // Ping every 30s to keep the connection alive + detect DEAD (half-open) connections.
    // G7 heartbeat Slice 2: o ws.close nem sempre dispara num drop "sujo" (meia-conexão:
    // sleep, partição de rede). O ping de PROTOCOLO (ws.ping) é auto-respondido pelo
    // browser (RFC 6455) → o evento 'pong' reseta isAlive. Sem pong num ciclo completo →
    // conexão morta → ws.terminate() dispara ws.on('close') → grace → agent_disconnect
    // (heartbeat Slice 1) → re-rota. O {type:"ping"} app-level é mantido (o Console pode usá-lo).
    let isAlive = true
    ws.on("pong", () => { isAlive = true })
    const pingInterval = setInterval(() => {
      if (ws.readyState !== WebSocket.OPEN) return
      if (!isAlive) {
        console.warn(`[agent-ws] no pong within interval — terminating dead connection pool=${poolId} user=${userId}`)
        try { ws.terminate() } catch { /* noop */ }
        return
      }
      isAlive = false
      try { ws.ping() } catch { /* noop */ }
      ws.send(JSON.stringify({ type: "ping" }))
    }, 30_000)

    ws.on("close", () => {
      clearInterval(pingInterval)
      if (poolId) dropConnection(userId, poolId)
      console.log(`[agent-ws] WS closed: pool=${poolId} user=${userId} instanceId=human-${userId || poolId}`)
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
        console.log(`[agent-ws] Scheduling unregister in ${UNREGISTER_GRACE_MS}ms: pool=${poolId} user=${userId}`)
        const timer = setTimeout(async () => {
          console.log(`[agent-ws] Grace period elapsed — calling unregisterHumanAgent pool=${poolId} user=${userId}`)
          pendingUnregister.delete(poolId)
          // G7 heartbeat Slice 1 — queda involuntária: drop genuíno confirmado (sem reconnect
          // dentro do grace). Para cada sessão ativa onde ESTE humano AINDA está anexado
          // (em human_agents → não saiu por agent_done), publica contact_closed(agent_disconnect)
          // para o bridge encerrar o segmento e re-rotar/continuar. O sismember dedup evita
          // republicar quando o humano já fechou via agent_done (o SREM já o tirou da SET).
          if (agentInstanceId) {
            for (const sid of subscribedSessions) {
              try {
                const attached = await redis.sismember(`session:${sid}:human_agents`, agentInstanceId)
                if (attached) {
                  await kafka.publish("conversations.events", {
                    event_type:  "contact_closed",
                    session_id:  sid,
                    instance_id: agentInstanceId,
                    reason:      "agent_disconnect",
                  })
                  console.log(`[agent-ws] agent_disconnect published: session=${sid} instance=${agentInstanceId}`)
                }
              } catch (e) {
                console.error(`[agent-ws] agent_disconnect publish error session=${sid}:`, e)
              }
            }
          }
          // O agente reconectou neste pool enquanto o timer corria (reload, troca
          // de aba, queda de rede curta). O fechamento que agendou este timer é
          // história — sair do pool agora derrubaria uma conexão VIVA.
          if (hasLiveConnection(userId, poolId)) {
            console.log(
              `[agent-ws] unregister ABORTADO pool=${poolId} user=${userId} — ` +
              `há conexão viva (reconectou após a janela de graça)`
            )
            return
          }
          unregisterHumanAgent(poolId, userId, redis, kafka).catch((err) =>
            console.error(`[agent-ws] unregisterHumanAgent pool=${poolId} user=${userId}:`, err)
          )
        }, UNREGISTER_GRACE_MS)
        pendingUnregister.set(poolId, timer)
      } else {
        console.log(`[agent-ws] WS close — no poolId, skipping unregister`)
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
      console.log(`   Tools Deploy:        skill_deploy, skill_handoff_status`)
      console.log(`   Tools Calendar:      calendar_is_open, calendar_next_slot, calendar_add_duration, calendar_business_duration`)
      console.log(`   Tools AgentEvents:   agent_event`)
      console.log(`   SKILL_FLOW_URL:      ${process.env["SKILL_FLOW_URL"] ?? "http://localhost:3400 (padrão — configure SKILL_FLOW_URL para Docker)"}`)
      console.log(`   WORKFLOW_API_URL:    ${process.env["WORKFLOW_API_URL"] ?? "http://localhost:3800 (padrão)"}`)
      resolve()
    })
  })
}

