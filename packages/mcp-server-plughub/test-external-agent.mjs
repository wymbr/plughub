/**
 * test-external-agent.mjs
 * Smoke test para o fluxo completo de agente externo via MCP (spec 4.6k).
 *
 * Fluxo testado (em loop contínuo até Ctrl+C ou MAX_CYCLES):
 *   agent_login → agent_ready → wait_for_assignment → agent_busy
 *   → send_message → wait_for_message → agent_done → agent_ready → ...
 *
 * Pré-requisitos (rodar uma vez antes):
 *   curl -s -X POST http://localhost:3300/v1/pools \
 *     -H "Content-Type: application/json" -H "x-tenant-id: default" \
 *     -d '{
 *       "pool_id":"externo_teste","display_name":"Externo Teste",
 *       "channel_types":["chat"],"sla_target_ms":300000
 *     }'
 *
 *   curl -s -X POST http://localhost:3300/v1/agent-types \
 *     -H "Content-Type: application/json" -H "x-tenant-id: default" \
 *     -d '{
 *       "agent_type_id":"agente_externo_v1","framework":"external-mcp",
 *       "execution_model":"stateless","role":"executor",
 *       "max_concurrent_sessions":1,"pools":["externo_teste"],
 *       "permissions":["mcp-server-crm:customer_get"]
 *     }'
 *
 * Uso:
 *   node test-external-agent.mjs
 *
 * Enquanto o script aguarda (wait_for_assignment), dispare um contato de teste:
 *   wscat -c "ws://localhost:8010/ws/chat/externo_teste" \
 *     -H "x-customer-id: cliente-teste" -H "x-tenant-id: default"
 *   > {"type":"message.text","text":"olá"}
 */

import { Client }             from "@modelcontextprotocol/sdk/client/index.js"
import { SSEClientTransport } from "@modelcontextprotocol/sdk/client/sse.js"

// ── Configuração ─────────────────────────────────────────────────────────────

const MCP_URL      = process.env["MCP_URL"]       ?? "http://localhost:3100"
const AGENT_TYPE   = process.env["AGENT_TYPE"]    ?? "agente_externo_v1"
const INSTANCE_ID  = process.env["INSTANCE_ID"]   ?? "externo-001"
const TENANT_ID    = process.env["TENANT_ID"]     ?? "default"
const WAIT_TIMEOUT = parseInt(process.env["WAIT_TIMEOUT"] ?? "120", 10)
const MAX_CYCLES   = parseInt(process.env["MAX_CYCLES"]   ?? "0",   10)  // 0 = infinito

// ── Helpers ───────────────────────────────────────────────────────────────────

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
function info(msg)    { log(`${c.dim}ℹ${c.reset}`,    msg) }

/**
 * Chama uma MCP tool e retorna o resultado parseado. Lança se isError=true.
 * clientTimeoutMs: timeout do SDK (não da tool). Default 30s.
 * Para tools bloqueantes (wait_for_assignment, wait_for_message), passar
 * um valor maior que o timeout_s da tool + margem de 30s.
 */
async function call(client, tool, args, clientTimeoutMs = 30_000) {
  const res = await client.callTool(
    { name: tool, arguments: args },
    undefined,
    { timeout: clientTimeoutMs },
  )
  const raw = res?.content?.[0]?.text ?? "{}"
  let parsed
  try { parsed = JSON.parse(raw) } catch { parsed = { raw } }
  if (res?.isError) {
    throw Object.assign(new Error(`${tool} falhou: ${parsed.message ?? raw}`), { data: parsed })
  }
  return parsed
}

// ── Um ciclo de atendimento ───────────────────────────────────────────────────

async function runCycle(client, session_token, cycleNum) {
  // ── agent_ready ────────────────────────────────────────────────────────────
  step(`[ciclo ${cycleNum}] agent_ready`)
  await call(client, "agent_ready", { session_token })
  ok("Pronto para receber contatos")

  // ── wait_for_assignment ────────────────────────────────────────────────────
  wait(`Aguardando contato no pool '${AGENT_TYPE}' (timeout: ${WAIT_TIMEOUT}s)...`)
  info(`wscat -c "ws://localhost:8010/ws/chat/externo_teste" -H "x-customer-id: cliente-teste" -H "x-tenant-id: default"`)
  console.log()

  let context_package
  try {
    const assignment = await call(client, "wait_for_assignment", {
      session_token,
      timeout_s: WAIT_TIMEOUT,
    }, (WAIT_TIMEOUT + 30) * 1000)
    context_package = assignment.context_package
    ok("Contato atribuído", {
      session_id:    context_package.session_id,
      contact_id:    context_package.contact_id,
      agent_type_id: context_package.agent_type_id,
      pool_id:       context_package.pool_id,
    })
  } catch (e) {
    if (e.data?.error === "timeout") {
      info("Timeout sem contato — chamando agent_ready novamente...")
      return  // volta ao loop externo que chama runCycle de novo
    }
    err("wait_for_assignment", e.data ?? e.message)
    throw e
  }

  const session_id = context_package.session_id
  const contact_id = context_package.contact_id

  // A partir daqui a instância fica busy. Se qualquer erro ocorrer, o loop
  // principal precisa do session_id para chamar agent_done de recuperação.
  // Enriquecemos o erro com sessionId antes de relançar.
  try {
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
      }, 90_000)
      customer_message = reply.message
      ok("Mensagem do cliente recebida", { message: customer_message })
    } catch (e) {
      if (e.data?.error === "client_disconnected") {
        info("Cliente desconectou — encerrando ciclo.")
      } else {
        err("wait_for_message", e.data ?? e.message)
        info("Encerrando sem resposta do cliente...")
      }
    }

    // ── send_message (resposta final) ────────────────────────────────────────
    if (customer_message) {
      step("send_message → resposta final")
      const msgText = typeof customer_message === "string"
        ? customer_message
        : customer_message?.text ?? JSON.stringify(customer_message)
      await call(client, "send_message", {
        session_token,
        session_id,
        contact_id,
        text: `Recebi sua mensagem: "${msgText}". Encerrando atendimento. Até logo!`,
        channel: context_package.channel ?? "chat",
      })
      ok("Resposta final enviada")
    }

    // ── agent_done ───────────────────────────────────────────────────────────
    step("agent_done")
    const done = await call(client, "agent_done", {
      session_token,
      conversation_id:    session_id,
      outcome:            customer_message ? "resolved" : "unresolved",
      issue_status: [{
        issue_id:    "external_mcp_test",
        description: "Teste de integração do agente externo via MCP (spec 4.6k).",
        status:      customer_message ? "resolved" : "unresolved",
      }],
      ...(customer_message ? {} : { handoff_reason: "Timeout aguardando resposta do cliente." }),
      resolution_summary: "Teste de agente externo via MCP — fluxo concluído.",
    })
    ok("Atendimento encerrado", {
      outcome:      done.outcome,
      acknowledged: done.acknowledged,
      completed_at: done.completed_at,
    })
    console.log()

  } catch (e) {
    // Enriquece o erro com sessionId para que o loop de recuperação saiba qual
    // sessão estava aberta quando o erro ocorreu.
    e.sessionId = session_id
    throw e
  }
}

// ── Main ──────────────────────────────────────────────────────────────────────

async function main() {
  console.log()
  console.log(`${c.bold}PlugHub — Agente Externo MCP (spec 4.6k) — Modo Contínuo${c.reset}`)
  console.log(`${c.dim}MCP: ${MCP_URL}  |  Agente: ${AGENT_TYPE}  |  Instância: ${INSTANCE_ID}${c.reset}`)
  console.log(`${c.dim}MAX_CYCLES=${MAX_CYCLES || "∞"}  WAIT_TIMEOUT=${WAIT_TIMEOUT}s${c.reset}`)
  console.log()

  // ── Conectar ao mcp-server-plughub via SSE ─────────────────────────────────
  step("Conectando ao mcp-server-plughub...")
  const client    = new Client({ name: "test-external-agent", version: "1.0.0" }, { capabilities: {} })
  const transport = new SSEClientTransport(new URL(`${MCP_URL}/sse`))
  await client.connect(transport)
  ok("Conectado", { endpoint: `${MCP_URL}/sse` })

  // ── agent_login (uma vez por sessão) ───────────────────────────────────────
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

  // ── Variável de estado inter-ciclo ─────────────────────────────────────────
  // Rastreia o session_id do ciclo que falhou — para tentar agent_done antes
  // do próximo agent_ready (recuperação de estado 'busy' após erro).
  let lastFailedSessionId = null

  // ── Loop de atendimento ────────────────────────────────────────────────────
  let cycle = 0
  while (true) {
    cycle++
    if (MAX_CYCLES > 0 && cycle > MAX_CYCLES) {
      info(`MAX_CYCLES=${MAX_CYCLES} atingido — encerrando.`)
      break
    }

    // Recuperação: se o ciclo anterior falhou com sessão aberta, tentar agent_done
    // para devolver a instância ao estado 'ready' antes de iniciar o próximo ciclo.
    if (lastFailedSessionId) {
      info(`Recuperando estado: tentando agent_done para sessão ${lastFailedSessionId}...`)
      try {
        await call(client, "agent_done", {
          session_token,
          conversation_id: lastFailedSessionId,
          outcome:         "unresolved",
          issue_status: [{
            issue_id:    "recovery",
            description: "Encerramento de recuperação após erro interno.",
            status:      "unresolved",
          }],
          handoff_reason:     "Erro interno no agente de teste.",
          resolution_summary: "Ciclo encerrado por erro — recuperação automática.",
        })
        info("Estado recuperado com sucesso.")
      } catch (recErr) {
        err("Falha na recuperação de estado", recErr.data ?? recErr.message)
        info("Aguardando 5s antes de tentar novamente...")
        await new Promise(r => setTimeout(r, 5000))
      }
      lastFailedSessionId = null
    }

    try {
      await runCycle(client, session_token, cycle)
    } catch (e) {
      err(`Ciclo ${cycle} encerrado com erro`, e.data ?? e.message)
      // Se o erro carrega session_id, armazenar para recuperação no próximo ciclo
      if (e.sessionId) lastFailedSessionId = e.sessionId
      // Pausa breve antes de tentar novamente
      await new Promise(r => setTimeout(r, 2000))
    }
  }

  console.log()
  console.log(`${c.green}${c.bold}✅ Agente encerrado após ${cycle - 1} ciclo(s).${c.reset}`)
  console.log()

  await client.close()
  process.exit(0)
}

// Ctrl+C gracioso
process.on("SIGINT", () => {
  console.log(`\n${c.yellow}${c.bold}Interrompido pelo usuário (Ctrl+C).${c.reset}\n`)
  process.exit(0)
})

main().catch(e => {
  console.error(`\n${c.red}${c.bold}Erro fatal:${c.reset}`, e.message ?? e)
  if (e.data) console.error(`${c.dim}${JSON.stringify(e.data)}${c.reset}`)
  process.exit(1)
})
