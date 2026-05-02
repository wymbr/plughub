/**
 * mock-agent-ws.ts
 * Servidor mock para testes manuais do Agent Assist UI.
 *
 * Substitui completamente o mcp-server-plughub + Redis + Kafka para que a UI
 * possa ser testada de forma isolada, sem infraestrutura.
 *
 * Uso:
 *   cd packages/mcp-server-plughub
 *   npx ts-node src/mock-agent-ws.ts
 *
 * Em outro terminal:
 *   cd packages/agent-assist-ui
 *   npm run dev
 *
 * Porta: 3100  (mesma do servidor real — sem alteração no vite.config.ts)
 *
 * API de controle (use curl ou Postman):
 *   POST /mock/assign      → envia conversation.assigned ao pool
 *   POST /mock/message     → envia message.text a uma sessão
 *   POST /mock/typing      → envia agent.typing a uma sessão
 *   POST /mock/close       → envia session.closed a uma sessão
 *   POST /mock/menu        → envia menu.render a uma sessão
 *   POST /mock/supervisor  → atualiza supervisor_state em memória (afeta GET /supervisor_state/:id)
 *   GET  /mock/status      → mostra conexões WS e sessões subscritas
 *   GET  /mock/log         → mensagens enviadas PELA UI (outbound do agente)
 *   DELETE /mock/log       → limpa o log de mensagens
 */

import http                             from "http"
import express, { Request, Response }   from "express"
import { WebSocketServer, WebSocket }   from "ws"
import crypto                           from "crypto"

// ── Estado global do mock ─────────────────────────────────────────────────

interface ClientState {
  id:        string               // UUID gerado no connect
  poolId:    string
  ws:        WebSocket
  sessions:  Set<string>          // sessões subscritas nesta conexão
}

const clients    = new Map<string, ClientState>()
const agentLog:  Array<{ ts: string; clientId: string; payload: unknown }> = []

// Sessões ativas por pool — persiste entre reconexões (StrictMode reconnect)
const poolSessions = new Map<string, Set<string>>()

// supervisor_state em memória — indexado por session_id
const supervisorStates = new Map<string, unknown>()

// ── Helpers ───────────────────────────────────────────────────────────────

function sendToSession(sessionId: string, event: unknown): number {
  let sent = 0
  for (const client of clients.values()) {
    if (client.sessions.has(sessionId) && client.ws.readyState === WebSocket.OPEN) {
      client.ws.send(JSON.stringify(event))
      sent++
    }
  }
  return sent
}

function sendToPool(poolId: string, event: unknown): number {
  let sent = 0
  for (const client of clients.values()) {
    if (client.poolId === poolId && client.ws.readyState === WebSocket.OPEN) {
      client.ws.send(JSON.stringify(event))
      sent++
    }
  }
  return sent
}

function defaultSupervisorState(sessionId: string) {
  return {
    session_id:  sessionId,
    turn_count:  3,
    is_stale:    false,
    sentiment: {
      current:    0.2,
      trajectory: [0.1, 0.15, 0.2],
      trend:      "improving",
      alert:      false,
    },
    intent: {
      current:    "solicitar_portabilidade",
      confidence: 0.85,
      history:    ["saudacao", "solicitar_portabilidade"],
    },
    flags: [],
    sla: {
      elapsed_ms:      45_000,
      target_ms:       300_000,
      percentage:      15,
      breach_imminent: false,
    },
    customer_context: {
      historical_insights:    [],
      conversation_insights:  [],
    },
  }
}

// ── Express + WS server ───────────────────────────────────────────────────

const app  = express()
app.use(express.json())

const httpServer = http.createServer(app)
const wss        = new WebSocketServer({ noServer: true })

httpServer.on("upgrade", (req, socket, head) => {
  const url = new URL(req.url ?? "", `http://${req.headers.host}`)
  if (url.pathname === "/agent/ws") {
    wss.handleUpgrade(req, socket, head, (ws) => wss.emit("connection", ws, req))
  } else {
    socket.destroy()
  }
})

wss.on("connection", (ws: WebSocket, req: http.IncomingMessage) => {
  const url    = new URL(req.url ?? "", `http://${req.headers.host}`)
  const poolId = url.searchParams.get("pool") ?? "unknown"
  const clientId = crypto.randomUUID()

  // Herda sessões já atribuídas ao pool (cobre reconexão do StrictMode)
  const inheritedSessions = new Set(poolSessions.get(poolId) ?? [])
  const state: ClientState = { id: clientId, poolId, ws, sessions: inheritedSessions }
  clients.set(clientId, state)

  console.log(`[mock] WS connected  client=${clientId} pool=${poolId}  sessions=${[...inheritedSessions]}  (total: ${clients.size})`)

  // Handshake imediato
  ws.send(JSON.stringify({ type: "connection.accepted", pool_id: poolId }))

  ws.on("message", (raw: Buffer) => {
    try {
      const msg = JSON.parse(raw.toString()) as Record<string, unknown>

      if (msg["type"] === "pong") return  // heartbeat — ignorar

      if (msg["type"] === "message.text") {
        const sid = msg["session_id"] as string | undefined
        if (!sid || !state.sessions.has(sid)) return

        agentLog.push({ ts: new Date().toISOString(), clientId, payload: msg })
        console.log(`[mock] AGENT → session=${sid}  text="${msg["text"]}"`)

        // Echo de volta como agent_human (simula o que o servidor real faria)
        const echo = {
          type:       "message.text",
          session_id: sid,
          message_id: `echo-${Date.now()}`,
          author:     { type: "agent_human", id: clientId },
          text:       msg["text"],
          timestamp:  new Date().toISOString(),
          visibility: "all",
        }
        sendToSession(sid, echo)
      }
    } catch { /* ignore */ }
  })

  ws.on("close", () => {
    clients.delete(clientId)
    console.log(`[mock] WS disconnected  client=${clientId}  (total: ${clients.size})`)
  })
})

// ── REST stubs (consumidos pela UI via proxy /api → :3100) ────────────────

app.get("/supervisor_state/:sessionId", (req: Request, res: Response) => {
  const sid   = req.params["sessionId"] as string
  const state = supervisorStates.get(sid) ?? defaultSupervisorState(sid)
  res.json(state)
})

app.get("/supervisor_capabilities/:_sessionId", (_req: Request, res: Response) => {
  res.json({
    suggested_agents: [
      {
        agent_type_id:       "agente_retencao_ia_v1",
        relevance:           "high",
        interaction_model:   "background",
        available_instances: 2,
        auto_join:           false,
        circuit_breaker:     "closed",
        reason:              "Alta probabilidade de churn detectada",
      },
    ],
    escalations: [
      {
        pool_id:           "retencao_especialista",
        reason:            "Cliente insatisfeito após 3 tentativas",
        estimated_wait_s:  45,
        recommended:       false,
      },
    ],
  })
})

app.get("/conversation_history/:sessionId", (req: Request, res: Response) => {
  const sid = req.params["sessionId"] as string
  res.json({
    session_id: sid,
    messages:   [
      {
        id:         `hist-1-${sid}`,
        author:     "customer",
        text:       "Boa tarde, quero cancelar meu plano.",
        timestamp:  new Date(Date.now() - 120_000).toISOString(),
        visibility: "all",
      },
      {
        id:         `hist-2-${sid}`,
        author:     "agent_ai",
        text:       "Olá! Entendo. Poderia me informar o motivo?",
        timestamp:  new Date(Date.now() - 90_000).toISOString(),
        visibility: "all",
      },
    ],
  })
})

app.post("/agent_done/:sessionId", (req: Request, res: Response) => {
  const sid = req.params["sessionId"] as string
  console.log(`[mock] agent_done session=${sid}`, req.body)

  // Envia session.closed para a UI (simulando o servidor real)
  const outcome = (req.body as Record<string, unknown>)?.["outcome"] ?? "resolved"
  sendToSession(sid, { type: "session.closed", session_id: sid, reason: outcome })

  res.json({ ok: true })
})

// ── API de controle /mock/* ───────────────────────────────────────────────

/**
 * POST /mock/assign
 * Body: { pool?: string, session_id?: string, contact_id?: string, channel?: string }
 * Envia conversation.assigned para todos os clientes do pool especificado.
 * Se pool for omitido, usa o pool da primeira conexão ativa.
 */
app.post("/mock/assign", (req: Request, res: Response) => {
  const body      = req.body as Record<string, string>
  const sessionId = body["session_id"] ?? `sess-${crypto.randomUUID().slice(0, 8)}`
  const contactId = body["contact_id"] ?? `contact-${crypto.randomUUID().slice(0, 8)}`
  const channel   = body["channel"]    ?? "webchat"
  const poolId    = body["pool"]       ?? [...clients.values()][0]?.poolId ?? "retencao_humano"

  const event = {
    type:        "conversation.assigned",
    session_id:  sessionId,
    contact_id:  contactId,
    pool_id:     poolId,
    channel,
    assigned_at: new Date().toISOString(),
  }

  // Registrar no pool-level registry (persiste para futuras reconexões WS)
  const pset = poolSessions.get(poolId) ?? new Set<string>()
  pset.add(sessionId)
  poolSessions.set(poolId, pset)

  // Subscrever todos os clientes do pool na nova sessão
  for (const client of clients.values()) {
    if (client.poolId === poolId) {
      client.sessions.add(sessionId)
    }
  }

  const sent = sendToPool(poolId, event)
  console.log(`[mock] conversation.assigned  session=${sessionId}  pool=${poolId}  sent_to=${sent}`)
  res.json({ ok: true, session_id: sessionId, sent_to: sent })
})

/**
 * POST /mock/message
 * Body: { session_id: string, author_type?: string, text: string, visibility?: string }
 */
app.post("/mock/message", (req: Request, res: Response) => {
  const body       = req.body as Record<string, string>
  const sessionId  = body["session_id"]
  const authorType = body["author_type"] ?? "customer"
  const text       = body["text"]        ?? "(mensagem vazia)"
  const visibility = body["visibility"]  ?? "all"

  if (!sessionId) { res.status(400).json({ error: "session_id required" }); return }

  const event = {
    type:       "message.text",
    session_id: sessionId,
    message_id: `msg-${Date.now()}`,
    author:     { type: authorType },
    text,
    timestamp:  new Date().toISOString(),
    visibility,
  }

  const sent = sendToSession(sessionId, event)
  console.log(`[mock] message  session=${sessionId}  author=${authorType}  sent_to=${sent}`)
  res.json({ ok: true, sent_to: sent })
})

/**
 * POST /mock/typing
 * Body: { session_id: string, author_type?: string }
 */
app.post("/mock/typing", (req: Request, res: Response) => {
  const body       = req.body as Record<string, string>
  const sessionId  = body["session_id"]
  const authorType = body["author_type"] ?? "agent_ai"

  if (!sessionId) { res.status(400).json({ error: "session_id required" }); return }

  const event = { type: "agent.typing", session_id: sessionId, author_type: authorType }
  const sent  = sendToSession(sessionId, event)
  console.log(`[mock] typing  session=${sessionId}  author=${authorType}  sent_to=${sent}`)
  res.json({ ok: true, sent_to: sent })
})

/**
 * POST /mock/close
 * Body: { session_id: string, reason?: "client_disconnect"|"resolved"|"abandoned" }
 */
app.post("/mock/close", (req: Request, res: Response) => {
  const body      = req.body as Record<string, string>
  const sessionId = body["session_id"]
  const reason    = body["reason"] ?? "client_disconnect"

  if (!sessionId) { res.status(400).json({ error: "session_id required" }); return }

  const event = { type: "session.closed", session_id: sessionId, reason }
  const sent  = sendToSession(sessionId, event)

  // Se fechamento definitivo, remover sessão dos clientes e do pool registry
  if (reason !== "client_disconnect") {
    for (const client of clients.values()) {
      client.sessions.delete(sessionId)
    }
    for (const pset of poolSessions.values()) {
      pset.delete(sessionId)
    }
  }

  console.log(`[mock] session.closed  session=${sessionId}  reason=${reason}  sent_to=${sent}`)
  res.json({ ok: true, sent_to: sent })
})

/**
 * POST /mock/menu
 * Body: { session_id, menu_id?, interaction, prompt, options?: [{id,label}], fields?: [{id,label,type}] }
 */
app.post("/mock/menu", (req: Request, res: Response) => {
  const body      = req.body as Record<string, unknown>
  const sessionId = body["session_id"] as string

  if (!sessionId) { res.status(400).json({ error: "session_id required" }); return }

  const event = {
    type:        "menu.render",
    session_id:  sessionId,
    menu_id:     (body["menu_id"] as string) ?? `menu-${Date.now()}`,
    interaction: body["interaction"] ?? "button",
    prompt:      body["prompt"]      ?? "Selecione uma opção:",
    options:     body["options"],
    fields:      body["fields"],
  }

  const sent = sendToSession(sessionId, event)
  console.log(`[mock] menu.render  session=${sessionId}  interaction=${event.interaction}  sent_to=${sent}`)
  res.json({ ok: true, sent_to: sent })
})

/**
 * POST /mock/supervisor
 * Body: supervisor_state parcial indexado por session_id
 * { session_id: string, sentiment_score?: number, intent?: string, sla_pct?: number, flags?: string[] }
 */
app.post("/mock/supervisor", (req: Request, res: Response) => {
  const body      = req.body as Record<string, unknown>
  const sessionId = body["session_id"] as string
  if (!sessionId) { res.status(400).json({ error: "session_id required" }); return }

  const current = (supervisorStates.get(sessionId) as Record<string, unknown>) ?? defaultSupervisorState(sessionId)
  const score   = typeof body["sentiment_score"] === "number" ? body["sentiment_score"] : (current["sentiment"] as Record<string, unknown>)["current"] as number
  const slaPct  = typeof body["sla_pct"]         === "number" ? body["sla_pct"]         : (current["sla"] as Record<string, unknown>)["percentage"] as number
  const intent  = typeof body["intent"]          === "string" ? body["intent"]          : (current["intent"] as Record<string, unknown>)["current"]
  const flags   = Array.isArray(body["flags"])                ? body["flags"]           : (current["flags"] as string[])

  const updated = {
    ...current,
    sentiment: { ...current["sentiment"] as object, current: score, alert: score < -0.5 },
    intent:    { ...current["intent"] as object, current: intent },
    sla:       { ...current["sla"] as object, percentage: slaPct, breach_imminent: slaPct > 90 },
    flags,
  }
  supervisorStates.set(sessionId, updated)

  // Notificar a UI para re-buscar
  sendToSession(sessionId, { type: "supervisor_state.updated", session_id: sessionId })
  res.json({ ok: true, state: updated })
})

/**
 * GET /mock/status — estado atual das conexões WS
 */
app.get("/mock/status", (_req: Request, res: Response) => {
  const snapshot = [...clients.values()].map(c => ({
    clientId: c.id,
    poolId:   c.poolId,
    sessions: [...c.sessions],
    wsState:  c.ws.readyState,
  }))
  res.json({ connections: snapshot.length, clients: snapshot })
})

/**
 * GET /mock/log — mensagens enviadas pela UI
 */
app.get("/mock/log", (_req: Request, res: Response) => {
  res.json({ count: agentLog.length, messages: agentLog })
})

/**
 * DELETE /mock/log — limpa o log
 */
app.delete("/mock/log", (_req: Request, res: Response) => {
  agentLog.length = 0
  res.json({ ok: true })
})

app.get("/health", (_req: Request, res: Response) => {
  res.json({ status: "ok", service: "mock-agent-ws", connections: clients.size })
})

// ── Start ─────────────────────────────────────────────────────────────────

const PORT = Number(process.env["PORT"] ?? 3101)   // 3101 para não conflitar com o servidor real (3100)
httpServer.listen(PORT, () => {
  console.log(`
╔══════════════════════════════════════════════════════════╗
║     Agent Assist UI — Mock Backend Server (porta ${PORT})    ║
╠══════════════════════════════════════════════════════════╣
║  WebSocket   ws://localhost:${PORT}/agent/ws?pool=<id>      ║
║  REST API    http://localhost:${PORT}                       ║
╠══════════════════════════════════════════════════════════╣
║  Controle:                                               ║
║  POST /mock/assign     → atribuir novo contato           ║
║  POST /mock/message    → enviar mensagem à sessão        ║
║  POST /mock/typing     → indicador de digitação IA       ║
║  POST /mock/close      → fechar sessão                   ║
║  POST /mock/menu       → renderizar menu interativo      ║
║  POST /mock/supervisor → atualizar supervisor_state      ║
║  GET  /mock/status     → ver conexões ativas             ║
║  GET  /mock/log        → ver mensagens enviadas pela UI  ║
╚══════════════════════════════════════════════════════════╝
`)
})
