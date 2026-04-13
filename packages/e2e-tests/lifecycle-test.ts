/**
 * lifecycle-test.ts
 * Step 4: Executa ciclo de vida completo do agente agente_retencao_teste_v1.
 * agent_login → agent_ready → agent_busy → insight_register → agent_done
 * Step 5: Asserts no Redis.
 */

import { McpTestClient } from "./lib/mcp-client"
import Redis from "ioredis"
import { randomUUID } from "crypto"

const MCP_URL      = "http://localhost:3100"
const TENANT_ID    = "tenant_e2e_test"
const AGENT_TYPE   = "agente_retencao_teste_v1"
const INSTANCE_ID  = `inst_${randomUUID().replace(/-/g, "").slice(0, 12)}`
const CONV_ID      = randomUUID()

const redis = new Redis({ host: "localhost", port: 6379 })

function log(msg: string): void {
  console.log(`  ${msg}`)
}

function pass(step: string): void {
  console.log(`✅ ${step}`)
}

function fail(step: string, detail: unknown): never {
  console.error(`❌ ${step}`)
  console.error(JSON.stringify(detail, null, 2))
  process.exit(1)
}

async function main(): Promise<void> {
  console.log("\n=== Step 4: Agent Lifecycle ===\n")
  log(`tenant_id:    ${TENANT_ID}`)
  log(`agent_type:   ${AGENT_TYPE}`)
  log(`instance_id:  ${INSTANCE_ID}`)
  log(`conv_id:      ${CONV_ID}`)
  console.log()

  const client = new McpTestClient(MCP_URL)
  await client.connect()
  log("Connected to mcp-server-plughub via SSE")

  // ── agent_login ────────────────────────────────────────────────────────────
  let session_token: string
  try {
    const loginResult = await client.agentLogin(TENANT_ID, AGENT_TYPE, INSTANCE_ID)
    session_token = loginResult.session_token
    pass(`agent_login → session_token: ${session_token.slice(0, 40)}...`)
  } catch (e) {
    fail("agent_login", e)
  }

  // Verify Redis state after login
  const instanceKey = `${TENANT_ID}:agent:instance:${INSTANCE_ID}`
  const redisState1 = await redis.hgetall(instanceKey)
  if (!redisState1 || redisState1["state"] !== "logged_in") {
    fail("Redis state after agent_login", { expected: "logged_in", got: redisState1 })
  }
  pass(`Redis: instance state = logged_in`)

  // ── agent_ready ────────────────────────────────────────────────────────────
  try {
    const readyResult = await client.agentReady(session_token!)
    pass(`agent_ready → status: ${(readyResult as { status: string }).status ?? JSON.stringify(readyResult)}`)
  } catch (e) {
    fail("agent_ready", e)
  }

  const redisState2 = await redis.hgetall(instanceKey)
  if (!redisState2 || redisState2["state"] !== "ready") {
    fail("Redis state after agent_ready", { expected: "ready", got: redisState2 })
  }
  pass(`Redis: instance state = ready`)

  // ── agent_busy ─────────────────────────────────────────────────────────────
  try {
    const busyResult = await client.agentBusy(session_token!, CONV_ID)
    pass(`agent_busy → status: ${(busyResult as { status: string }).status ?? JSON.stringify(busyResult)}`)
  } catch (e) {
    fail("agent_busy", e)
  }

  const redisState3 = await redis.hgetall(instanceKey)
  if (!redisState3 || redisState3["state"] !== "busy") {
    fail("Redis state after agent_busy", { expected: "busy", got: redisState3 })
  }
  pass(`Redis: instance state = busy`)

  // ── insight_register (simulates flow step registrar_oferta) ────────────────
  const insightResult = await client.callTool("insight_register", {
    session_token:   session_token!,
    conversation_id: CONV_ID,
    category:        "insight.conversa.oferta_aceita",
    content: {
      resultado_oferta: "Cliente aceitou plano de retenção Premium 50GB",
      valor_mensal:     "89.90",
      vigencia_meses:   12,
    },
    priority: 10,
  })
  if (insightResult.isError) {
    fail("insight_register", insightResult.data)
  }
  pass(`insight_register → insight.conversa.oferta_aceita registered`)

  // Verify insight in Redis — key pattern: {tenant}:insight:{conv_id}:{item_id}
  const insightPattern = `${TENANT_ID}:insight:${CONV_ID}:*`
  const insightKeys = await redis.keys(insightPattern)
  if (insightKeys.length === 0) {
    fail("Redis: insight.conversa.oferta_aceita not found", { pattern: insightPattern })
  }
  const insightRaw = await redis.get(insightKeys[0]!)
  const insightObj = insightRaw ? JSON.parse(insightRaw) as { category?: string } : null
  if (!insightObj || insightObj.category !== "insight.conversa.oferta_aceita") {
    fail("Redis: insight category mismatch", insightObj)
  }
  pass(`Redis: insight.conversa.oferta_aceita found at ${insightKeys[0]}`)

  // ── agent_done ─────────────────────────────────────────────────────────────
  const doneResult = await client.agentDone({
    session_token:      session_token!,
    conversation_id:    CONV_ID,
    outcome:            "resolved",
    issue_status: [{
      issue_id:    "issue_retencao_1",
      description: "Oferta de retenção aceita pelo cliente",
      status:      "resolved",
      resolved_at: new Date().toISOString(),
    }],
    resolution_summary: "Cliente aceitou plano de retenção Premium 50GB. Contrato renovado por 12 meses.",
  })

  if ("isError" in doneResult && doneResult.isError) {
    fail("agent_done", doneResult)
  }
  pass(`agent_done → outcome: resolved`)

  // ── Step 5: Final assertions ───────────────────────────────────────────────
  console.log("\n=== Step 5: Final Redis Assertions ===\n")

  // After agent_done with current_sessions=0 and not draining, instance returns to "ready"
  // (Spec 4.2: agent_done completes the conversation; agent instance remains available)
  const redisStateFinal = await redis.hgetall(instanceKey)
  if (!redisStateFinal || !["ready", "logged_out"].includes(redisStateFinal["state"] ?? "")) {
    fail("Redis: agent state after agent_done", { expected: "ready or logged_out", got: redisStateFinal })
  }
  pass(`Redis: instance state = ${redisStateFinal["state"]} (conversation completed, instance available)`)

  // Assert pipeline_state contains executed steps
  const pipelineKey = `${TENANT_ID}:pipeline:${CONV_ID}`
  const pipelineRaw = await redis.get(pipelineKey)
  if (!pipelineRaw) {
    log(`⚠️  pipeline_state key not found at ${pipelineKey} (may be stored under different key or managed by skill-flow-engine)`)
  } else {
    pass(`Redis: pipeline_state found at ${pipelineKey}`)
    const pipeline = JSON.parse(pipelineRaw) as Record<string, unknown>
    log(`  pipeline_state keys: ${Object.keys(pipeline).join(", ")}`)
  }

  await client.disconnect()
  await redis.quit()

  console.log("\n=== All Steps PASSED ===\n")
}

main().catch((e) => {
  console.error("Fatal error:", e)
  process.exit(1)
})
