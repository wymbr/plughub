/**
 * bpm.test.ts
 * Testes das tools de BPM — validação de input e contrato de saída.
 * Spec: PlugHub v24.0 seção 9.4
 */

import { describe, it, expect, beforeEach } from "vitest"
import RedisMock from "ioredis-mock"
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import { registerBpmTools } from "../tools/bpm"
import { createCapturingKafkaProducer } from "../infra/kafka"
import type { CapturingKafkaProducer } from "../infra/kafka"

// ─── callTool helper (MCP SDK no longer exposes callTool on McpServer) ─────────

type ToolResponse = {
  isError?: boolean
  content: Array<{ type: string; text: string }>
}

type TestServer = {
  callTool: (name: string, input: unknown) => Promise<ToolResponse>
}

function makeTestServer(mcpServer: McpServer): TestServer {
  return {
    callTool: async (name: string, input: unknown) => {
      const reg = (mcpServer as unknown as Record<string, Record<string, { handler: (i: unknown) => Promise<ToolResponse> }>>)
        ._registeredTools?.[name]
      if (!reg) throw new Error(`Tool '${name}' not registered`)
      try {
        return await reg.handler(input)
      } catch (e) {
        // Mirror MCP SDK behaviour: uncaught handler errors → isError response
        const msg = e instanceof Error ? e.message : String(e)
        return {
          isError: true,
          content: [{ type: "text", text: JSON.stringify({ error: "tool_error", message: msg }) }],
        }
      }
    },
  }
}

function _body(res: ToolResponse): Record<string, unknown> {
  return JSON.parse(res.content[0]!.text) as Record<string, unknown>
}

// ─────────────────────────────────────────────────────────────────────────────

describe("Tools BPM — validação de input", () => {
  let redis: InstanceType<typeof RedisMock>
  let kafka: CapturingKafkaProducer
  let server: TestServer

  beforeEach(() => {
    redis = new RedisMock()
    kafka = createCapturingKafkaProducer()

    const mcpServer = new McpServer({ name: "test-bpm", version: "0.0.1" })
    registerBpmTools(mcpServer, { redis, kafka })
    server = makeTestServer(mcpServer)
  })

  it("conversation_start rejeita customer_id com formato inválido", async () => {
    const res = await server.callTool("conversation_start", {
      channel:     "webchat",
      customer_id: "nao-uuid",
      tenant_id:   "tenant_test",
    })
    expect(res.isError).toBe(true)
  })

  it("conversation_start aceita payload mínimo válido", async () => {
    const res = await server.callTool("conversation_start", {
      channel:     "webchat",
      customer_id: "550e8400-e29b-41d4-a716-446655440000",
      tenant_id:   "tenant_test",
    })
    const body = _body(res)
    expect(res.isError).toBeFalsy()
    expect(body).toHaveProperty("session_id")
    expect(body).toHaveProperty("status", "routing")
    expect(body).toHaveProperty("started_at")
  })

  it("conversation_start aceita process_context opcional", async () => {
    const res = await server.callTool("conversation_start", {
      channel:     "whatsapp",
      customer_id: "550e8400-e29b-41d4-a716-446655440000",
      tenant_id:   "tenant_test",
      process_context: {
        process_id:       "proc_001",
        process_instance: "inst_001",
        status:           "running",
      },
    })
    const body = _body(res)
    expect(res.isError).toBeFalsy()
    expect(body).toHaveProperty("session_id")
  })

  it("conversation_start persiste meta no Redis e publica em Kafka", async () => {
    const res = await server.callTool("conversation_start", {
      channel:     "webchat",
      customer_id: "550e8400-e29b-41d4-a716-446655440000",
      tenant_id:   "tenant_test",
    })
    const body = _body(res)
    const sessionId = body.session_id as string

    // Redis meta deve existir
    const metaRaw = await redis.get(`session:${sessionId}:meta`)
    expect(metaRaw).not.toBeNull()
    const meta = JSON.parse(metaRaw!) as Record<string, string>
    expect(meta["channel"]).toBe("webchat")
    expect(meta["tenant_id"]).toBe("tenant_test")

    // Deve ter publicado contact_open + conversations.inbound
    const contactOpen = kafka.events.find(e =>
      e.topic === "conversations.events" && e.message["event_type"] === "contact_open"
    )
    expect(contactOpen).toBeDefined()

    const inbound = kafka.events.find(e => e.topic === "conversations.inbound")
    expect(inbound).toBeDefined()
    expect(inbound!.message["session_id"]).toBe(sessionId)
  })

  it("conversation_end rejeita reason inválido", async () => {
    const res = await server.callTool("conversation_end", {
      session_id: "550e8400-e29b-41d4-a716-446655440000",
      tenant_id:  "tenant_test",
      reason:     "invalid_reason",
    })
    expect(res.isError).toBe(true)
  })

  it("conversation_end publica contact_closed e session.closed", async () => {
    const sessionId = "550e8400-e29b-41d4-a716-446655440000"
    const res = await server.callTool("conversation_end", {
      session_id: sessionId,
      tenant_id:  "tenant_test",
      reason:     "timeout",
    })
    const body = _body(res)
    expect(res.isError).toBeFalsy()
    expect(body.terminated).toBe(true)
    expect(body.reason).toBe("timeout")

    const closed = kafka.events.find(e =>
      e.topic === "conversations.events" && e.message["event_type"] === "contact_closed"
    )
    expect(closed).toBeDefined()
  })

  it("rule_dry_run retorna estrutura de simulação", async () => {
    const res = await server.callTool("rule_dry_run", {
      tenant_id: "tenant_test",
      rule: {
        name:        "churn_escalation",
        expression:  { sentiment_below: -0.5, churn_risk_above: 0.7 },
        target_pool: "retencao_especialista",
      },
      history_window_days: 7,
    })
    const body = _body(res)
    expect(res.isError).toBeFalsy()
    expect(body).toHaveProperty("rule_name", "churn_escalation")
    expect(body).toHaveProperty("simulation")
    // simulation pode ter 'error' caso rules_engine_url não esteja disponível — ambos são válidos
    expect(body.simulation).toBeDefined()
  })

  it("notification_send entrega mensagem e publica em conversations.outbound", async () => {
    // Pré-popula contact_id no Redis (como faria o channel-gateway)
    const sessionId = "550e8400-e29b-41d4-a716-446655440000"
    const contactId = "660e8400-e29b-41d4-a716-446655440001"
    await redis.set(`session:${sessionId}:contact_id`, contactId)

    const res = await server.callTool("notification_send", {
      session_id: sessionId,
      message:    "Seu atendimento foi concluído.",
    })
    const body = _body(res)
    expect(res.isError).toBeFalsy()
    expect(body.delivered).toBe(true)
    expect(body.contact_id).toBe(contactId)

    const outbound = kafka.events.find(e => e.topic === "conversations.outbound")
    expect(outbound).toBeDefined()
    expect(outbound!.message["text"]).toBe("Seu atendimento foi concluído.")
  })

  it("conversation_escalate publica evento de roteamento com pool_id explícito", async () => {
    const sessionId = "550e8400-e29b-41d4-a716-446655440000"
    const res = await server.callTool("conversation_escalate", {
      session_id:  sessionId,
      target_pool: "retencao_humano",
      error_reason: "Churn risk elevado",
    })
    const body = _body(res)
    expect(res.isError).toBeFalsy()
    expect(body.escalated).toBe(true)
    expect(body.target_pool).toBe("retencao_humano")

    const inbound = kafka.events.find(e =>
      e.topic === "conversations.inbound" && e.message["pool_id"] === "retencao_humano"
    )
    expect(inbound).toBeDefined()
    expect(inbound!.message["confidence"]).toBe(0)
  })
})
