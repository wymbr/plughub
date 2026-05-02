/**
 * integration/instances.integration.test.ts
 * Testes de integração da rota GET /v1/instances.
 * Spec: PlugHub v24.0 seção 4.5 — ciclo de vida de instância
 *
 * Instâncias são criadas diretamente via Prisma (simula o mcp-server-plughub).
 */

import { describe, it, expect, beforeEach, afterAll } from "vitest"
import request from "supertest"
import { app }  from "../../app"
import {
  createTestPrisma, truncateAll,
  TENANT, HEADERS, VALID_POOL, VALID_AGENT_TYPE,
} from "./helpers"

const prisma = createTestPrisma()

// ─── Helpers locais ───────────────────────────────────────────────────────────

/** Cria instância diretamente no banco (simula agent_login do mcp-server). */
async function createInstance(params: {
  instance_id:   string
  agent_type_id?: string
  status?:        string
  current_sessions?: number
  session_token?: string
}): Promise<void> {
  await prisma.agentInstance.create({
    data: {
      instance_id:      params.instance_id,
      tenant_id:        TENANT,
      agent_type_id:    params.agent_type_id ?? "agente_retencao_v1",
      status:           (params.status ?? "ready") as never,
      current_sessions: params.current_sessions ?? 0,
      session_token:    params.session_token ?? `token_${params.instance_id}`,
    },
  })
}

// ─── Setup ────────────────────────────────────────────────────────────────────

beforeEach(async () => {
  await truncateAll(prisma)

  // Pré-condições
  await request(app).post("/v1/pools").set(HEADERS).send(VALID_POOL)
  await request(app).post("/v1/agent-types").set(HEADERS).send(VALID_AGENT_TYPE)
})

afterAll(async () => {
  await prisma.$disconnect()
})

// ─── GET /v1/instances ────────────────────────────────────────────────────────

describe("GET /v1/instances", () => {
  it("retorna lista vazia quando não há instâncias", async () => {
    const res = await request(app).get("/v1/instances").set(HEADERS)

    expect(res.status).toBe(200)
    expect(res.body.total).toBe(0)
    expect(res.body.instances).toHaveLength(0)
  })

  it("retorna instâncias do tenant com campos do agent_type", async () => {
    await createInstance({ instance_id: "inst_001", status: "ready" })
    await createInstance({ instance_id: "inst_002", status: "busy", current_sessions: 1 })

    const res = await request(app).get("/v1/instances").set(HEADERS)

    expect(res.status).toBe(200)
    expect(res.body.total).toBe(2)
    expect(res.body.instances[0]).toHaveProperty("instance_id")
    expect(res.body.instances[0]).toHaveProperty("status")
    expect(res.body.instances[0]).toHaveProperty("agent_type")
    expect(res.body.instances[0].agent_type.agent_type_id).toBe("agente_retencao_v1")
    expect(res.body.instances[0].agent_type.traffic_weight).toBe(1.0)
    // session_token não exposto
    expect(res.body.instances[0]).not.toHaveProperty("session_token")
  })

  it("não retorna instâncias de outro tenant", async () => {
    await createInstance({ instance_id: "inst_001" })

    // Instância de outro tenant
    await prisma.agentInstance.create({
      data: {
        instance_id:   "inst_outro",
        tenant_id:     "outro_tenant",
        agent_type_id: "agente_retencao_v1",
        status:        "ready" as never,
        session_token: "token_outro",
      },
    })

    const res = await request(app).get("/v1/instances").set(HEADERS)

    expect(res.body.total).toBe(1)
    expect(res.body.instances[0].instance_id).toBe("inst_001")
  })

  it("filtra por status=ready", async () => {
    await createInstance({ instance_id: "inst_ready", status: "ready" })
    await createInstance({ instance_id: "inst_busy",  status: "busy"  })
    await createInstance({ instance_id: "inst_paused", status: "paused" })

    const res = await request(app)
      .get("/v1/instances")
      .query({ status: "ready" })
      .set(HEADERS)

    expect(res.body.total).toBe(1)
    expect(res.body.instances[0].instance_id).toBe("inst_ready")
    expect(res.body.instances[0].status).toBe("ready")
  })

  it("filtra por status=busy e retorna múltiplos", async () => {
    await createInstance({ instance_id: "inst_busy_1", status: "busy", current_sessions: 1 })
    await createInstance({ instance_id: "inst_busy_2", status: "busy", current_sessions: 2 })
    await createInstance({ instance_id: "inst_ready",  status: "ready" })

    const res = await request(app)
      .get("/v1/instances")
      .query({ status: "busy" })
      .set(HEADERS)

    expect(res.body.total).toBe(2)
    expect(res.body.instances.every((i: { status: string }) => i.status === "busy")).toBe(true)
  })

  it("filtra por pool_id — retorna apenas instâncias cujo agent type participa do pool", async () => {
    // Pool e agent type adicional
    await request(app)
      .post("/v1/pools")
      .set(HEADERS)
      .send({ pool_id: "suporte_tecnico", channel_types: ["chat"], sla_target_ms: 300000 })
    await request(app)
      .post("/v1/agent-types")
      .set(HEADERS)
      .send({ ...VALID_AGENT_TYPE, agent_type_id: "agente_suporte_v1", pools: ["suporte_tecnico"] })

    await createInstance({ instance_id: "inst_retencao", agent_type_id: "agente_retencao_v1" })
    await createInstance({ instance_id: "inst_suporte",  agent_type_id: "agente_suporte_v1" })

    const res = await request(app)
      .get("/v1/instances")
      .query({ pool_id: "retencao_humano" })
      .set(HEADERS)

    expect(res.body.total).toBe(1)
    expect(res.body.instances[0].instance_id).toBe("inst_retencao")
  })

  it("combina filtro status + pool_id", async () => {
    await createInstance({ instance_id: "inst_ret_ready",  agent_type_id: "agente_retencao_v1", status: "ready" })
    await createInstance({ instance_id: "inst_ret_busy",   agent_type_id: "agente_retencao_v1", status: "busy"  })

    const res = await request(app)
      .get("/v1/instances")
      .query({ status: "ready", pool_id: "retencao_humano" })
      .set(HEADERS)

    expect(res.body.total).toBe(1)
    expect(res.body.instances[0].instance_id).toBe("inst_ret_ready")
  })

  it("retorna 422 para status inválido", async () => {
    const res = await request(app)
      .get("/v1/instances")
      .query({ status: "flying" })
      .set(HEADERS)

    expect(res.status).toBe(422)
    expect(res.body.error).toBe("validation_error")
  })

  it("paginação: limit e page funcionam corretamente", async () => {
    for (let i = 1; i <= 5; i++) {
      await createInstance({ instance_id: `inst_${i}`, status: "ready" })
    }

    const res1 = await request(app)
      .get("/v1/instances")
      .query({ limit: 3, page: 1 })
      .set(HEADERS)

    expect(res1.body.instances).toHaveLength(3)
    expect(res1.body.total).toBe(5)
    expect(res1.body.page).toBe(1)

    const res2 = await request(app)
      .get("/v1/instances")
      .query({ limit: 3, page: 2 })
      .set(HEADERS)

    expect(res2.body.instances).toHaveLength(2)
    expect(res2.body.page).toBe(2)
  })

  it("reflete traffic_weight atualizado pelo PATCH /canary", async () => {
    await createInstance({ instance_id: "inst_canary", status: "ready" })

    await request(app)
      .patch("/v1/agent-types/agente_retencao_v1/canary")
      .set(HEADERS)
      .send({ traffic_weight: 0.20 })

    const res = await request(app).get("/v1/instances").set(HEADERS)

    expect(res.body.instances[0].agent_type.traffic_weight).toBe(0.20)
  })
})
