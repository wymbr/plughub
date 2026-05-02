/**
 * integration/pools.integration.test.ts
 * Testes de integração das rotas de Pools com PostgreSQL real.
 * Spec: PlugHub v24.0 seção 4.5
 */

import { describe, it, expect, beforeEach, afterAll } from "vitest"
import request from "supertest"
import { app }           from "../../app"
import { createTestPrisma, truncateAll, TENANT, HEADERS, VALID_POOL } from "./helpers"

const prisma = createTestPrisma()

beforeEach(async () => {
  await truncateAll(prisma)
})

afterAll(async () => {
  await prisma.$disconnect()
})

// ─── POST /v1/pools ───────────────────────────────────────────────────────────

describe("POST /v1/pools", () => {
  it("cria pool com payload mínimo válido — retorna 201", async () => {
    const res = await request(app)
      .post("/v1/pools")
      .set(HEADERS)
      .send(VALID_POOL)

    expect(res.status).toBe(201)
    expect(res.body.pool_id).toBe("retencao_humano")
    expect(res.body.tenant_id).toBe(TENANT)
    expect(res.body.status).toBe("active")
    expect(res.body.sla_target_ms).toBe(480000)
    expect(res.body).not.toHaveProperty("id") // id interno não exposto
  })

  it("persiste no banco — pool recuperável via GET após criação", async () => {
    await request(app).post("/v1/pools").set(HEADERS).send(VALID_POOL)

    const inDb = await prisma.pool.findUnique({
      where: { pool_id_tenant_id: { pool_id: "retencao_humano", tenant_id: TENANT } },
    })
    expect(inDb).not.toBeNull()
    expect(inDb!.sla_target_ms).toBe(480000)
    expect(inDb!.channel_types).toEqual(["chat", "whatsapp", "voice"])
  })

  it("cria pool com routing_expression e supervisor_config — persiste JSON", async () => {
    const pool = {
      ...VALID_POOL,
      routing_expression:    { peso_sla: 1.0, peso_espera: 0.8, peso_tier: 0.6, peso_churn: 0.9, peso_negocio: 0.4 },
      evaluation_template_id: "template_retencao_v2",
      supervisor_config:     { enabled: true, history_window_days: 30, insight_categories: [], intent_capability_map: {} },
    }

    const res = await request(app).post("/v1/pools").set(HEADERS).send(pool)

    expect(res.status).toBe(201)
    expect(res.body.evaluation_template_id).toBe("template_retencao_v2")

    const inDb = await prisma.pool.findUnique({
      where: { pool_id_tenant_id: { pool_id: "retencao_humano", tenant_id: TENANT } },
    })
    expect(inDb!.routing_expression).toMatchObject({ peso_sla: 1.0 })
    expect(inDb!.supervisor_config).toMatchObject({ enabled: true })
  })

  it("retorna 409 quando pool_id já existe no mesmo tenant", async () => {
    await request(app).post("/v1/pools").set(HEADERS).send(VALID_POOL)

    const res = await request(app).post("/v1/pools").set(HEADERS).send(VALID_POOL)

    expect(res.status).toBe(409)
    expect(res.body.error).toMatch(/pool_id/)
  })

  it("pool_id é isolado por tenant — mesmo pool_id em tenant diferente é permitido", async () => {
    await request(app).post("/v1/pools").set(HEADERS).send(VALID_POOL)

    const res = await request(app)
      .post("/v1/pools")
      .set({ "x-tenant-id": "outro_tenant", "x-user-id": "user_test" })
      .send(VALID_POOL)

    expect(res.status).toBe(201)
  })

  it("retorna 422 quando channel_types está vazio", async () => {
    const res = await request(app)
      .post("/v1/pools")
      .set(HEADERS)
      .send({ ...VALID_POOL, channel_types: [] })

    expect(res.status).toBe(422)
  })

  it("retorna 422 quando channel inválido (telegram não é suportado)", async () => {
    const res = await request(app)
      .post("/v1/pools")
      .set(HEADERS)
      .send({ ...VALID_POOL, channel_types: ["telegram"] })

    expect(res.status).toBe(422)
  })

  it("retorna 422 quando sla_target_ms está ausente", async () => {
    const res = await request(app)
      .post("/v1/pools")
      .set(HEADERS)
      .send({ pool_id: "test_pool", channel_types: ["chat"] })

    expect(res.status).toBe(422)
  })

  it("retorna 422 quando pool_id não segue snake_case sem versão", async () => {
    const res = await request(app)
      .post("/v1/pools")
      .set(HEADERS)
      .send({ ...VALID_POOL, pool_id: "RetencaoHumano" })

    expect(res.status).toBe(422)
  })
})

// ─── GET /v1/pools ────────────────────────────────────────────────────────────

describe("GET /v1/pools", () => {
  it("retorna lista dos pools do tenant", async () => {
    await request(app).post("/v1/pools").set(HEADERS).send(VALID_POOL)
    await request(app).post("/v1/pools").set(HEADERS).send({
      ...VALID_POOL, pool_id: "suporte_tecnico"
    })

    const res = await request(app).get("/v1/pools").set(HEADERS)

    expect(res.status).toBe(200)
    expect(res.body.total).toBe(2)
    expect(res.body.pools).toHaveLength(2)
    const ids = res.body.pools.map((p: { pool_id: string }) => p.pool_id)
    expect(ids).toContain("retencao_humano")
    expect(ids).toContain("suporte_tecnico")
  })

  it("não retorna pools de outro tenant", async () => {
    await request(app).post("/v1/pools").set(HEADERS).send(VALID_POOL)
    await request(app)
      .post("/v1/pools")
      .set({ "x-tenant-id": "outro_tenant", "x-user-id": "u" })
      .send({ ...VALID_POOL, pool_id: "pool_outro_tenant" })

    const res = await request(app).get("/v1/pools").set(HEADERS)

    expect(res.body.total).toBe(1)
  })

  it("retorna lista vazia quando nenhum pool existe", async () => {
    const res = await request(app).get("/v1/pools").set(HEADERS)

    expect(res.status).toBe(200)
    expect(res.body.total).toBe(0)
    expect(res.body.pools).toHaveLength(0)
  })
})

// ─── GET /v1/pools/:pool_id ───────────────────────────────────────────────────

describe("GET /v1/pools/:pool_id", () => {
  it("retorna pool existente com todos os campos", async () => {
    await request(app).post("/v1/pools").set(HEADERS).send(VALID_POOL)

    const res = await request(app).get("/v1/pools/retencao_humano").set(HEADERS)

    expect(res.status).toBe(200)
    expect(res.body.pool_id).toBe("retencao_humano")
    expect(res.body.channel_types).toEqual(["chat", "whatsapp", "voice"])
    expect(res.body.sla_target_ms).toBe(480000)
  })

  it("retorna 404 quando pool não existe", async () => {
    const res = await request(app).get("/v1/pools/pool_inexistente").set(HEADERS)

    expect(res.status).toBe(404)
  })
})

// ─── PUT /v1/pools/:pool_id ───────────────────────────────────────────────────

describe("PUT /v1/pools/:pool_id", () => {
  it("atualiza sla_target_ms do pool", async () => {
    await request(app).post("/v1/pools").set(HEADERS).send(VALID_POOL)

    const res = await request(app)
      .put("/v1/pools/retencao_humano")
      .set(HEADERS)
      .send({ sla_target_ms: 300000 })

    expect(res.status).toBe(200)
    expect(res.body.sla_target_ms).toBe(300000)

    const inDb = await prisma.pool.findUnique({
      where: { pool_id_tenant_id: { pool_id: "retencao_humano", tenant_id: TENANT } },
    })
    expect(inDb!.sla_target_ms).toBe(300000)
  })

  it("retorna 404 ao atualizar pool inexistente", async () => {
    const res = await request(app)
      .put("/v1/pools/nao_existe")
      .set(HEADERS)
      .send({ sla_target_ms: 300000 })

    expect(res.status).toBe(404)
  })
})
