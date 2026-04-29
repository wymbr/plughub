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
import type { SkillFlow } from "@plughub/schemas"

// ── Config ────────────────────────────────────────────────────────────────────

const PORT           = parseInt(process.env["PORT"]           ?? "3400", 10)
const REDIS_URL      = process.env["REDIS_URL"]               ?? "redis://localhost:6379"
const MCP_SERVER_URL = process.env["MCP_SERVER_URL"]          ?? "http://localhost:3100"
const MCP_AUTH_URL   = process.env["MCP_AUTH_URL"]            ?? "http://localhost:3150"
const AI_GATEWAY_URL = process.env["AI_GATEWAY_URL"]          ?? "http://localhost:3200"

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

// ── Engine ────────────────────────────────────────────────────────────────────

const engine = new SkillFlowEngine({ redis, mcpCall, aiGatewayCall })

// ── Express app ───────────────────────────────────────────────────────────────

const app = express()
app.use(express.json())

// GET /health
app.get("/health", (_req: Request, res: Response) => {
  res.json({ status: "ok" })
})

// POST /execute
// Body: { tenant_id, session_id, customer_id, skill_id, flow, session_context, instance_id? }
// Returns: { outcome, pipeline_state } | { error, active_job_id }
app.post("/execute", async (req: Request, res: Response) => {
  const {
    tenant_id,
    session_id,
    customer_id,
    skill_id,
    flow,
    session_context,
    instance_id,
  } = req.body as {
    tenant_id:       string
    session_id:      string
    customer_id:     string
    skill_id:        string
    flow:            SkillFlow
    session_context: Record<string, unknown>
    /** Routing Engine instance_id — stored in execution lock for crash detection. */
    instance_id?:    string
  }

  if (!tenant_id || !session_id || !customer_id || !skill_id || !flow) {
    res.status(400).json({
      error: "BAD_REQUEST",
      message: "tenant_id, session_id, customer_id, skill_id, and flow are required",
    })
    return
  }

  console.log(
    `[skill-flow-service] /execute received: session=${session_id} skill=${skill_id} entry=${(flow as { entry?: string }).entry ?? "?"}`,
  )

  try {
    const result = await engine.run({
      tenantId:       tenant_id,
      sessionId:      session_id,
      customerId:     customer_id,
      skillId:        skill_id,
      flow,
      sessionContext: session_context ?? {},
      instanceId:     instance_id,
    })

    if ("error" in result && result.error === "PRECONDITION_FAILED") {
      res.status(412).json(result)
      return
    }

    res.json(result)
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err)
    console.error("[skill-flow-service] /execute error:", message)
    res.status(500).json({ error: "INTERNAL_ERROR", message })
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
    const result = await engine.run({
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
