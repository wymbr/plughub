/**
 * test-conference-agent.mjs
 * Agente externo de teste para modo conferência (spec 4.6k + conferencia-e-historico.md).
 *
 * Diferença do test-external-agent.mjs:
 *   - Detecta is_conference no context_package
 *   - Age como especialista convidado, não como atendente principal
 *   - Passa conference_id no wait_for_message (offset 0: lê desde o início do stream)
 *   - Encerra com agent_done sem fechar a sessão do cliente (humano continua)
 *
 * Fluxo:
 *   agent_login → agent_ready → wait_for_assignment
 *   → [context_package com is_conference=true]
 *   → send_message (saudação do especialista)
 *   → wait_for_message (aguarda pergunta do cliente)
 *   → send_message (resposta especializada)
 *   → agent_done (encerra participação, sessão permanece aberta)
 *   → agent_ready → ... (loop)
 *
 * Pré-requisitos — pool e agent-type do especialista:
 *   curl -s -X POST http://localhost:3300/v1/pools \
 *     -H "Content-Type: application/json" -H "x-tenant-id: default" \
 *     -d '{
 *       "pool_id":"especialista_ia","display_name":"Especialista IA",
 *       "channel_types":["chat"],"sla_target_ms":120000
 *     }'
 *
 *   curl -s -X POST http://localhost:3300/v1/agent-types \
 *     -H "Content-Type: application/json" -H "x-tenant-id: default" \
 *     -d '{
 *       "agent_type_id":"agente_especialista_v1","framework":"external-mcp",
 *       "execution_model":"stateless","role":"executor",
 *       "max_concurrent_sessions":3,"pools":["especialista_ia"],
 *       "permissions":[]
 *     }'
 *
 * Uso:
 *   node test-conference-agent.mjs
 *
 * Para disparar uma conferência (com sessão de cliente já aberta):
 *   node test-trigger-conference.mjs --session-id {SESSION_ID}
 */

import { Client }             from "@modelcontextprotocol/sdk/client/index.js"
import { SSEClientTransport } from "@modelcontextprotocol/sdk/client/sse.js"

// ── Configuração ─────────────────────────────────────────────────────────────

const MCP_URL      = process.env["MCP_URL"]       ?? "http://localhost:3100"
const AGENT_TYPE   = process.env["AGENT_TYPE"]    ?? "agente_especialista_v1"
const INSTANCE_ID  = process.env["INSTANCE_ID"]   ?? "especialista-001"
const TENANT_ID    = process.env["TENANT_ID"]     ?? "default"
const POOL_ID      = process.env["POOL_ID"]       ?? "especialista_ia"
const WAIT_TIMEOUT = parseInt(process.env["WAIT_TIMEOUT"] ?? "120", 10)
const MAX_CYCLES   = parseInt(process.env["MAX_CYCLES"]   ?? "0",   10)  // 0 = infinito

// ── Helpers de log ────────────────────────────────────────────────────────────

const c = {
  reset:   "\x1b[0m",
  bold:    "\x1b[1m",
  cyan:    "\x1b[36m",
  green:   "\x1b[32m",
  yellow:  "\x1b[33m",
  red:     "\x1b[31m",
  magenta: "\x1b[35m",
  blue:    "\x1b[34m",
}

const ts = () => new Date().toISOString().slice(11, 23)

function log(icon, color, label, data) {
  const prefix = `${c.bold}[${ts()}]${c.reset} ${color}${icon} ${label}${c.reset}`
  if (data !== undefined) {
    console.log(prefix, typeof data === "object" ? JSON.stringify(data, null, 2) : data)
  } else {
    console.log(prefix)
  }
}

const step  = (label, data) => log("→", c.cyan,    label, data)
const ok    = (label, data) => log("✓", c.green,   label, data)
const warn  = (label, data) => log("⚠", c.yellow,  label, data)
const err   = (label, data) => log("✗", c.red,     label, data)
const info  = (label, data) => log("·", c.blue,    label, data)
const conf  = (label, data) => log("⬡", c.magenta, label, data)
const wait  = (label)       => log("…", c.yellow,  label)

// ── Chamada MCP ───────────────────────────────────────────────────────────────
//
// clientTimeoutMs controla o timeout do lado do SDK MCP (cancelamento da request).
// Para tools bloqueantes (wait_for_assignment, wait_for_message), deve ser maior
// que o timeout_s da tool + margem. O default do SDK é 60s — insuficiente.

async function call(client, tool, params, clientTimeoutMs = 30_000) {
  const result = await client.callTool(
    { name: tool, arguments: params },
    undefined,                          // resultSchema — não usado
    { timeout: clientTimeoutMs },       // timeout do SDK, não da tool
  )
  const raw = result?.content?.[0]?.text ?? "{}"
  let parsed
  try { parsed = JSON.parse(raw) } catch { parsed = { raw } }
  // Trata tanto isError (MCP protocol errors) quanto { error } (tool errors)
  if (result?.isError) {
    const e = new Error(`${tool} falhou: ${parsed.message ?? raw}`)
    e.data  = parsed
    throw e
  }
  if (parsed.error) {
    const e = new Error(parsed.message ?? parsed.error)
    e.data  = parsed
    throw e
  }
  return parsed
}

// ── Ciclo de conferência ──────────────────────────────────────────────────────

async function runCycle(client, session_token, cycleNum) {
  console.log()
  conf(`═══ Ciclo #${cycleNum} — aguardando assignment ═══`)

  // ── wait_for_assignment ───────────────────────────────────────────────────
  // clientTimeoutMs = WAIT_TIMEOUT + 30s de margem para o SDK não cancelar
  // antes da tool responder. O SDK tem default de 60s — insuficiente para
  // ciclos longos.
  wait(`wait_for_assignment (timeout: ${WAIT_TIMEOUT}s)...`)
  const assignment  = await call(client, "wait_for_assignment", {
    session_token,
    timeout_s: WAIT_TIMEOUT,
  }, (WAIT_TIMEOUT + 30) * 1000)
  ok("Assignment recebido", assignment)

  // wait_for_assignment retorna { context_package: {...} }
  // — os campos estão dentro de context_package, não no nível raiz.
  const pkg = assignment.context_package ?? assignment

  const {
    session_id,
    contact_id,
    conference_id,
    participant_id,
    channel_identity,
    is_conference,
  } = pkg

  if (!is_conference || !conference_id) {
    warn("Assignment sem conference_id — este agente é apenas para conferência.")
    warn("Use test-external-agent.mjs para atendimentos primários.")
    // Encerrar sem agent_done — deixar o routing engine recuperar por timeout
    return null
  }

  const identity = channel_identity?.text ?? "Especialista"
  conf(`Conferência detectada: conference_id=${conference_id}`, {
    participant_id,
    identity,
    session_id,
  })

  // ── agent_busy ────────────────────────────────────────────────────────────
  // runtime.ts espera `conversation_id`, não `session_id`
  step("agent_busy")
  await call(client, "agent_busy", { session_token, conversation_id: session_id })
  ok("agent_busy confirmado")

  let customer_message = null

  try {
    // ── send_message (saudação do especialista) ───────────────────────────
    step("send_message → saudação do especialista")
    await call(client, "send_message", {
      session_token,
      session_id,
      contact_id,
      text: `Olá! Sou o ${identity}. Em que posso ajudá-lo?`,
      channel: pkg.channel ?? "chat",
    })
    ok("Saudação enviada")

    // ── wait_for_message ──────────────────────────────────────────────────
    // conference_id passado para que o consumer group use offset 0:
    // lê desde o início do stream — garante que mensagens enviadas pelo cliente
    // entre o agent_join_conference e este wait_for_message não sejam perdidas.
    // clientTimeoutMs = 120s + 30s margem para não cancelar antes do XREADGROUP.
    wait("wait_for_message (aguardando pergunta do cliente, timeout: 120s)...")
    const reply = await call(client, "wait_for_message", {
      session_token,
      session_id,
      timeout_s:    120,
      conference_id,   // ← offset 0 no consumer group
    }, 150_000)
    customer_message = reply.message
    ok("Mensagem do cliente recebida", { message: customer_message })

    // ── send_message (resposta especializada) ─────────────────────────────
    const msgText = typeof customer_message === "string"
      ? customer_message
      : customer_message?.text ?? JSON.stringify(customer_message)

    step("send_message → resposta especializada")
    await call(client, "send_message", {
      session_token,
      session_id,
      contact_id,
      text: `[${identity}] Entendido: "${msgText}". Vou verificar isso para você.`,
      channel: pkg.channel ?? "chat",
    })
    ok("Resposta enviada")

  } catch (e) {
    if (e.data?.error === "client_disconnected") {
      info("Cliente desconectou durante a conferência — encerrando participação.")
    } else if (e.data?.error === "timeout") {
      warn("Timeout aguardando cliente — encerrando participação.")
    } else {
      err("Erro no ciclo de conferência", e.data ?? e.message)
    }
  }

  // ── agent_done ────────────────────────────────────────────────────────────
  // Em modo conferência, agent_done encerra a PARTICIPAÇÃO do especialista,
  // não a sessão do cliente. O humano e o cliente continuam conectados.
  // conference_id sinaliza ao runtime que NÃO deve publicar session.closed —
  // o WebSocket do cliente permanece aberto para o atendente principal continuar.
  step("agent_done → encerrando participação na conferência")
  try {
    await call(client, "agent_done", {
      session_token,
      conversation_id: session_id,   // runtime.ts espera conversation_id
      conference_id,                  // omite session.closed — não encerra a sessão do cliente
      outcome: customer_message ? "resolved" : "unresolved",
      ...(customer_message ? {} : { handoff_reason: "Timeout aguardando cliente." }),
      issue_status: [{
        issue_id:    "conference_specialist_test",
        description: `Participação de conferência como ${identity}.`,
        status:      customer_message ? "resolved" : "unresolved",
      }],
    })
    ok("agent_done — participação encerrada. Sessão do cliente permanece aberta.")
  } catch (e) {
    err("agent_done falhou", e.data ?? e.message)
  }

  return { session_id, conference_id, customer_message }
}

// ── Main ──────────────────────────────────────────────────────────────────────

async function main() {
  console.log()
  conf(`PlugHub Conference Agent — ${AGENT_TYPE} / ${INSTANCE_ID}`)
  conf(`MCP: ${MCP_URL}  Pool: ${POOL_ID}  MaxCycles: ${MAX_CYCLES || "∞"}`)
  console.log()

  // ── Conectar ao MCP server ────────────────────────────────────────────────
  const transport = new SSEClientTransport(new URL(`${MCP_URL}/sse`))
  const client    = new Client({ name: "test-conference-agent", version: "1.0.0" })
  await client.connect(transport)
  ok("Conectado ao MCP server")

  // ── agent_login ───────────────────────────────────────────────────────────
  step("agent_login")
  const loginResult  = await call(client, "agent_login", {
    agent_type_id: AGENT_TYPE,
    instance_id:   INSTANCE_ID,
    tenant_id:     TENANT_ID,
  })
  const session_token = loginResult.session_token
  ok("agent_login", { instance_id: loginResult.instance_id })

  let cycle = 0
  let lastFailedSessionId = null

  // ── Loop de ciclos ────────────────────────────────────────────────────────
  while (MAX_CYCLES === 0 || cycle < MAX_CYCLES) {
    cycle++

    // Recuperação: se o ciclo anterior falhou com sessão aberta, encerrar antes
    if (lastFailedSessionId) {
      warn(`Recuperando sessão interrompida: ${lastFailedSessionId}`)
      try {
        await call(client, "agent_done", {
          session_token,
          session_id:     lastFailedSessionId,
          outcome:        "unresolved",
          handoff_reason: "Recuperação de falha no ciclo anterior.",
          issue_status:   [{
            issue_id:    "recovery",
            description: "Encerramento de recuperação.",
            status:      "unresolved",
          }],
        })
        ok("Sessão interrompida encerrada")
      } catch (e) {
        warn("Falha na recuperação (ignorando)", e.data?.error ?? e.message)
      }
      lastFailedSessionId = null
    }

    // ── agent_ready ───────────────────────────────────────────────────────
    step("agent_ready")
    try {
      await call(client, "agent_ready", {
        session_token,
        pools: [POOL_ID],
      })
      ok("agent_ready — aguardando conferência")
    } catch (e) {
      err("agent_ready falhou", e.data ?? e.message)
      await new Promise(r => setTimeout(r, 2000))
      continue
    }

    // ── Executar ciclo ─────────────────────────────────────────────────────
    try {
      await runCycle(client, session_token, cycle)
    } catch (e) {
      err(`Ciclo #${cycle} falhou`, e.data ?? e.message)
      if (e.sessionId) {
        lastFailedSessionId = e.sessionId
      }
      await new Promise(r => setTimeout(r, 1000))
    }
  }

  info(`${MAX_CYCLES} ciclos concluídos. Encerrando.`)
  await client.close()
}

main().catch(e => {
  err("Erro fatal", e.message)
  process.exit(1)
})
