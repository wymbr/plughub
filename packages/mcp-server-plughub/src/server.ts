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
import { registerBpmTools }        from "./tools/bpm"
import type { BpmDeps }            from "./tools/bpm"
import { registerRuntimeTools }    from "./tools/runtime"
import type { RuntimeDeps }        from "./tools/runtime"
import { registerSupervisorTools } from "./tools/supervisor"
import type { SupervisorDeps }     from "./tools/supervisor"
import { registerEvaluationTools } from "./tools/evaluation"
import type { EvaluationDeps }     from "./tools/evaluation"
import { createRedisClient }       from "./infra/redis"
import { createKafkaProducer }     from "./infra/kafka"
import { createRegistryClient }    from "./infra/registry-client"
import { createPostgresClient }    from "./infra/postgres"

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
    postgres:         createPostgresClient(),
    proxyUrl:         process.env["MCP_PROXY_URL"]      ?? "http://localhost:7422",
    skillRegistryUrl: process.env["SKILL_REGISTRY_URL"] ?? "http://localhost:3400",
  }

  const bpmDeps: BpmDeps = { kafka, redis }

  const supervisorDeps: SupervisorDeps = { redis, kafka }

  // Registrar todas as tools
  registerBpmTools(server, bpmDeps)
  registerRuntimeTools(server, runtimeDeps)
  registerSupervisorTools(server, supervisorDeps)
  registerEvaluationTools(server, evalDeps)

  return server
}

export async function startServer(config: ServerConfig): Promise<void> {
  const app = express()
  app.use(express.json())

  // Redis shared for Agent Assist REST + WS endpoints
  const redis = createRedisClient()
  // Kafka producer for publishing human-agent outbound messages
  const kafka = createKafkaProducer()

  const mcpServer = createServer(undefined)

  // Map sessionId → transport para suportar conexões simultâneas
  const transports = new Map<string, SSEServerTransport>()

  // GET /sse — cliente abre conexão SSE
  app.get("/sse", async (req: Request, res: Response) => {
    const transport = new SSEServerTransport("/messages", res)
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

      res.json({
        session_id:   sessionId,
        turn_count:   turns.length,
        is_stale:     false,
        sentiment: {
          current:    currentSentiment,
          trajectory: trajectory.slice(0, -1), // exclude the current partial from trajectory display
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
          historical_insights:   [],
          conversation_insights: [],
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

  httpServer.on("upgrade", (request, socket, head) => {
    const url = new URL(request.url ?? "", `http://${request.headers.host}`)
    if (url.pathname === "/agent/ws") {
      wss.handleUpgrade(request, socket, head, (ws) => {
        wss.emit("connection", ws, request)
      })
    } else {
      socket.destroy()
    }
  })

  wss.on("connection", (ws: WebSocket, request: http.IncomingMessage) => {
    const url       = new URL(request.url ?? "", `http://${request.headers.host}`)
    const poolId    = url.searchParams.get("pool") ?? ""
    // activeSessionId starts from the URL param; updated when conversation.assigned arrives.
    // Using let so the message handler always sees the current session_id even in lobby mode.
    let   activeSessionId = url.searchParams.get("session_id") ?? ""

    // Send connection.accepted immediately
    ws.send(JSON.stringify({ type: "connection.accepted", session_id: activeSessionId, pool_id: poolId }))

    const subscriber = redis.duplicate()

    const forward = (_channel: string, message: string) => {
      if (ws.readyState !== WebSocket.OPEN) return
      ws.send(message)

      // When conversation.assigned arrives via the pool channel,
      // track the new session_id so the message handler can use it,
      // and also subscribe to the session-specific channel so subsequent
      // customer messages (published to agent:events:{session_id}) reach this socket.
      try {
        const event = JSON.parse(message) as Record<string, unknown>
        if (event["type"] === "conversation.assigned" && typeof event["session_id"] === "string") {
          activeSessionId = event["session_id"]
          subscriber.subscribe(`agent:events:${event["session_id"]}`, (err) => {
            if (err) console.error("Redis session subscribe error:", err)
          })
        }
      } catch { /* ignore */ }
    }

    if (activeSessionId) {
      // Direct session connection — agent already knows their session (e.g. re-connect).
      subscriber.subscribe(`agent:events:${activeSessionId}`, (err) => {
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
    }

    subscriber.on("message", forward)

    // ── Inbound messages FROM the human agent → conversations.outbound ───────
    // The agent UI sends { type: "message.text", text, timestamp } over this socket.
    // We look up the contact_id from Redis (written by channel-gateway on connect),
    // then publish to conversations.outbound so the channel-gateway delivers the reply
    // to the customer's WebSocket.
    ws.on("message", async (raw: Buffer) => {
      try {
        const msg = JSON.parse(raw.toString()) as Record<string, unknown>
        if (msg["type"] !== "message.text") return   // ignore pong, etc.
        if (!activeSessionId) return                  // lobby mode, no session yet

        // Look up contact_id and channel from session metadata
        let contactId = activeSessionId  // fallback: use session_id
        try {
          const metaRaw = await redis.get(`session:${activeSessionId}:meta`)
          if (metaRaw) {
            const meta = JSON.parse(metaRaw) as Record<string, string>
            if (meta["contact_id"]) contactId = meta["contact_id"]
          }
        } catch { /* use fallback */ }

        await kafka.publish("conversations.outbound", {
          type:       "message.text",
          contact_id: contactId,
          session_id: activeSessionId,
          message_id: crypto.randomUUID(),
          channel:    "chat",
          author:     { type: "agent_human", id: "human_agent" },
          text:       typeof msg["text"] === "string" ? msg["text"] : "",
          timestamp:  typeof msg["timestamp"] === "string"
            ? msg["timestamp"]
            : new Date().toISOString(),
        })
      } catch (err) {
        console.error(`Agent WS message error session=${activeSessionId}:`, err)
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
      subscriber.unsubscribe()
      subscriber.quit()
    })

    ws.on("error", (err) => {
      console.error(`Agent WS error session=${activeSessionId} pool=${poolId}:`, err)
    })
  })

  await new Promise<void>((resolve) => {
    httpServer.listen(config.port, config.host, () => {
      console.log(`✅ mcp-server-plughub iniciado`)
      console.log(`   Transporte: SSE`)
      console.log(`   Endpoint:   http://${config.host}:${config.port}/sse`)
      console.log(`   Agent WS:   ws://${config.host}:${config.port}/agent/ws`)
      console.log(`   Tools BPM:        conversation_start, conversation_status, conversation_end, rule_dry_run, notification_send, conversation_escalate`)
      console.log(`   Tools Runtime:    agent_login, agent_ready, agent_busy, agent_done, agent_pause, agent_logout, insight_register`)
      console.log(`   Tools Supervisor: supervisor_state, supervisor_capabilities, agent_join_conference`)
      console.log(`   Tools Evaluation: transcript_get, evaluation_context_resolve, evaluation_publish`)
      resolve()
    })
  })
}
