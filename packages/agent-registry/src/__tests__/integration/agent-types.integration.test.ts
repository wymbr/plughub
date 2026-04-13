/**
 * integration/agent-types.integration.test.ts
 * Testes de integração das rotas de Agent Types com PostgreSQL real.
 * Spec: PlugHub v24.0 seção 4.5
 */

import { describe, it, expect, beforeEach, afterAll } from "vitest"
import request from "supertest"
import { app }  from "../../app"
import {
  createTestPrisma, truncateAll,
  TENANT, HEADERS, VALID_POOL, VALID_AGENT_TYPE,
} from "./helpers"

const prisma = createTestPrisma()

beforeEach(async () => {
  await truncateAll(prisma)
  // Pré-condicão: pool deve existir para registrar agent type
  await request(app).post("/v1/pools").set(HEADERS).send(VALID_POOL)
})

afterAll(async () => {
  await prisma.$disconnect()
})

// ─── POST /v1/agent-types ─────────────────────────────────────────────────────

describe("POST /v1/agent-types", () => {
  it("cria agent type válido e retorna 201", async () => {
    const res = await request(app)
      .post("/v1/agent-types")
      .set(HEADERS)
      .send(VALID_AGENT_TYPE)

    expect(res.status).toBe(201)
    expect(res.body.agent_type_id).toBe("agente_retencao_v1")
    expect(res.body.tenant_id).toBe(TENANT)
    expect(res.body.framework).toBe("langgraph")
    expect(res.body.traffic_weight).toBe(1.0)
    expect(res.body).not.toHaveProperty("id")
  })

  it("persiste no banco com relação ao pool", async () => {
    await request(app).post("/v1/agent-types").set(HEADERS).send(VALID_AGENT_TYPE)

    const inDb = await prisma.agentType.findUnique({
      where:   { agent_type_id_tenant_id: { agent_type_id: "agente_retencao_v1", tenant_id: TENANT } },
      include: { pools: { include: { pool: true } } },
    })
    expect(inDb).not.toBeNull()
    expect(inDb!.framework).toBe("langgraph")
    expect(inDb!.traffic_weight).toBe(1.0)
    expect(inDb!.pools).toHaveLength(1)
    expect(inDb!.pools[0]!.pool.pool_id).toBe("retencao_humano")
  })

  it("retorna 422 quando pool[] não existe no banco", async () => {
    const res = await request(app)
      .post("/v1/agent-types")
      .set(HEADERS)
      .send({ ...VALID_AGENT_TYPE, pools: ["pool_inexistente"] })

    expect(res.status).toBe(422)
    expect(res.body.error).toBe("pools_not_found")
    expect(res.body.missing).toContain("pool_inexistente")
  })

  it("retorna 422 quando um dos pools[] não existe (parcial)", async () => {
    const res = await request(app)
      .post("/v1/agent-types")
      .set(HEADERS)
      .send({ ...VALID_AGENT_TYPE, pools: ["retencao_humano", "pool_que_nao_existe"] })

    expect(res.status).toBe(422)
    expect(res.body.missing).toContain("pool_que_nao_existe")
    expect(res.body.missing).not.toContain("retencao_humano")
  })

  it("retorna 409 quando agent_type_id já existe no tenant", async () => {
    await request(app).post("/v1/agent-types").set(HEADERS).send(VALID_AGENT_TYPE)

    const res = await request(app)
      .post("/v1/agent-types")
      .set(HEADERS)
      .send(VALID_AGENT_TYPE)

    expect(res.status).toBe(409)
  })

  it("agent_type_id é isolado por tenant — mesmo id em tenant diferente é permitido", async () => {
    // Cria pool no segundo tenant
    await request(app)
      .post("/v1/pools")
      .set({ "x-tenant-id": "outro_tenant", "x-user-id": "u" })
      .send(VALID_POOL)

    await request(app).post("/v1/agent-types").set(HEADERS).send(VALID_AGENT_TYPE)

    const res = await request(app)
      .post("/v1/agent-types")
      .set({ "x-tenant-id": "outro_tenant", "x-user-id": "u" })
      .send(VALID_AGENT_TYPE)

    expect(res.status).toBe(201)
  })

  it("registra agent type com múltiplos pools", async () => {
    await request(app)
      .post("/v1/pools")
      .set(HEADERS)
      .send({ pool_id: "suporte_tecnico", channel_types: ["chat"], sla_target_ms: 300000 })

    const res = await request(app)
      .post("/v1/agent-types")
      .set(HEADERS)
      .send({ ...VALID_AGENT_TYPE, pools: ["retencao_humano", "suporte_tecnico"] })

    expect(res.status).toBe(201)

    const inDb = await prisma.agentType.findUnique({
      where:   { agent_type_id_tenant_id: { agent_type_id: "agente_retencao_v1", tenant_id: TENANT } },
      include: { pools: { include: { pool: true } } },
    })
    expect(inDb!.pools).toHaveLength(2)
  })

  it("retorna 422 quando agent_type_id não segue formato {nome}_v{n}", async () => {
    const res = await request(app)
      .post("/v1/agent-types")
      .set(HEADERS)
      .send({ ...VALID_AGENT_TYPE, agent_type_id: "agente_retencao" })

    expect(res.status).toBe(422)
  })

  it("registra agent type com skills válidas", async () => {
    // Pré-condição: criar a skill
    await request(app).post("/v1/skills").set(HEADERS).send({
      skill_id:       "skill_portabilidade_telco_v2",
      name:           "Portabilidade Telco",
      version:        "2.0.0",
      description:    "Verifica portabilidade",
      classification: { type: "executor" },
      instruction:    { prompt_id: "prompt_portabilidade_v1" },
    })

    const res = await request(app)
      .post("/v1/agent-types")
      .set(HEADERS)
      .send({
        ...VALID_AGENT_TYPE,
        skills: [{ skill_id: "skill_portabilidade_telco_v2", version_policy: "stable" }],
      })

    expect(res.status).toBe(201)
  })

  it("retorna 422 quando skill_id não existe no tenant", async () => {
    const res = await request(app)
      .post("/v1/agent-types")
      .set(HEADERS)
      .send({
        ...VALID_AGENT_TYPE,
        skills: [{ skill_id: "skill_inexistente_v1", version_policy: "stable" }],
      })

    expect(res.status).toBe(422)
    expect(res.body.error).toBe("skills_not_found")
  })
})

// ─── GET /v1/agent-types ─────────────────────────────────────────────────────

describe("GET /v1/agent-types", () => {
  it("retorna lista de agent types do tenant", async () => {
    await request(app).post("/v1/agent-types").set(HEADERS).send(VALID_AGENT_TYPE)

    const res = await request(app).get("/v1/agent-types").set(HEADERS)

    expect(res.status).toBe(200)
    expect(res.body.total).toBe(1)
    expect(res.body.agent_types[0].agent_type_id).toBe("agente_retencao_v1")
  })

  it("filtra por pool_id", async () => {
    await request(app).post("/v1/agent-types").set(HEADERS).send(VALID_AGENT_TYPE)
    await request(app)
      .post("/v1/pools")
      .set(HEADERS)
      .send({ pool_id: "outro_pool", channel_types: ["chat"], sla_target_ms: 60000 })
    await request(app)
      .post("/v1/agent-types")
      .set(HEADERS)
      .send({ ...VALID_AGENT_TYPE, agent_type_id: "agente_outro_v1", pools: ["outro_pool"] })

    const res = await request(app)
      .get("/v1/agent-types")
      .query({ pool_id: "retencao_humano" })
      .set(HEADERS)

    expect(res.body.total).toBe(1)
    expect(res.body.agent_types[0].agent_type_id).toBe("agente_retencao_v1")
  })
})

// ─── GET /v1/agent-types/:agent_type_id ──────────────────────────────────────

describe("GET /v1/agent-types/:agent_type_id", () => {
  it("retorna agent type com traffic_weight padrão (1.0)", async () => {
    await request(app).post("/v1/agent-types").set(HEADERS).send(VALID_AGENT_TYPE)

    const res = await request(app)
      .get("/v1/agent-types/agente_retencao_v1")
      .set(HEADERS)

    expect(res.status).toBe(200)
    expect(res.body.agent_type_id).toBe("agente_retencao_v1")
    expect(res.body.traffic_weight).toBe(1.0)
  })

  it("retorna 404 quando agent type não existe", async () => {
    const res = await request(app)
      .get("/v1/agent-types/agente_inexistente_v1")
      .set(HEADERS)

    expect(res.status).toBe(404)
  })
})
