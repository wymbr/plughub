/**
 * operational.test.ts
 * Tools do grupo `operational` — os três que decidem **oferta de canal ao cliente**.
 *
 * POR QUE ESTE ARQUIVO EXISTE (2026-08-21): medido antes de escrever, nenhum skill
 * do repositório chama estes tools, e a tabela `mcp_audit_log` não existe neste
 * ambiente — logo não há tráfego que denuncie uma regressão aqui, e não há como
 * medir quem sofre. Um conserto sem consumidor é conserto de CONTRATO, e contrato
 * sem teste volta calado no primeiro refactor.
 *
 * A pergunta que todos os casos abaixo fazem é uma só: **uma abstenção do produtor
 * ("não sei") sobrevive até o consumidor, ou vira "não há"?**
 *
 * Redis in-memory (ioredis-mock). Sem infra externa.
 */

import { describe, it, expect, beforeEach } from "vitest"
import RedisMock from "ioredis-mock"
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import { registerOperationalTools } from "../tools/operational"
import { keys } from "../infra/redis"

const TENANT = "tenant_test"
const POOL   = "retencao_humano"

type ToolResponse = { isError?: boolean; content: Array<{ type: string; text: string }> }
type TestServer   = { callTool: (name: string, input: unknown) => Promise<ToolResponse> }

function makeTestServer(mcpServer: McpServer): TestServer {
  return {
    callTool: async (name, input) => {
      const reg = (mcpServer as unknown as Record<string, Record<string, { handler: (i: unknown) => Promise<ToolResponse> }>>)
        ._registeredTools?.[name]
      if (!reg) throw new Error(`Tool '${name}' not registered`)
      return reg.handler(input)
    },
  }
}

const _body = (r: ToolResponse) => JSON.parse(r.content[0]!.text) as Record<string, any>

describe("tools operacionais — a abstenção do produtor sobrevive ao consumidor?", () => {
  let redis: InstanceType<typeof RedisMock>
  let server: TestServer

  beforeEach(() => {
    redis = new RedisMock()
    const mcpServer = new McpServer({ name: "test", version: "0.0.1" })
    registerOperationalTools(mcpServer, { redis })
    server = makeTestServer(mcpServer)
    // Todo pool medido precisa ser membro do tenant (system_availability_check
    // enumera a partir daqui; sem isto ele devolve "No pools found" e os casos
    // passariam sem exercer nada).
    return redis.sadd(`${TENANT}:pools`, POOL)
  })

  async function seedSnapshot(extra: Record<string, unknown>) {
    await redis.set(keys.poolQueueSnapshot(TENANT, POOL), JSON.stringify({
      pool_id: POOL, tenant_id: TENANT,
      queue_length: 0, sla_target_ms: 480_000, channel_types: ["webchat"],
      updated_at: new Date().toISOString(),
      ...extra,
    }))
  }

  // ── pool_status_get ───────────────────────────────────────────────────────

  describe("pool_status_get", () => {
    it("CONTROLE: linha que AFIRMA capacidade continua respondendo o número", async () => {
      // Sem esta metade, uma implementação que devolvesse `unknown` sempre
      // passaria nos casos de abstenção abaixo sem provar nada.
      await seedSnapshot({ available: 3, busy: 0, model: "resource_semaphore" })
      const b = _body(await server.callTool("pool_status_get", { tenant_id: TENANT, pool_id: POOL }))
      expect(b.available).toBe(3)
      expect(b.status).toBe("available")
      expect(b.reason).toBeNull()
    })

    it("linha SEM o campo `available` responde 'unknown', não 'empty'", async () => {
      // O bootstrap OMITE `available` quando só saberia somar uma parcela
      // (`capacity_unknown: "unmanaged_members"`). Em TS `undefined > 0` é false,
      // então a omissão caía em `queued`/`empty` — publicando "não há agente" a
      // partir de um "não sei", no tool que decide oferta de canal ao cliente.
      await seedSnapshot({
        model: "bootstrap_placeholder", capacity_unknown: "unmanaged_members",
      })
      const b = _body(await server.callTool("pool_status_get", { tenant_id: TENANT, pool_id: POOL }))
      expect(b.available).toBeNull()
      expect(b.status).toBe("unknown")
      expect(String(b.reason)).toContain("unmanaged_members")
    })

    it("`available: 0` é uma AFIRMAÇÃO e continua distinta de 'unknown'", async () => {
      // A regressão simétrica: tratar zero como ausência apagaria a única forma
      // de o produtor dizer "medi, e não há vaga".
      await seedSnapshot({ available: 0, model: "resource_semaphore" })
      const b = _body(await server.callTool("pool_status_get", { tenant_id: TENANT, pool_id: POOL }))
      expect(b.available).toBe(0)
      expect(b.status).toBe("empty")
    })

    it("distingue 'não há agente' de 'há agente, PAUSADO'", async () => {
      // `available: 0` sozinho colapsa duas decisões de produto diferentes:
      // desviar o cliente de canal × informar espera. `paused_capacity` as separa.
      await seedSnapshot({
        available: 0, busy: 1, paused_capacity: 2, total_instances: 3,
        model: "resource_semaphore",
      })
      const b = _body(await server.callTool("pool_status_get", { tenant_id: TENANT, pool_id: POOL }))
      expect(b.available).toBe(0)
      expect(b.paused_capacity).toBe(2)
    })
  })

  // ── queue_context_get ─────────────────────────────────────────────────────

  describe("queue_context_get", () => {
    it("`available_agents` é null quando a linha se abstém — nunca 0", async () => {
      await seedSnapshot({ model: "bootstrap_placeholder", capacity_unknown: "unmanaged_members" })
      const b = _body(await server.callTool("queue_context_get", {
        tenant_id: TENANT, pool_id: POOL, session_id: "sess_x",
      }))
      expect(b.available_agents).toBeNull()
      expect(b.capacity_unknown).toBe("unmanaged_members")
    })

    it("CONTROLE: com capacidade afirmada, devolve o número", async () => {
      await seedSnapshot({ available: 2, model: "resource_semaphore" })
      const b = _body(await server.callTool("queue_context_get", {
        tenant_id: TENANT, pool_id: POOL, session_id: "sess_x",
      }))
      expect(b.available_agents).toBe(2)
      expect(b.capacity_unknown).toBeNull()
    })
  })

  // ── system_availability_check ─────────────────────────────────────────────

  describe("system_availability_check", () => {
    it("SEM o rollup do tenant, o veredicto é 'unknown' — nunca 'available'", async () => {
      // O ramo que este caso fixa dizia `pools_available > 0 ? "available" :
      // "unknown"`, contra o comentário três linhas acima dele: *"nunca cair de
      // volta na soma das linhas: a soma é o defeito, não o fallback dele"*.
      // A linha do pool AFIRMA 3 vagas de propósito: é o valor que faria a versão
      // antiga responder "available" sem consultar a fonte deduplicada.
      await seedSnapshot({ available: 3, model: "resource_semaphore" })
      const b = _body(await server.callTool("system_availability_check", { tenant_id: TENANT }))
      expect(b.channels.webchat.capacity_unknown).toBe(true)
      expect(b.channels.webchat.available_by_kind).toBeNull()
      expect(b.channels.webchat.pools_available).toBe(1)   // segue como DADO
      expect(b.channels.webchat.status).toBe("unknown")    // mas não como sentença
    })

    it("CONTROLE: COM o rollup, o veredicto volta a ser afirmativo", async () => {
      await seedSnapshot({ available: 3, model: "resource_semaphore" })
      await redis.set(keys.tenantCapacity(TENANT), JSON.stringify({
        by_kind: { human: { total_capacity: 3, used: 0, available: 3, instances: 1,
                            by_channel: { webchat: { available: 3, instances: 1, pools_available: 1 } } } },
        computed_at: new Date().toISOString(),
      }))
      const b = _body(await server.callTool("system_availability_check", { tenant_id: TENANT }))
      expect(b.channels.webchat.capacity_unknown).toBe(false)
      expect(b.channels.webchat.status).toBe("available")
      expect(b.channels.webchat.available_by_kind).toEqual({ human: 3 })
    })

    it("pool que se abstém NÃO conta como pool sem vaga — vai para balde próprio", async () => {
      await seedSnapshot({ model: "bootstrap_placeholder", capacity_unknown: "unmanaged_members" })
      const b = _body(await server.callTool("system_availability_check", { tenant_id: TENANT }))
      expect(b.channels.webchat.pools_available).toBe(0)
      expect(b.channels.webchat.pools_capacity_unknown).toBe(1)
    })
  })
})
