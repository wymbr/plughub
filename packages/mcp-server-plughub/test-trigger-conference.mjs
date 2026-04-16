/**
 * test-trigger-conference.mjs
 * Dispara uma conferência chamando agent_join_conference via MCP.
 *
 * Substitui o @mention da Agent Assist UI para fins de teste.
 * O agente test-conference-agent.mjs deve estar rodando antes de executar este script.
 *
 * Uso:
 *   node test-trigger-conference.mjs --session-id {SESSION_ID}
 *
 * Ou via variáveis de ambiente:
 *   SESSION_ID=xxx POOL_ID=especialista_ia node test-trigger-conference.mjs
 *
 * Fluxo completo de teste:
 *   Terminal 1: node test-conference-agent.mjs
 *   Terminal 2: wscat -c "ws://localhost:8010/ws/chat/externo_teste" \
 *                 -H "x-customer-id: cliente-teste" -H "x-tenant-id: default"
 *               > {"type":"message.text","text":"olá"}   ← abre sessão com agente primário
 *   Terminal 3: node test-trigger-conference.mjs --session-id {SESSION_ID_DO_TERMINAL_2}
 *               ← conferência disparada; test-conference-agent entra na sessão
 *   Terminal 2: > {"type":"message.text","text":"preciso de ajuda com autenticação"}
 *               ← mensagem chega ao agente humano E ao especialista IA
 */

import { Client }             from "@modelcontextprotocol/sdk/client/index.js"
import { SSEClientTransport } from "@modelcontextprotocol/sdk/client/sse.js"

// ── Configuração ─────────────────────────────────────────────────────────────

const MCP_URL      = process.env["MCP_URL"]             ?? "http://localhost:3100"
const AGENT_TYPE   = process.env["AGENT_TYPE_ID"]       ?? "agente_especialista_v1"
const POOL_ID      = process.env["POOL_ID"]             ?? "especialista_ia"
const TENANT_ID    = process.env["TENANT_ID"]           ?? "default"
const IDENTITY_TXT = process.env["IDENTITY"]            ?? "Especialista"
// Agent type used only to obtain a session_token for agent_join_conference.
// Must exist in the agent registry — defaults to the seeded retencao_humano type.
const TRIGGER_AGENT_TYPE = process.env["TRIGGER_AGENT_TYPE"] ?? "agente_retencao_humano_v1"

// Aceita --session-id como argumento CLI ou SESSION_ID env
let SESSION_ID = process.env["SESSION_ID"] ?? ""
const args = process.argv.slice(2)
for (let i = 0; i < args.length; i++) {
  if (args[i] === "--session-id" && args[i + 1]) SESSION_ID = args[i + 1]
}

// Strip curly braces if user accidentally copied the placeholder literally: {UUID} → UUID
SESSION_ID = SESSION_ID.replace(/^\{|\}$/g, "").trim()

if (!SESSION_ID) {
  console.error("Erro: session_id obrigatório.")
  console.error("  node test-trigger-conference.mjs --session-id SESSION_ID")
  console.error("  SESSION_ID=xxx node test-trigger-conference.mjs")
  process.exit(1)
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const c = {
  reset:   "\x1b[0m",
  bold:    "\x1b[1m",
  green:   "\x1b[32m",
  cyan:    "\x1b[36m",
  red:     "\x1b[31m",
  magenta: "\x1b[35m",
}

const ts  = () => new Date().toISOString().slice(11, 23)
const log = (icon, color, label, data) => {
  const prefix = `${c.bold}[${ts()}]${c.reset} ${color}${icon} ${label}${c.reset}`
  if (data !== undefined) console.log(prefix, typeof data === "object" ? JSON.stringify(data, null, 2) : data)
  else console.log(prefix)
}

const step = (l, d) => log("→", c.cyan,    l, d)
const ok   = (l, d) => log("✓", c.green,   l, d)
const err  = (l, d) => log("✗", c.red,     l, d)
const conf = (l, d) => log("⬡", c.magenta, l, d)

async function call(client, tool, params) {
  const result = await client.callTool({ name: tool, arguments: params })
  const raw    = result.content?.[0]?.text ?? "{}"
  const data   = JSON.parse(raw)
  if (data.error) {
    const e  = new Error(data.message ?? data.error)
    e.data   = data
    throw e
  }
  return data
}

// ── Main ──────────────────────────────────────────────────────────────────────

async function main() {
  console.log()
  conf("PlugHub Conference Trigger")
  conf(`session_id: ${SESSION_ID}`)
  conf(`agent_type: ${AGENT_TYPE}  pool: ${POOL_ID}  identity: "${IDENTITY_TXT}"`)
  console.log()

  // ── Conectar ao MCP como agente humano (precisa de session_token) ─────────
  // Usamos um agent_login temporário de tipo humano para obter o token que
  // autoriza o agent_join_conference. Em produção, o Agent Assist UI já tem
  // o token do agente humano logado.
  const transport = new SSEClientTransport(new URL(`${MCP_URL}/sse`))
  const client    = new Client({ name: "conference-trigger", version: "1.0.0" })
  await client.connect(transport)
  ok("Conectado ao MCP server")

  step(`agent_login como ${TRIGGER_AGENT_TYPE} (humano temporário para autorizar agent_join_conference)`)
  const loginResult = await call(client, "agent_login", {
    agent_type_id: TRIGGER_AGENT_TYPE,
    instance_id:   `trigger-${Date.now()}`,
    tenant_id:     TENANT_ID,
  })
  const session_token = loginResult.session_token
  ok("session_token obtido")

  // ── agent_join_conference ─────────────────────────────────────────────────
  step("agent_join_conference")
  const result = await call(client, "agent_join_conference", {
    session_token,
    session_id:       SESSION_ID,
    agent_type_id:    AGENT_TYPE,
    pool_id:          POOL_ID,
    interaction_model: "conference",
    channel_identity: {
      text:          IDENTITY_TXT,
      voice_profile: "assistant_pt_br",
    },
  })

  ok("Conferência disparada!", result)
  console.log()
  conf("Aguarde: o test-conference-agent.mjs deve receber o assignment em instantes.")
  conf(`conference_id: ${result.conference_id}`)
  conf(`participant_id: ${result.participant_id}`)
  conf(`status: ${result.status}`)
  console.log()

  await client.close()
}

main().catch(e => {
  err("Erro fatal", e.data ?? e.message)
  process.exit(1)
})
