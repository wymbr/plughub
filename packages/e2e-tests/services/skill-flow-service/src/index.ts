/**
 * skill-flow-service/src/index.ts
 *
 * Thin HTTP wrapper around @plughub/skill-flow-engine for E2E testing.
 * Exposes a simple REST API so E2E tests can drive the Skill Flow engine
 * without coupling to its TypeScript API directly.
 */

import express, { Request, Response } from "express"
import Redis from "ioredis"
import * as fs   from "fs"
import * as path from "path"
import * as yaml from "js-yaml"
import { Client } from "@modelcontextprotocol/sdk/client/index.js"
import { SSEClientTransport } from "@modelcontextprotocol/sdk/client/sse.js"
import { SkillFlowEngine } from "@plughub/skill-flow-engine"
import type { SkillFlowEngineConfig, ResumeContext } from "@plughub/skill-flow-engine"
import { ContextStore } from "./context-store"
import type { SkillFlow } from "@plughub/schemas"

// ── Config ────────────────────────────────────────────────────────────────────

const PORT            = parseInt(process.env["PORT"]            ?? "3400", 10)
const REDIS_URL       = process.env["REDIS_URL"]                ?? "redis://localhost:6379"
const MCP_SERVER_URL  = process.env["MCP_SERVER_URL"]           ?? "http://localhost:3100"
const MCP_AUTH_URL    = process.env["MCP_AUTH_URL"]             ?? "http://localhost:3150"
const AI_GATEWAY_URL  = process.env["AI_GATEWAY_URL"]           ?? "http://localhost:3200"
// Arc 19 Fase D: calendar-api for business-hours deadline calculation on suspend steps
const CALENDAR_API_URL = process.env["CALENDAR_API_URL"]        ?? "http://localhost:3700"
// Arc 19 delegate: channel-gateway for creating child sessions via handle_delegate
const CHANNEL_GATEWAY_URL = process.env["CHANNEL_GATEWAY_URL"] ?? "http://localhost:8010"

// Map of named MCP server → base URL.
// Add entries here when new domain MCP servers are introduced.
const MCP_SERVER_URLS: Record<string, string> = {
  "mcp-server-plughub": MCP_SERVER_URL,
  "mcp-server-auth":    MCP_AUTH_URL,
}

// SKILLS_DIR: resolved relative to this file's location at runtime.
// Default: packages/skill-flow-engine/skills (dev) or /app/skills (Docker).
const _defaultSkillsDir = path.resolve(__dirname, "../../../../skill-flow-engine/skills")
const SKILLS_DIR = process.env["SKILLS_DIR"] ?? _defaultSkillsDir

// DELEGATION_JOB_TTL_S: how long the delegation Redis key lives (1h).
const DELEGATION_JOB_TTL_S = 3600

// ── Redis ─────────────────────────────────────────────────────────────────────

const redis = new Redis(REDIS_URL, {
  lazyConnect: false,
  maxRetriesPerRequest: 3,
})

redis.on("error", (err) => {
  console.error("[skill-flow-service] Redis error:", err)
})

// ── MCP client pool (one persistent SSE connection per server URL) ────────────

interface McpClientEntry {
  client:     Client | null
  connecting: Promise<void> | null
}

const mcpClientPool = new Map<string, McpClientEntry>()

function getPoolEntry(serverUrl: string): McpClientEntry {
  let entry = mcpClientPool.get(serverUrl)
  if (!entry) {
    entry = { client: null, connecting: null }
    mcpClientPool.set(serverUrl, entry)
  }
  return entry
}

async function getMcpClientForUrl(serverUrl: string): Promise<Client> {
  const entry = getPoolEntry(serverUrl)

  if (entry.client !== null) return entry.client

  if (entry.connecting !== null) {
    await entry.connecting
    return entry.client!
  }

  // Attempt connection with up to 3 retries and 500ms backoff.
  // Protects against a race where the health check passed but the /sse
  // endpoint is not yet accepting connections (startup jitter).
  const MAX_CONNECT_ATTEMPTS = 3
  let lastErr: unknown

  entry.connecting = (async () => {
    for (let attempt = 1; attempt <= MAX_CONNECT_ATTEMPTS; attempt++) {
      try {
        const client = new Client(
          { name: "skill-flow-service", version: "1.0.0" },
          { capabilities: {} }
        )
        const sseUrl = new URL(`${serverUrl}/sse`)
        const transport = new SSEClientTransport(sseUrl)
        await client.connect(transport)
        entry.client = client
        console.log(`[skill-flow-service] MCP client connected to ${serverUrl}/sse (attempt ${attempt})`)
        return
      } catch (err) {
        lastErr = err
        const msg = err instanceof Error ? err.message : String(err)
        console.warn(`[skill-flow-service] MCP connect attempt ${attempt}/${MAX_CONNECT_ATTEMPTS} failed for ${serverUrl}: ${msg}`)
        if (attempt < MAX_CONNECT_ATTEMPTS) {
          await new Promise(r => setTimeout(r, 500 * attempt))
        }
      }
    }
    throw lastErr
  })()

  try {
    await entry.connecting
  } catch (err) {
    entry.client      = null
    entry.connecting  = null
    throw err
  }
  entry.connecting = null
  return entry.client!
}

/** Pre-warm all known MCP connections at startup (non-blocking — logs errors but does not fail). */
async function prewarmMcpConnections(): Promise<void> {
  for (const [name, url] of Object.entries(MCP_SERVER_URLS)) {
    try {
      await getMcpClientForUrl(url)
      console.log(`[skill-flow-service] Pre-warmed MCP connection: ${name} → ${url}`)
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      console.warn(`[skill-flow-service] Pre-warm failed for ${name} (${url}): ${msg}`)
    }
  }
}

function resolveMcpServerUrl(mcpServer?: string): string {
  if (!mcpServer) return MCP_SERVER_URL
  return MCP_SERVER_URLS[mcpServer] ?? MCP_SERVER_URL
}

// ── MCP call adapter ──────────────────────────────────────────────────────────

async function mcpCall(
  tool: string,
  input: unknown,
  mcpServer?: string,
): Promise<unknown> {
  const serverUrl = resolveMcpServerUrl(mcpServer)
  const entry = getPoolEntry(serverUrl)

  let client: Client
  try {
    client = await getMcpClientForUrl(serverUrl)
  } catch (err) {
    // Reset so next call retries
    entry.client = null
    entry.connecting = null
    const message = err instanceof Error ? err.message : String(err)
    throw new Error(`[skill-flow-service] MCP connect failed (${serverUrl}): ${message}`)
  }

  let result: Awaited<ReturnType<typeof client.callTool>>
  try {
    result = await client.callTool({
      name:      tool,
      arguments: input as Record<string, unknown>,
    })
  } catch (callErr) {
    // The SSE connection may have died (e.g. container rebuild).
    // Reset the pool entry and reconnect once before giving up.
    entry.client     = null
    entry.connecting = null
    console.warn(`[skill-flow-service] callTool failed for ${tool}@${serverUrl}, resetting pool and retrying once`)
    try {
      client = await getMcpClientForUrl(serverUrl)
      result = await client.callTool({
        name:      tool,
        arguments: input as Record<string, unknown>,
      })
    } catch (retryErr) {
      entry.client     = null
      entry.connecting = null
      const message = retryErr instanceof Error ? retryErr.message : String(retryErr)
      throw new Error(`[skill-flow-service] MCP callTool retry failed (${tool}@${serverUrl}): ${message}`)
    }
  }

  if (result.isError === true) {
    // Extract the error message from MCP text content
    const firstContent = Array.isArray(result.content) ? result.content[0] : undefined
    let errorDetail = "mcp_tool_error"
    if (
      firstContent &&
      typeof firstContent === "object" &&
      "type" in firstContent &&
      firstContent.type === "text" &&
      "text" in firstContent
    ) {
      errorDetail = firstContent.text as string
    }
    throw new Error(`[skill-flow-service] MCP tool error (${tool}): ${errorDetail}`)
  }

  // Parse text content
  const firstContent = Array.isArray(result.content) ? result.content[0] : undefined
  if (
    firstContent &&
    typeof firstContent === "object" &&
    "type" in firstContent &&
    firstContent.type === "text" &&
    "text" in firstContent
  ) {
    try {
      return JSON.parse(firstContent.text as string)
    } catch {
      return firstContent.text
    }
  }

  return result.content
}

// ── AI Gateway call adapter ───────────────────────────────────────────────────

async function aiGatewayCall(payload: {
  prompt_id:     string
  input:         Record<string, unknown>
  output_schema: Record<string, unknown>
  session_id:    string
  attempt:       number
  json_schema?:  Record<string, unknown>   // T7b — forwardado via JSON.stringify(payload)
  model_profile?: string                    // R8d — forwardado ao /v1/reason (ReasonRequest)
}): Promise<unknown> {
  const url = `${AI_GATEWAY_URL}/v1/reason`
  console.log(
    `[skill-flow-service] aiGatewayCall → POST ${url} session=${payload.session_id} prompt_id=${payload.prompt_id} attempt=${payload.attempt}`,
  )
  let res: globalThis.Response
  try {
    res = await fetch(url, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify(payload),
    })
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    console.error(`[skill-flow-service] aiGatewayCall network error: ${message}`)
    throw new Error(`[skill-flow-service] aiGatewayCall network error: ${message}`)
  }

  if (!res.ok) {
    const body = await res.text().catch(() => "(unreadable body)")
    console.error(
      `[skill-flow-service] aiGatewayCall HTTP ${res.status} from AI Gateway: ${body}`,
    )
    throw new Error(
      `[skill-flow-service] aiGatewayCall HTTP ${res.status} from AI Gateway: ${body}`,
    )
  }

  // The AI gateway returns a ReasonResponse wrapper: { session_id, result, model_used, ... }
  // executeReason validates against the *inner* result, so unwrap it here.
  const data = await res.json() as { result?: unknown }
  console.log(
    `[skill-flow-service] aiGatewayCall ← 200 OK session=${payload.session_id} result_keys=${Object.keys(data.result as object ?? {}).join(",")}`,
  )
  return data.result !== undefined ? data.result : data
}

// ── Engine factory ────────────────────────────────────────────────────────────
// ContextStore requires tenantId at construction, and tenantId varies per
// request.  Creating the engine per-request is cheap (constructor only builds
// a PipelineStateManager that holds a Redis reference), so we do that instead
// of keeping a global singleton without contextStore.
//
// IMPORTANT: Each engine gets a **dedicated** Redis connection via .duplicate().
// The menu step uses BLPOP (blocking command), and Redis only processes one
// blocking command per connection at a time.  If two agents (e.g. NPS + wrap-up)
// share the same connection, the second BLPOP is queued locally by ioredis until
// the first completes — causing serialization instead of parallelism.
// The caller MUST disconnect the dedicated connection after engine.run() completes.

function createEngine(
  tenantId: string,
  _sessionId: string,
  persistSuspendWebhook?: SkillFlowEngineConfig["persistSuspendWebhook"],
): { engine: SkillFlowEngine; dedicatedRedis: Redis } {
  const dedicatedRedis = redis.duplicate()
  const contextStore = new ContextStore({ redis: dedicatedRedis, tenantId })
  const engine = new SkillFlowEngine({
    redis: dedicatedRedis,
    mcpCall,
    aiGatewayCall,
    contextStore,
    ...(persistSuspendWebhook ? { persistSuspendWebhook } : {}),
  })
  return { engine, dedicatedRedis }
}

// ── Express app ───────────────────────────────────────────────────────────────

const app = express()
app.use(express.json())

// GET /health
app.get("/health", (_req: Request, res: Response) => {
  res.json({ status: "ok" })
})

// POST /execute
// Body: { tenant_id, session_id, customer_id, skill_id, flow, session_context, instance_id?,
//         webhook_pool?, resume_context? }
// Returns: { outcome, pipeline_state } | { error, active_job_id }
app.post("/execute", async (req: Request, res: Response) => {
  const {
    tenant_id,
    session_id,
    customer_id,
    skill_id,
    flow,
    session_context,
    config,
    instance_id,
    pipeline_session_id,
    segment_id,
    webhook_pool,
    resume_context,
  } = req.body as {
    tenant_id:       string
    session_id:      string
    customer_id:     string
    skill_id:        string
    flow:            SkillFlow
    session_context: Record<string, unknown>
    /** Dialog primitive §17.3-1 — slot config_json → engine $.config.* */
    config?:         Record<string, unknown>
    /** Routing Engine instance_id — stored in execution lock for crash detection. */
    instance_id?:    string
    /** When set, used for pipeline_state key and execution lock instead of session_id.
     *  Allows conference specialists (hook agents) to run in parallel on the same session. */
    pipeline_session_id?: string
    /** Segment UUID for segment-scoped ContextStore writes (scope: segment in YAML). */
    segment_id?:     string
    /**
     * Arc 19 — When true, this is a webhook pool session.
     * The engine wires persistSuspendWebhook to extend Redis TTLs and write
     * the resume_token to {tenant}:resume_tokens on suspend.
     */
    webhook_pool?:   boolean
    /**
     * Arc 19 — Resume context. Set when the session is being resumed after a suspend.
     * The suspend step reads this instead of suspending again.
     */
    resume_context?: {
      step_id:   string
      decision:  "approved" | "rejected" | "input" | "timeout"
      payload:   Record<string, unknown>
    }
  }

  if (!tenant_id || !session_id || !customer_id || !skill_id || !flow) {
    res.status(400).json({
      error: "BAD_REQUEST",
      message: "tenant_id, session_id, customer_id, skill_id, and flow are required",
    })
    return
  }

  console.log(
    `[skill-flow-service] /execute received: session=${session_id} skill=${skill_id} entry=${(flow as { entry?: string }).entry ?? "?"}` +
    (pipeline_session_id ? ` pipeline=${pipeline_session_id}` : ""),
  )

  // Arc 19: declare dedicatedRedis before the persistSuspendWebhook closure so the
  // callback can capture it. For non-webhook sessions this is identical to createEngine.
  const dedicatedRedis = redis.duplicate()
  const contextStore   = new ContextStore({ redis: dedicatedRedis, tenantId: tenant_id })

  // Arc 19: wire persistSuspendWebhook for webhook pool sessions.
  // The engine's _buildContext injects tenant_id and session_id into params automatically.
  // Responsibility: extend all session Redis key TTLs + write resume_token to the hash.
  const persistSuspendWebhookFn: SkillFlowEngineConfig["persistSuspendWebhook"] | undefined =
    webhook_pool
      ? async (params) => {
          // ── Deadline calculation ────────────────────────────────────────────
          // Arc 19 Fase D: when the suspend step requests business-hours-aware
          // expiry (business_hours=true + calendar_id provided), call the
          // calendar-api.  Fall back to wall-clock on any failure.
          let expiresAt: string
          if (params.business_hours && params.calendar_id) {
            try {
              const calResp = await fetch(
                `${CALENDAR_API_URL}/v1/engine/add-business-duration`,
                {
                  method:  "POST",
                  headers: { "Content-Type": "application/json" },
                  body:    JSON.stringify({
                    tenant_id:   params.tenant_id,
                    entity_type: "calendar",
                    entity_id:   params.calendar_id,
                    from_dt:     new Date().toISOString(),
                    hours:       params.timeout_hours,
                  }),
                },
              )
              if (calResp.ok) {
                const calData = await calResp.json() as { deadline?: string }
                expiresAt = calData.deadline ?? new Date(Date.now() + params.timeout_hours * 3_600_000).toISOString()
              } else {
                console.warn(
                  `[skill-flow-service] calendar-api ${calResp.status} — falling back to wall-clock deadline`,
                )
                expiresAt = new Date(Date.now() + params.timeout_hours * 3_600_000).toISOString()
              }
            } catch (err) {
              console.warn(`[skill-flow-service] calendar-api unreachable — wall-clock fallback: ${err}`)
              expiresAt = new Date(Date.now() + params.timeout_hours * 3_600_000).toISOString()
            }
          } else {
            expiresAt = new Date(Date.now() + params.timeout_hours * 3_600_000).toISOString()
          }

          const ttlS = Math.ceil(
            (new Date(expiresAt).getTime() - Date.now()) / 1000,
          ) + 3600   // +1h buffer

          // ── Extend session Redis key TTLs ────────────────────────────────────
          // Non-fatal: EXPIRE on a missing key is silently ignored.
          const sessionKeys = [
            `session:${params.session_id}:stream`,
            `${params.tenant_id}:ctx:${params.session_id}`,
            `${params.tenant_id}:pipeline:${params.session_id}`,
            `${params.tenant_id}:session:${params.session_id}:status`,
          ]
          for (const key of sessionKeys) {
            try { await dedicatedRedis.expire(key, ttlS) } catch { /* non-fatal */ }
          }

          // ── Write resume_token to hash ───────────────────────────────────────
          // WebhookAdapter.handle_resume() does HGET on this hash for token lookup.
          const tokenValue = `${params.session_id}:${params.step_id}:${expiresAt}`
          await dedicatedRedis.hset(
            `${params.tenant_id}:resume_tokens`,
            params.resume_token,
            tokenValue,
          )
          // Keep the hash alive at least until the deadline (best-effort).
          try { await dedicatedRedis.expire(`${params.tenant_id}:resume_tokens`, ttlS) } catch { /* non-fatal */ }

          return { resume_expires_at: expiresAt }
        }
      : undefined

  // Arc 19 delegate (v2): delegate() is A2A — the target agent ALWAYS runs as a
  // conference specialist INSIDE the caller's session (a segment, never a
  // standalone session nor a child workflow). This holds whether the caller is a
  // webhook workflow (Session B) or a webchat agent (Session A-new reconnect).
  // The specialist joins the parent session; messages go to the parent stream.
  // child workflows are created by task(), not by delegate().
  const persistDelegateFn: SkillFlowEngineConfig["persistDelegate"] | undefined =
    async (params) => {
      const resp = await fetch(
        `${CHANNEL_GATEWAY_URL}/v1/channels/webhook/delegate-conference`,
        {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({
            tenant_id:    params.tenant_id,
            pool_id:      params.pool,
            session_id:   params.session_id,
            customer_id,
            resume_token: params.resume_token,
            step_id:      params.step_id,   // parent's real delegate step id (resume matching)
            context:      params.context,
            timeout_hours: params.timeout_hours,
            // Identity Resolver (nível b) — gate the pending_by_customer dual-write.
            customer_resumable: params.customer_resumable ?? false,
            resume_policy:      params.resume_policy ?? "offer",
          }),
        },
      )
      if (!resp.ok) {
        const body = await resp.text().catch(() => "(unreadable)")
        throw new Error(
          `[skill-flow-service] persistDelegate (conference): channel-gateway ${resp.status}: ${body}`,
        )
      }
      const data = await resp.json() as { session_id: string }
      console.log(
        `[skill-flow-service] persistDelegate (conference): parent=${data.session_id} pool=${params.pool} step=${params.step_id}`,
      )
      return { child_session_id: data.session_id }   // = parent session_id (segment)
    }

  // Journey J4c — persistCollect callback. Unlike the legacy skill-flow-worker
  // (which posts to the now-deprecated workflow-api), the Arc 19 stack routes the
  // collect through the channel-gateway N2 handler: it resolves the channel from the
  // customer's reachability + the declarative channel_policy (N3 never names the
  // channel), creates the child contact session (inherits root → journey member),
  // and delivers. The workflow suspends until collect.responded resumes it.
  const persistCollectFn: SkillFlowEngineConfig["persistCollect"] | undefined =
    async (params) => {
      const resp = await fetch(
        `${CHANNEL_GATEWAY_URL}/v1/channels/webhook/collect`,
        {
          method:  "POST",
          headers: { "Content-Type": "application/json" },
          body:    JSON.stringify({
            tenant_id:      params.tenant_id,
            session_id:     params.session_id,   // caller (N3) session — root inherited from its ctx
            customer_id,
            step_id:        params.step_id,
            collect_token:  params.collect_token,
            target:         params.target,
            ...(params.channel        ? { channel:        params.channel }        : {}),
            ...(params.requires       ? { requires:       params.requires }       : {}),
            ...(params.channel_policy ? { channel_policy: params.channel_policy } : {}),
            ...(params.dialog_form_id ? { dialog_form_id: params.dialog_form_id } : {}),
            interaction:    params.interaction,
            prompt:         params.prompt,
            ...(params.options ? { options: params.options } : {}),
            ...(params.fields  ? { fields:  params.fields }  : {}),
            ...(params.scheduled_at ? { scheduled_at: params.scheduled_at } : {}),
            ...(params.delay_hours !== undefined ? { delay_hours: params.delay_hours } : {}),
            timeout_hours:  params.timeout_hours,
            business_hours: params.business_hours,
            ...(params.calendar_id ? { calendar_id: params.calendar_id } : {}),
            ...(params.campaign_id ? { campaign_id: params.campaign_id } : {}),
            ...(params.customer_resumable !== undefined ? { customer_resumable: params.customer_resumable } : {}),
            ...(params.resume_policy ? { resume_policy: params.resume_policy } : {}),
          }),
        },
      )
      if (!resp.ok) {
        const body = await resp.text().catch(() => "(unreadable)")
        throw new Error(
          `[skill-flow-service] persistCollect: channel-gateway ${resp.status}: ${body}`,
        )
      }
      const data = await resp.json() as { send_at: string; expires_at: string }
      console.log(
        `[skill-flow-service] persistCollect: session=${params.session_id} step=${params.step_id} token=${params.collect_token}`,
      )
      return { send_at: data.send_at, expires_at: data.expires_at }
    }

  const engine = new SkillFlowEngine({
    redis:        dedicatedRedis,
    mcpCall,
    aiGatewayCall,
    contextStore,
    ...(persistSuspendWebhookFn ? { persistSuspendWebhook: persistSuspendWebhookFn } : {}),
    ...(persistDelegateFn       ? { persistDelegate:       persistDelegateFn }       : {}),
    ...(persistCollectFn        ? { persistCollect:        persistCollectFn }        : {}),
  })

  try {
    const result = await engine.run({
      tenantId:       tenant_id,
      sessionId:      session_id,
      customerId:     customer_id,
      skillId:        skill_id,
      flow,
      sessionContext: session_context ?? {},
      ...(config ? { config } : {}),
      instanceId:     instance_id,
      segmentId:      segment_id,
      // Use pipeline_session_id for lock/state isolation when provided
      // (conference specialists). sessionId is still used for message delivery.
      pipelineSessionId: pipeline_session_id,
      // Arc 19: resume context — when set, the suspended step follows its on_resume path
      ...(resume_context ? { resumeContext: resume_context as ResumeContext } : {}),
    })

    if ("error" in result && result.error === "PRECONDITION_FAILED") {
      console.warn(`[skill-flow-service] /execute PRECONDITION_FAILED: session=${session_id} active_job=${result.active_job_id}`)
      res.status(412).json(result)
      return
    }

    const outcome = "outcome" in result ? result.outcome : "unknown"
    console.log(`[skill-flow-service] /execute completed: session=${session_id} skill=${skill_id} outcome=${outcome}`)
    res.json(result)
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    const stack   = err instanceof Error ? err.stack : undefined
    console.error(`[skill-flow-service] /execute ERROR: session=${session_id} skill=${skill_id}: ${message}`)
    if (stack) console.error(stack)
    res.status(500).json({ error: "INTERNAL_ERROR", message })
  } finally {
    // Disconnect the dedicated Redis connection to avoid connection leaks.
    // disconnect() is graceful — it waits for pending commands to finish.
    dedicatedRedis.disconnect()
  }
})

// GET /pipeline/:tenant_id/:session_id
// Reads pipeline state from Redis directly (bypasses engine lock).
app.get("/pipeline/:tenant_id/:session_id", async (req: Request, res: Response) => {
  const { tenant_id, session_id } = req.params as {
    tenant_id:  string
    session_id: string
  }

  const key = `${tenant_id}:pipeline:${session_id}`

  try {
    const raw = await redis.get(key)
    if (raw === null) {
      res.status(404).json({ error: "NOT_FOUND", message: `Pipeline not found: ${key}` })
      return
    }
    const pipeline_state: unknown = JSON.parse(raw)
    res.json({ pipeline_state })
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    console.error("[skill-flow-service] /pipeline read error:", message)
    res.status(500).json({ error: "INTERNAL_ERROR", message })
  }
})

// ── Skill YAML loader ─────────────────────────────────────────────────────────

function loadSkillFlow(skillId: string): SkillFlow | null {
  const filePath = path.join(SKILLS_DIR, `${skillId}.yaml`)
  try {
    const content = fs.readFileSync(filePath, "utf-8")
    return yaml.load(content) as SkillFlow
  } catch {
    console.warn(`[skill-flow-service] Skill YAML not found: ${filePath}`)
    return null
  }
}

// ── Delegation job executor (background) ─────────────────────────────────────

async function runDelegationJob(params: {
  jobId:           string
  tenantId:        string
  sessionId:       string   // parent session — used for comms (notify, menu)
  customerId:      string
  targetSkill:     string
  pipelineContext: Record<string, unknown>
}): Promise<void> {
  const { jobId, tenantId, sessionId, customerId, targetSkill, pipelineContext } = params
  const jobKey        = `${tenantId}:delegation:${jobId}`
  // Derive an isolated pipeline session id for the specialist so it doesn't
  // conflict with the primary agent's execution lock on the same session_id.
  const pipelineSessionId = `${sessionId}--assist--${jobId.slice(0, 8)}`

  const updateJob = async (fields: Record<string, unknown>) => {
    try {
      const current = await redis.get(jobKey)
      const existing = current ? JSON.parse(current) as Record<string, unknown> : {}
      await redis.set(jobKey, JSON.stringify({ ...existing, ...fields }), "EX", DELEGATION_JOB_TTL_S)
    } catch { /* non-fatal */ }
  }

  try {
    await updateJob({ status: "running", started_at: new Date().toISOString() })

    // Load skill flow
    const flow = loadSkillFlow(targetSkill)
    if (!flow) {
      await updateJob({ status: "failed", error: `Skill '${targetSkill}' not found in ${SKILLS_DIR}` })
      return
    }

    // Pre-seed the specialist's pipeline state with parent context so it can
    // read existing contact_context (if any) without re-collecting already-known data.
    if (Object.keys(pipelineContext).length > 0) {
      const seedState = {
        flow_id:         targetSkill,
        current_step_id: flow.entry,
        status:          "in_progress",
        started_at:      new Date().toISOString(),
        updated_at:      new Date().toISOString(),
        results:         pipelineContext,
        retry_counters:  {},
        transitions:     [],
      }
      await redis.set(
        `${tenantId}:pipeline:${pipelineSessionId}`,
        JSON.stringify(seedState),
        "EX",
        86400,
      )
    }

    // Run the skill flow engine.
    // sessionId  = parent session — notifications and menus route to the parent channel.
    // pipelineSessionId = derived — exclusive lock/state for the specialist.
    const { engine: delegationEngine, dedicatedRedis: delegationRedis } = createEngine(tenantId, sessionId)
    try {
    const result = await delegationEngine.run({
      tenantId,
      sessionId,               // comms → parent channel
      pipelineSessionId,       // state/lock → isolated
      customerId,
      skillId:        targetSkill,
      flow,
      sessionContext: {
        tenant_id:          tenantId,
        session_id:         sessionId,
        pipeline_session_id: pipelineSessionId,
        agent_type:         targetSkill,
        delegation_mode:    "assist",
      },
      instanceId: `assist-${jobId.slice(0, 8)}`,
    })

    if ("error" in result) {
      await updateJob({ status: "failed", error: result.error })
      return
    }

    // Merge contact_context from specialist back into parent pipeline state.
    // The parent is blocked in polling; this write is safe (no race).
    //
    // agente_contexto_ia_v1 stores output_as: contexto_final, so the contact_context
    // lives at specialistResults.contexto_final.contact_context — not at the top level.
    // Also check top-level as fallback for other specialist skills.
    const specialistResults = result.pipeline_state.results
    const contextoFinal = specialistResults["contexto_final"] as Record<string, unknown> | undefined
    const contactContextValue =
      contextoFinal?.["contact_context"] ??
      specialistResults["contact_context"]

    if (contactContextValue) {
      const parentKey = `${tenantId}:pipeline:${sessionId}`
      try {
        const parentRaw = await redis.get(parentKey)
        if (parentRaw) {
          const parentState = JSON.parse(parentRaw) as Record<string, unknown>
          const parentResults = (parentState["results"] as Record<string, unknown>) ?? {}
          // Write contact_context at top level so supervisor_state can find it easily
          parentResults["contact_context"] = contactContextValue
          parentState["results"] = parentResults
          parentState["updated_at"] = new Date().toISOString()
          await redis.set(parentKey, JSON.stringify(parentState), "EX", 86400)
          console.log(`[skill-flow-service] delegation ${jobId}: merged contact_context into parent pipeline`)
        }
      } catch (mergeErr) {
        console.warn(`[skill-flow-service] delegation ${jobId}: failed to merge contact_context:`, mergeErr)
      }
    } else {
      console.warn(`[skill-flow-service] delegation ${jobId}: no contact_context found in specialist results (keys: ${Object.keys(specialistResults).join(", ")})`)
    }

    await updateJob({
      status:       "completed",
      outcome:      result.outcome,
      result:       specialistResults,
      completed_at: new Date().toISOString(),
    })
    console.log(`[skill-flow-service] delegation ${jobId}: completed (outcome=${result.outcome})`)

    } finally {
      delegationRedis.disconnect()
    }

  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    console.error(`[skill-flow-service] delegation ${jobId} failed:`, message)
    await updateJob({ status: "failed", error: message, failed_at: new Date().toISOString() })
  }
}

// POST /delegate
// Body: { job_id, tenant_id, session_id, customer_id, target_skill, pipeline_context? }
// Returns immediately: { job_id, status: "accepted" }
// Background: runs the target skill as a specialist and updates Redis job key.
app.post("/delegate", (req: Request, res: Response) => {
  const {
    job_id,
    tenant_id,
    session_id,
    customer_id,
    target_skill,
    pipeline_context,
  } = req.body as {
    job_id:            string
    tenant_id:         string
    session_id:        string
    customer_id:       string
    target_skill:      string
    pipeline_context?: Record<string, unknown>
  }

  if (!job_id || !tenant_id || !session_id || !customer_id || !target_skill) {
    res.status(400).json({
      error: "BAD_REQUEST",
      message: "job_id, tenant_id, session_id, customer_id, and target_skill are required",
    })
    return
  }

  // Respond immediately — do not await the job
  res.json({ job_id, status: "accepted" })

  // Fire background execution (non-blocking)
  runDelegationJob({
    jobId:           job_id,
    tenantId:        tenant_id,
    sessionId:       session_id,
    customerId:      customer_id,
    targetSkill:     target_skill,
    pipelineContext: pipeline_context ?? {},
  }).catch(err => {
    console.error("[skill-flow-service] delegation background error:", err)
  })
})

// ── Start ─────────────────────────────────────────────────────────────────────

app.listen(PORT, () => {
  console.log(`[skill-flow-service] Listening on port ${PORT}`)
  console.log(`[skill-flow-service] Redis:            ${REDIS_URL}`)
  console.log(`[skill-flow-service] MCP plughub:      ${MCP_SERVER_URL}`)
  console.log(`[skill-flow-service] MCP auth:         ${MCP_AUTH_URL}`)
  console.log(`[skill-flow-service] AI gateway:       ${AI_GATEWAY_URL}`)
  console.log(`[skill-flow-service] Skills dir:       ${SKILLS_DIR}`)

  // Pre-warm all MCP SSE connections in the background.
  // docker-compose healthchecks ensure the servers are up, but the SSE
  // handshake is separate from the /health probe — do it eagerly to avoid
  // the first real skill invocation paying the connection penalty.
  prewarmMcpConnections().catch(err => {
    console.warn("[skill-flow-service] Pre-warm completed with errors:", err)
  })
})
