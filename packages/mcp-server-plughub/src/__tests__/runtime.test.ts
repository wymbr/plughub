/**
 * runtime.test.ts
 * Testes de integração das tools de Agent Runtime.
 * Spec: PlugHub v24.0 seções 4.5, 4.2, 3.4a
 *
 * Usa Redis in-memory (ioredis-mock) e Kafka de captura.
 * Não requer infra externa — adequado para CI.
 */

import { describe, it, expect, beforeEach } from "vitest"
import RedisMock from "ioredis-mock"
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import { registerRuntimeTools }          from "../tools/runtime"
import { createCapturingKafkaProducer }  from "../infra/kafka"
import type { CapturingKafkaProducer }   from "../infra/kafka"
import { createStubRegistryClient }      from "../infra/registry-client"
import { keys }                          from "../infra/redis"

// ─── Fixtures ─────────────────────────────────────────────────────────────────

const TENANT         = "tenant_test"
const AGENT_TYPE_ID  = "agente_retencao_v1"
const INSTANCE_ID    = "inst_retencao_abc123"
const CONV_ID        = "550e8400-e29b-41d4-a716-446655440000"
const CONV_ID_2      = "660e8400-e29b-41d4-a716-446655440001"

// ─── Tipo helper para callTool ─────────────────────────────────────────────────

type ToolResponse = {
  isError?: boolean
  content: Array<{ type: string; text: string }>
}
type TestServer = {
  callTool: (name: string, input: unknown) => Promise<ToolResponse>
}

// ─── Setup ────────────────────────────────────────────────────────────────────

describe("Tools Agent Runtime — integração com Redis", () => {
  let redis: InstanceType<typeof RedisMock>
  let kafka: CapturingKafkaProducer
  let server: TestServer

  beforeEach(() => {
    redis  = new RedisMock()
    kafka  = createCapturingKafkaProducer()

    const registry = createStubRegistryClient([
      {
        agent_type_id:           AGENT_TYPE_ID,
        max_concurrent_sessions: 2,
        execution_model:         "stateless",
        pools:                   ["retencao_humano", "retencao_bot"],
        permissions:             ["mcp-server-crm:customer_get"],
      },
    ])

    const mcpServer = new McpServer({ name: "test", version: "0.0.1" })
    registerRuntimeTools(mcpServer, { redis, kafka, registry })
    server = mcpServer as unknown as TestServer
  })

  // ── agent_login ─────────────────────────────────────────────────────────

  describe("agent_login", () => {
    it("retorna session_token, token_expires_at e instance_id", async () => {
      const res  = await _login()
      const body = _body(res)

      expect(res.isError).toBeFalsy()
      expect(body).toHaveProperty("session_token")
      expect(body).toHaveProperty("token_expires_at")
      expect(body.instance_id).toBe(INSTANCE_ID)
    })

    it("persiste estado 'logged_in' no Redis com max_concurrent_sessions", async () => {
      await _login()

      const instanceKey = keys.agentInstance(TENANT, INSTANCE_ID)
      expect(await redis.hget(instanceKey, "state")).toBe("logged_in")
      expect(await redis.hget(instanceKey, "max_concurrent_sessions")).toBe("2")
      expect(await redis.hget(instanceKey, "current_sessions")).toBe("0")
      expect(await redis.hget(instanceKey, "agent_type_id")).toBe(AGENT_TYPE_ID)
    })

    it("cria índice token → instance_id no Redis", async () => {
      const res   = await _login()
      const token = _body(res).session_token as string

      const tokenKey = keys.agentToken(TENANT, token)
      expect(await redis.get(tokenKey)).toBe(INSTANCE_ID)
    })

    it("publica evento agent_login no Kafka", async () => {
      await _login()

      const ev = kafka.events.find(e => e.message["event"] === "agent_login")
      expect(ev).toBeDefined()
      expect(ev!.topic).toBe("agent.lifecycle")
      expect(ev!.message["instance_id"]).toBe(INSTANCE_ID)
    })

    it("rejeita agent_type_id desconhecido", async () => {
      const res  = await server.callTool("agent_login", {
        agent_type_id: "agente_inexistente_v1",
        instance_id:   INSTANCE_ID,
        tenant_id:     TENANT,
      })
      expect(res.isError).toBe(true)
      expect(_body(res).error).toBe("agent_type_not_found")
    })

    it("rejeita payload sem tenant_id", async () => {
      const res = await server.callTool("agent_login", {
        agent_type_id: AGENT_TYPE_ID,
        instance_id:   INSTANCE_ID,
        // tenant_id ausente
      })
      expect(res.isError).toBe(true)
    })
  })

  // ── agent_ready ─────────────────────────────────────────────────────────

  describe("agent_ready", () => {
    it("muda estado para 'ready' e adiciona instância aos pools", async () => {
      const token = await _loginToken()
      const res   = await server.callTool("agent_ready", { session_token: token })

      expect(res.isError).toBeFalsy()
      expect(_body(res).status).toBe("ready")

      const instanceKey = keys.agentInstance(TENANT, INSTANCE_ID)
      expect(await redis.hget(instanceKey, "state")).toBe("ready")

      // Instância nos dois pools
      expect(await redis.sismember(keys.poolAvailable(TENANT, "retencao_humano"), INSTANCE_ID)).toBe(1)
      expect(await redis.sismember(keys.poolAvailable(TENANT, "retencao_bot"),    INSTANCE_ID)).toBe(1)
    })

    it("publica evento agent_ready no Kafka com lista de pools", async () => {
      const token = await _loginToken()
      await server.callTool("agent_ready", { session_token: token })

      const ev = kafka.events.find(e => e.message["event"] === "agent_ready")
      expect(ev).toBeDefined()
      expect(ev!.message["pools"]).toEqual(["retencao_humano", "retencao_bot"])
    })

    it("rejeita token inválido", async () => {
      const res = await server.callTool("agent_ready", { session_token: "token_invalido" })
      expect(res.isError).toBe(true)
      expect(_body(res).error).toBe("invalid_token")
    })

    it("rejeita chamada quando estado não é logged_in ou paused", async () => {
      const token = await _loginAndReadyToken()

      // Tentar agent_ready de novo (já em 'ready')
      const res = await server.callTool("agent_ready", { session_token: token })
      expect(res.isError).toBe(true)
      expect(_body(res).error).toBe("invalid_state")
    })
  })

  // ── agent_busy ──────────────────────────────────────────────────────────

  describe("agent_busy", () => {
    it("incrementa current_sessions e muda estado para 'busy'", async () => {
      const token = await _loginAndReadyToken()
      const res   = await server.callTool("agent_busy", {
        session_token:   token,
        conversation_id: CONV_ID,
      })

      expect(res.isError).toBeFalsy()
      expect(_body(res).current_sessions).toBe(1)

      const instanceKey = keys.agentInstance(TENANT, INSTANCE_ID)
      expect(await redis.hget(instanceKey, "current_sessions")).toBe("1")
      expect(await redis.hget(instanceKey, "state")).toBe("busy")
    })

    it("adiciona conversation_id ao SET de conversas ativas da instância", async () => {
      const token = await _loginAndReadyToken()
      await server.callTool("agent_busy", { session_token: token, conversation_id: CONV_ID })

      const convKey = keys.agentConversations(TENANT, INSTANCE_ID)
      expect(await redis.sismember(convKey, CONV_ID)).toBe(1)
    })

    it("mantém instância no pool enquanto abaixo do limite (max=2, sessions=1)", async () => {
      const token = await _loginAndReadyToken()
      await server.callTool("agent_busy", { session_token: token, conversation_id: CONV_ID })

      // Ainda com capacidade (1 < 2): deve continuar no pool
      expect(await redis.sismember(keys.poolAvailable(TENANT, "retencao_humano"), INSTANCE_ID)).toBe(1)
    })

    it("remove da fila quando current_sessions == max_concurrent_sessions (max=2)", async () => {
      const token = await _loginAndReadyToken()
      await server.callTool("agent_busy", { session_token: token, conversation_id: CONV_ID })
      await server.callTool("agent_busy", { session_token: token, conversation_id: CONV_ID_2 })

      // Atingiu max: deve sair do pool
      expect(await redis.sismember(keys.poolAvailable(TENANT, "retencao_humano"), INSTANCE_ID)).toBe(0)
      expect(await redis.sismember(keys.poolAvailable(TENANT, "retencao_bot"),    INSTANCE_ID)).toBe(0)
    })

    it("rejeita conversation_id com formato inválido (não UUID)", async () => {
      const token = await _loginAndReadyToken()
      const res   = await server.callTool("agent_busy", {
        session_token:   token,
        conversation_id: "nao-uuid",
      })
      expect(res.isError).toBe(true)
    })
  })

  // ── agent_done ──────────────────────────────────────────────────────────

  describe("agent_done", () => {
    it("aceita payload válido com outcome resolved", async () => {
      const token = await _loginAndReadyToken()
      await server.callTool("agent_busy", { session_token: token, conversation_id: CONV_ID })

      const res  = await _done(token, CONV_ID, "resolved")
      const body = _body(res)

      expect(res.isError).toBeFalsy()
      expect(body.acknowledged).toBe(true)
      expect(body.outcome).toBe("resolved")
      expect(body.conversation_id).toBe(CONV_ID)
    })

    it("decrementa current_sessions no Redis", async () => {
      const token = await _loginAndReadyToken()
      await server.callTool("agent_busy", { session_token: token, conversation_id: CONV_ID })
      await _done(token, CONV_ID, "resolved")

      const instanceKey = keys.agentInstance(TENANT, INSTANCE_ID)
      expect(await redis.hget(instanceKey, "current_sessions")).toBe("0")
    })

    it("volta ao estado 'ready' quando current_sessions chega a 0", async () => {
      const token = await _loginAndReadyToken()
      await server.callTool("agent_busy", { session_token: token, conversation_id: CONV_ID })
      await _done(token, CONV_ID, "resolved")

      const instanceKey = keys.agentInstance(TENANT, INSTANCE_ID)
      expect(await redis.hget(instanceKey, "state")).toBe("ready")
    })

    it("publica evento em conversations.events com outcome e issue_status", async () => {
      const token = await _loginAndReadyToken()
      await server.callTool("agent_busy", { session_token: token, conversation_id: CONV_ID })
      await _done(token, CONV_ID, "resolved")

      const ev = kafka.events.find(e => e.topic === "conversations.events")
      expect(ev).toBeDefined()
      expect(ev!.message["event"]).toBe("conversation_completed")
      expect(ev!.message["outcome"]).toBe("resolved")
      expect(ev!.message["conversation_id"]).toBe(CONV_ID)
    })

    it("rejeita issue_status vazio (mínimo 1 item)", async () => {
      const token = await _loginAndReadyToken()
      await server.callTool("agent_busy", { session_token: token, conversation_id: CONV_ID })

      const res = await server.callTool("agent_done", {
        session_token:   token,
        conversation_id: CONV_ID,
        outcome:         "resolved",
        issue_status:    [],   // deve falhar: mínimo 1
        completed_at:    new Date().toISOString(),
      })
      expect(res.isError).toBe(true)
      expect(_body(res).error).toBe("validation_error")
    })

    it("rejeita escalated_human sem handoff_reason (.refine do schema)", async () => {
      const token = await _loginAndReadyToken()
      await server.callTool("agent_busy", { session_token: token, conversation_id: CONV_ID })

      const res = await server.callTool("agent_done", {
        session_token:   token,
        conversation_id: CONV_ID,
        outcome:         "escalated_human",
        issue_status:    [{ issue_id: "1", description: "problema", status: "unresolved" }],
        completed_at:    new Date().toISOString(),
        // handoff_reason ausente — deve falhar
      })
      expect(res.isError).toBe(true)
      expect(_body(res).error).toBe("validation_error")
    })

    it("aceita escalated_human com handoff_reason", async () => {
      const token = await _loginAndReadyToken()
      await server.callTool("agent_busy", { session_token: token, conversation_id: CONV_ID })

      const res = await server.callTool("agent_done", {
        session_token:   token,
        conversation_id: CONV_ID,
        outcome:         "escalated_human",
        issue_status:    [{ issue_id: "1", description: "Churn detectado", status: "unresolved" }],
        handoff_reason:  "cliente com alto risco de churn — escalando para especialista",
        completed_at:    new Date().toISOString(),
      })
      expect(res.isError).toBeFalsy()
      expect(_body(res).outcome).toBe("escalated_human")
    })

    it("estado 'draining' → 'logged_out' quando última sessão é concluída", async () => {
      const token = await _loginAndReadyToken()
      await server.callTool("agent_busy", { session_token: token, conversation_id: CONV_ID })

      // Logout com sessão ativa → draining
      await server.callTool("agent_logout", { session_token: token })
      const instanceKey = keys.agentInstance(TENANT, INSTANCE_ID)
      expect(await redis.hget(instanceKey, "state")).toBe("draining")

      // agent_done finaliza a última sessão → logged_out e chaves removidas
      await _done(token, CONV_ID, "resolved")
      expect(await redis.hget(instanceKey, "state")).toBeNull()
    })
  })

  // ── agent_pause ─────────────────────────────────────────────────────────

  describe("agent_pause", () => {
    it("muda estado para 'paused' e remove dos pools", async () => {
      const token = await _loginAndReadyToken()
      const res   = await server.callTool("agent_pause", { session_token: token })

      expect(res.isError).toBeFalsy()
      expect(_body(res).status).toBe("paused")

      const instanceKey = keys.agentInstance(TENANT, INSTANCE_ID)
      expect(await redis.hget(instanceKey, "state")).toBe("paused")
      expect(await redis.sismember(keys.poolAvailable(TENANT, "retencao_humano"), INSTANCE_ID)).toBe(0)
      expect(await redis.sismember(keys.poolAvailable(TENANT, "retencao_bot"),    INSTANCE_ID)).toBe(0)
    })

    it("sessões ativas não são alteradas por agent_pause", async () => {
      const token = await _loginAndReadyToken()
      await server.callTool("agent_busy", { session_token: token, conversation_id: CONV_ID })
      await server.callTool("agent_pause", { session_token: token })

      const instanceKey = keys.agentInstance(TENANT, INSTANCE_ID)
      expect(await redis.hget(instanceKey, "current_sessions")).toBe("1")
      expect(await redis.sismember(keys.agentConversations(TENANT, INSTANCE_ID), CONV_ID)).toBe(1)
    })

    it("agente pausado pode ser reativado via agent_ready", async () => {
      const token = await _loginAndReadyToken()
      await server.callTool("agent_pause", { session_token: token })

      const resumeRes = await server.callTool("agent_ready", { session_token: token })
      expect(resumeRes.isError).toBeFalsy()

      const instanceKey = keys.agentInstance(TENANT, INSTANCE_ID)
      expect(await redis.hget(instanceKey, "state")).toBe("ready")
      expect(await redis.sismember(keys.poolAvailable(TENANT, "retencao_humano"), INSTANCE_ID)).toBe(1)
    })

    it("publica evento agent_pause no Kafka", async () => {
      const token = await _loginAndReadyToken()
      await server.callTool("agent_pause", { session_token: token })

      const ev = kafka.events.find(e => e.message["event"] === "agent_pause")
      expect(ev).toBeDefined()
      expect(ev!.topic).toBe("agent.lifecycle")
    })
  })

  // ── agent_logout ────────────────────────────────────────────────────────

  describe("agent_logout", () => {
    it("logged_out imediato quando sem sessões ativas — remove chaves do Redis", async () => {
      const token = await _loginAndReadyToken()
      const res   = await server.callTool("agent_logout", { session_token: token })
      const body  = _body(res)

      expect(res.isError).toBeFalsy()
      expect(body.status).toBe("logged_out")
      expect(body.active_sessions).toBe(0)

      // Chaves da instância removidas
      const instanceKey = keys.agentInstance(TENANT, INSTANCE_ID)
      expect(await redis.hget(instanceKey, "state")).toBeNull()
    })

    it("estado 'draining' quando há sessões ativas — remove dos pools imediatamente", async () => {
      const token = await _loginAndReadyToken()
      await server.callTool("agent_busy", { session_token: token, conversation_id: CONV_ID })

      const res  = await server.callTool("agent_logout", { session_token: token })
      const body = _body(res)

      expect(body.status).toBe("draining")
      expect(body.active_sessions).toBe(1)

      // Fora dos pools
      expect(await redis.sismember(keys.poolAvailable(TENANT, "retencao_humano"), INSTANCE_ID)).toBe(0)

      // Instância ainda existe no Redis (sessão ativa)
      const instanceKey = keys.agentInstance(TENANT, INSTANCE_ID)
      expect(await redis.hget(instanceKey, "state")).toBe("draining")
    })

    it("publica evento agent_logout no Kafka", async () => {
      const token = await _loginAndReadyToken()
      await server.callTool("agent_logout", { session_token: token })

      const ev = kafka.events.find(e => e.message["event"] === "agent_logout")
      expect(ev).toBeDefined()
      expect(ev!.topic).toBe("agent.lifecycle")
    })
  })

  // ── insight_register ────────────────────────────────────────────────────

  describe("insight_register", () => {
    it("persiste insight no Redis e retorna item_id e registered_at", async () => {
      const token = await _loginAndReadyToken()
      const res   = await server.callTool("insight_register", {
        session_token:   token,
        conversation_id: CONV_ID,
        categoria:       "insight.conversa.servico.falha_tecnica",
        conteudo:        { service: "banda_larga", resolved: false },
        prioridade:      80,
      })
      const body = _body(res)

      expect(res.isError).toBeFalsy()
      expect(body).toHaveProperty("item_id")
      expect(body).toHaveProperty("registered_at")
      expect(body.categoria).toBe("insight.conversa.servico.falha_tecnica")

      // Verificar que foi persistido no Redis
      const insightKey = keys.insight(TENANT, CONV_ID, body.item_id as string)
      const stored = await redis.get(insightKey)
      expect(stored).not.toBeNull()

      const item = JSON.parse(stored!)
      expect(item.categoria).toBe("insight.conversa.servico.falha_tecnica")
      expect(item.conteudo).toMatchObject({ service: "banda_larga", resolved: false })
      expect(item.prioridade).toBe(80)
      expect(item.status).toBe("pendente")
    })

    it("TTL aplicado com base em validade informada", async () => {
      const token = await _loginAndReadyToken()
      const validadeDate = new Date(Date.now() + 60 * 60 * 1000) // 1h à frente

      const res  = await server.callTool("insight_register", {
        session_token:   token,
        conversation_id: CONV_ID,
        categoria:       "insight.conversa.produto.interesse",
        conteudo:        { produto: "plano_premium" },
        prioridade:      60,
        validade:        validadeDate.toISOString(),
      })
      const body = _body(res)

      const insightKey = keys.insight(TENANT, CONV_ID, body.item_id as string)
      const ttl = await redis.ttl(insightKey)
      // TTL deve estar entre 3500s e 3600s (±100s de tolerância)
      expect(ttl).toBeGreaterThan(3500)
      expect(ttl).toBeLessThanOrEqual(3600)
    })

    it("rejeita categoria insight.historico.*", async () => {
      const token = await _loginAndReadyToken()
      const res   = await server.callTool("insight_register", {
        session_token:   token,
        conversation_id: CONV_ID,
        categoria:       "insight.historico.servico.falha",
        conteudo:        { service: "banda_larga" },
        prioridade:      50,
      })
      expect(res.isError).toBe(true)
      // Rejeição pelo regex antes de chegar ao handler
      expect(_body(res).error).toBe("validation_error")
    })

    it("rejeita categoria sem prefixo insight.conversa.*", async () => {
      const token = await _loginAndReadyToken()
      const res   = await server.callTool("insight_register", {
        session_token:   token,
        conversation_id: CONV_ID,
        categoria:       "outro.tipo.qualquer",
        conteudo:        {},
        prioridade:      50,
      })
      expect(res.isError).toBe(true)
    })

    it("rejeita prioridade fora do intervalo [0, 100]", async () => {
      const token = await _loginAndReadyToken()
      const res   = await server.callTool("insight_register", {
        session_token:   token,
        conversation_id: CONV_ID,
        categoria:       "insight.conversa.teste",
        conteudo:        {},
        prioridade:      150,  // > 100
      })
      expect(res.isError).toBe(true)
    })

    it("rejeita token inválido", async () => {
      const res = await server.callTool("insight_register", {
        session_token:   "jwt_invalido",
        conversation_id: CONV_ID,
        categoria:       "insight.conversa.teste",
        conteudo:        {},
        prioridade:      50,
      })
      expect(res.isError).toBe(true)
      expect(_body(res).error).toBe("invalid_token")
    })

    it("rejeita 'insight.historico.atendimento.*' — retorna MCP error, não lança exceção", async () => {
      const token = await _loginAndReadyToken()

      // Deve retornar isError: true — nunca lançar exceção não tratada
      let threw = false
      let res: ToolResponse = { content: [], isError: true }
      try {
        res = await server.callTool("insight_register", {
          session_token:   token,
          conversation_id: CONV_ID,
          categoria:       "insight.historico.atendimento.cancelamento",
          conteudo:        { motivo: "preco_alto" },
          prioridade:      70,
        })
      } catch {
        threw = true
      }

      expect(threw).toBe(false)  // não deve lançar exceção
      expect(res.isError).toBe(true)
      expect(_body(res).error).toBe("validation_error")
    })

    it("agent_done com issue_status ausente — retorna MCP error, não lança exceção", async () => {
      const token = await _loginAndReadyToken()
      await server.callTool("agent_busy", { session_token: token, conversation_id: CONV_ID })

      let threw = false
      let res: ToolResponse = { content: [], isError: true }
      try {
        res = await server.callTool("agent_done", {
          session_token:   token,
          conversation_id: CONV_ID,
          outcome:         "resolved",
          // issue_status ausente — deve ser MCP error, não exceção
          completed_at:    new Date().toISOString(),
        })
      } catch {
        threw = true
      }

      expect(threw).toBe(false)  // não deve lançar exceção
      expect(res.isError).toBe(true)
    })
  })

  // ── Drain aguarda agent_done ─────────────────────────────────────────────

  describe("agent_logout + drain: instância preservada até agent_done", () => {
    it("instância permanece no Redis com estado 'draining' após logout com sessão ativa", async () => {
      const token = await _loginAndReadyToken()
      await server.callTool("agent_busy", { session_token: token, conversation_id: CONV_ID })

      // Logout com sessão ativa
      await server.callTool("agent_logout", { session_token: token })

      const instanceKey = keys.agentInstance(TENANT, INSTANCE_ID)
      // Instância AINDA existe no Redis — drain aguarda agent_done
      expect(await redis.hget(instanceKey, "state")).toBe("draining")
      // Mas foi removida dos pools de disponibilidade
      expect(await redis.sismember(keys.poolAvailable(TENANT, "retencao_humano"), INSTANCE_ID)).toBe(0)
    })

    it("instância é removida do Redis apenas APÓS agent_done durante drain", async () => {
      const token = await _loginAndReadyToken()
      await server.callTool("agent_busy", { session_token: token, conversation_id: CONV_ID })
      await server.callTool("agent_logout", { session_token: token })

      const instanceKey = keys.agentInstance(TENANT, INSTANCE_ID)
      // Antes do agent_done: instância ainda existe
      expect(await redis.hget(instanceKey, "state")).toBe("draining")

      // agent_done finaliza a sessão → instância removida
      await _done(token, CONV_ID, "resolved")
      expect(await redis.hget(instanceKey, "state")).toBeNull()
    })
  })

  // ── Fluxo completo ──────────────────────────────────────────────────────

  describe("fluxo completo: login → ready → busy → done", () => {
    it("ciclo de vida completo de um agente single-session", async () => {
      // Login
      const loginRes = await _login()
      expect(loginRes.isError).toBeFalsy()
      const token = _body(loginRes).session_token as string

      // Ready
      const readyRes = await server.callTool("agent_ready", { session_token: token })
      expect(readyRes.isError).toBeFalsy()
      expect(await redis.sismember(keys.poolAvailable(TENANT, "retencao_humano"), INSTANCE_ID)).toBe(1)

      // Busy
      const busyRes = await server.callTool("agent_busy", { session_token: token, conversation_id: CONV_ID })
      expect(busyRes.isError).toBeFalsy()
      expect(_body(busyRes).current_sessions).toBe(1)

      // Done
      const doneRes = await _done(token, CONV_ID, "resolved")
      expect(doneRes.isError).toBeFalsy()
      expect(_body(doneRes).acknowledged).toBe(true)

      // Logout
      const logoutRes = await server.callTool("agent_logout", { session_token: token })
      expect(logoutRes.isError).toBeFalsy()
      expect(_body(logoutRes).status).toBe("logged_out")

      // Verificar eventos Kafka em ordem
      const lifecycleEvents = kafka.events
        .filter(e => e.topic === "agent.lifecycle")
        .map(e => e.message["event"])
      expect(lifecycleEvents).toContain("agent_login")
      expect(lifecycleEvents).toContain("agent_ready")
      expect(lifecycleEvents).toContain("agent_busy")
      expect(lifecycleEvents).toContain("agent_done")
      expect(lifecycleEvents).toContain("agent_logout")
    })
  })

  // ─── Helpers ─────────────────────────────────────────────────────────────

  function _body(res: ToolResponse): Record<string, unknown> {
    return JSON.parse(res.content[0]!.text) as Record<string, unknown>
  }

  async function _login(): Promise<ToolResponse> {
    return server.callTool("agent_login", {
      agent_type_id: AGENT_TYPE_ID,
      instance_id:   INSTANCE_ID,
      tenant_id:     TENANT,
    })
  }

  async function _loginToken(): Promise<string> {
    const res = await _login()
    return _body(res).session_token as string
  }

  async function _loginAndReadyToken(): Promise<string> {
    const token = await _loginToken()
    await server.callTool("agent_ready", { session_token: token })
    return token
  }

  async function _done(
    sessionToken: string,
    conversationId: string,
    outcome: string,
    opts?: { handoff_reason?: string }
  ): Promise<ToolResponse> {
    return server.callTool("agent_done", {
      session_token:   sessionToken,
      conversation_id: conversationId,
      outcome,
      issue_status:    [{ issue_id: "i1", description: "atendimento concluído", status: "resolved" }],
      completed_at:    new Date().toISOString(),
      ...(opts?.handoff_reason ? { handoff_reason: opts.handoff_reason } : {}),
    })
  }
})
