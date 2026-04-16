/**
 * test-external-agent.mjs
 * Smoke test para o fluxo completo de agente externo via MCP (spec 4.6k).
 *
 * Fluxo testado:
 *   agent_login → agent_ready → wait_for_assignment → agent_busy
 *   → send_message → wait_for_message → agent_done
 *
 * Pré-requisitos (rodar uma vez antes):
 *   curl -s -X POST http://localhost:3200/v1/pools \
 *     -H "Content-Type: application/json" -H "x-tenant-id: default" \
 *     -d '{"pool_id":"externo_teste","display_name":"Externo Teste","capacity":1}'
 *
 *   curl -s -X POST http://localhost:3200/v1/agent-types \
 *     -H "Content-Type: application/json" -H "x-tenant-id: default" \
 *     -d '{"agent_type_id":"agente_externo_v1","framework":"external-mcp",
 *           "execution_model":"stateless","role":"executor",
 *           "max_concurrent_sessions":1,"pools":["externo_teste"],
 *           "permissions":["mcp-server-crm:customer_get"]}'
 *
 *   redis-cli HSET default:agent:instance:externo-001 \
 *     agent_type_id agente_externo_v1 status ready \
 *     current_sessions 0 max_concurrent_sessions 1 \
 *     pools '["externo_teste"]'
 *   redis-cli SADD default:pool:externo_teste:instances externo-001
 *
 * Uso:
 *   node test-external-agent.mjs
 *
 * Enquanto o script aguarda (wait_for_assignment), dispare um contato de teste:
 *   wscat -c "ws://localhost:3000/ws?contact_id=cliente-teste&channel=chat"
 *   > {"type":"message.text","text":"olá"}
 *
 * O script responde automaticamente e encerra após 1 troca de mensagem.
 */

import { Client }           from "@modelcontextprotocol/sdk/client/index.js"
import { SSEClientTransport } from "@modelcontextprotocol/sdk/client/sse.js"

// ── Configuração ────────────────────────────────────────────────────────────

const MCP_URL      = process.env["MCP_URL"]       ?? "http://localhost:3100"
const AGENT_TYPE   = process.env["AGENT_TYPE"]    ?? "agente_externo_v1"
const INSTANCE_ID  = process.env["INSTANCE_ID"]   ?? "externo-001"
const TENANT_ID    = process.env["TENANT_ID"]     ?? "default"
const WAIT_TIMEOUT = parseInt(process.env["WAIT_TIMEOUT"] ?? "120", 10)

// ── Helpers ──────────────────────────────────────────────────────────────────

const c = {
  reset:  "\x1b[0m",
  green:  "\x1b[32m",
  yellow: "\x1b[33m",
  cyan:   "\x1b[36m",
  red:    "\x1b[31m",
  bold:   "\x1b[1m",
  dim:    "\x1b[2m",
}

function log(prefix, msg, data) {
  const ts = new Date().toISOString().slice(11, 23)
  const line = data !== undefined
    ? `${c.dim}${ts}${c.reset} ${prefix} ${msg}\n         ${c.dim}${JSON.stringify(data)}${c.reset}`
    : `${c.dim}${ts}${c.reset} ${prefix} ${msg}`
  console.log(line)
}

function step(name)   { log(`${c.cyan}▶${c.reset}`, `${c.bold}${name}${c.reset}`) }
function ok(name, d)  { log(`${c.green}✓${c.reset}`, name, d) }
function err(name, d) { log(`${c.red}✗${c.reset}`,   name, d) }
function wait(msg)    { log(`${c.yellow}⏳${c.reset}`, msg) }

/** Chama uma MCP tool e retorna o resultado parseado. Lança se isError=true. */
async function call(client, tool, args) {
  const res = await client.callTool({ name: tool, arguments: args })
  const raw = res?.content?.[0]?.text ?? "{}"
  let parsed
  try { parsed = JSON.parse(raw) } catch { parsed = { raw } }
  if (res?.isError) {
    throw Object.assign(new Error(`${tool} falhou: ${parsed.message ?? raw}`), { data: parsed })
  }
  return parsed
}

// ── Main ─────────────────────────────────────────────────────────────────────

async function main() {
  console.log()
  console.log(`${c.bold}PlugHub — Teste de Agente Externo via MCP (spec 4.6k)${c.reset}`)
  console.log(`${c.dim}MCP Server: ${MCP_URL}  |  Agent: ${AGENT_TYPE}  |  Instance: ${INSTANCE_ID}${c.reset}`)
  console.log()

  // ── Conectar ao mcp-server-plughub via SSE ──────────────────────────────
  step("Conectando ao mcp-server-plughub...")
  const client    = new Client({ name: "test-external-agent", version: "1.0.0" }, { capabilities: {} })
  const transport = new SSEClientTransport(new URL(`${MCP_URL}/sse`))
  await client.connect(transport)
  ok("Conectado", { endpoint: `${MCP_URL}/sse` })

  // ── agent_login ──────────────────────────────────────────────────────────
  step("agent_login")
  const login = await call(client, "agent_login", {
    agent_type_id: AGENT_TYPE,
    instance_id:   INSTANCE_ID,
    tenant_id:     TENANT_ID,
  })
  ok("Logado", {
    instance_id:      login.instance_id,
    token_expires_at: login.token_expires_at,
  })

  const session_token = login.session_token

  // ── agent_ready ──────────────────────────────────────────────────────────
  step("agent_ready")
  const ready = await call(client, "agent_ready", { session_token })
  ok("Pronto", { pools: ready.pools })

  // ── wait_for_assignment ──────────────────────────────────────────────────
  wait(`Aguardando contato em '${AGENT_TYPE}' (timeout: ${WAIT_TIMEOUT}s)...`)
  console.log(`${c.dim}         Dica: wscat -c "ws://localhost:3000/ws?contact_id=cliente-teste&channel=chat"${c.reset}`)
  console.log(`${c.dim}         Depois envie: {"type":"message.text","text":"olá"}${c.reset}`)
  console.log()

  let context_package
  try {
    const assignment = await call(client, "wait_for_assignment", {
      session_token,
      timeout_s: WAIT_TIMEOUT,
    })
    context_package = assignment.context_package
    ok("Contato atribuído", {
      session_id:    context_package.session_id,
      contact_id:    context_package.contact_id,
      agent_type_id: context_package.agent_type_id,
      pool_id:       context_package.pool_id,
    })
  } catch (e) {
    err("wait_for_assignment", e.data ?? e.message)
    process.exit(1)
  }

  const session_id  = context_package.session_id
  const contact_id  = context_package.contact_id

  // ── agent_busy ───────────────────────────────────────────────────────────
  step("agent_busy")
  const busy = await call(client, "agent_busy", {
    session_token,
    conversation_id: session_id,
  })
  ok("Em atendimento", { current_sessions: busy.current_sessions })

  // ── send_message (saudação) ──────────────────────────────────────────────
  step("send_message → saudação ao cliente")
  const sent = await call(client, "send_message", {
    session_token,
    session_id,
    contact_id,
    text: "Olá! Sou um agente externo integrado via MCP. Como posso ajudar?",
    channel: context_package.channel ?? "chat",
  })
  ok("Mensagem enviada", { message_id: sent.message_id })

  // ── wait_for_message (resposta do cliente) ───────────────────────────────
  wait("Aguardando resposta do cliente (timeout: 60s)...")

  let customer_message
  try {
    const reply = await call(client, "wait_for_message", {
      session_token,
      session_id,
      timeout_s: 60,
    })
    customer_message = reply.message
    ok("Mensagem do cliente recebida", { message: customer_message })
  } catch (e) {
    err("wait_for_message timeout", e.data ?? e.message)
    console.log(`${c.yellow}         Encerrando sem resposta do cliente...${c.reset}`)
  }

  // ── send_message (resposta final) ────────────────────────────────────────
  if (customer_message) {
    step("send_message → resposta final")
    await call(client, "send_message", {
      session_token,
      session_id,
      contact_id,
      text: `Recebi sua mensagem: "${typeof customer_message === "string" ? customer_message : customer_message?.text ?? JSON.stringify(customer_message)}". Encerrando atendimento. Até logo!`,
      channel: context_package.channel ?? "chat",
    })
    ok("Resposta final enviada")
  }

  // ── agent_done ───────────────────────────────────────────────────────────
  step("agent_done")
  const done = await call(client, "agent_done", {
    session_token,
    conversation_id:    session_id,
    outcome:            "resolved",
    issue_status:       [{ issue: "external_mcp_test", status: "resolved" }],
    resolution_summary: "Teste de agente externo via MCP — fluxo completo validado.",
  })
  ok("Atendimento encerrado", {
    outcome:      done.outcome,
    acknowledged: done.acknowledged,
    completed_at: done.completed_at,
  })

  console.log()
  console.log(`${c.green}${c.bold}✅ Teste concluído — ciclo externo-mcp validado com sucesso!${c.reset}`)
  console.log()

  await client.close()
  process.exit(0)
}

main().catch(e => {
  console.error(`\n${c.red}${c.bold}Erro fatal:${c.reset}`, e.message ?? e)
  if (e.data) console.error(`${c.dim}${JSON.stringify(e.data)}${c.reset}`)
  process.exit(1)
})
