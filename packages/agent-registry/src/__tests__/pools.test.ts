/**
 * pools.test.ts
 * Testes das rotas de pools — validação e CRUD.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import request from "supertest"
import { app }  from "../app"

// Mock do Kafka — evita conexão real a localhost:9092 nos unit tests
vi.mock("../infra/kafka", () => ({
  publishRegistryEvent: vi.fn().mockResolvedValue(undefined),
  disconnectKafka:      vi.fn().mockResolvedValue(undefined),
}))

// Mock do Prisma — inclui Prisma.DbNull usado nos campos JSON opcionais
vi.mock("../db", () => ({
  prisma: {
    pool: {
      findUnique: vi.fn(),
      findMany:   vi.fn(),
      create:     vi.fn(),
      update:     vi.fn(),
    },
  },
  Prisma: { DbNull: null },
}))

import { prisma } from "../db"

const validPool = {
  pool_id:       "retencao_humano",
  channel_types: ["webchat", "whatsapp"],
  sla_target_ms: 480000,
}

const dbPool = {
  pool_id:               "retencao_humano",
  tenant_id:             "tenant_test",
  status:                "active",
  channel_types:         ["webchat", "whatsapp"],
  sla_target_ms:         480000,
  description:           null,
  routing_expression:    null,
  evaluation_template_id: null,
  supervisor_config:     null,
  created_at:            new Date().toISOString(),
  updated_at:            new Date().toISOString(),
  created_by:            "system",
}

const headers = { "x-tenant-id": "tenant_test", "x-user-id": "user_001" }

beforeEach(() => { vi.clearAllMocks() })

describe("POST /v1/pools", () => {
  it("cria pool válido e retorna 201", async () => {
    vi.mocked(prisma.pool.findUnique).mockResolvedValue(null)
    vi.mocked(prisma.pool.create).mockResolvedValue(dbPool as never)

    const res = await request(app)
      .post("/v1/pools")
      .set(headers)
      .send(validPool)

    expect(res.status).toBe(201)
    expect(res.body.pool_id).toBe("retencao_humano")
  })

  it("retorna 409 quando pool_id já existe", async () => {
    vi.mocked(prisma.pool.findUnique).mockResolvedValue(dbPool as never)

    const res = await request(app)
      .post("/v1/pools")
      .set(headers)
      .send(validPool)

    expect(res.status).toBe(409)
  })

  it("retorna 422 quando channel_types está vazio", async () => {
    const res = await request(app)
      .post("/v1/pools")
      .set(headers)
      .send({ ...validPool, channel_types: [] })

    expect(res.status).toBe(422)
  })

  it("retorna 422 quando sla_target_ms está ausente", async () => {
    const res = await request(app)
      .post("/v1/pools")
      .set(headers)
      .send({ pool_id: "test", channel_types: ["webchat"] })

    expect(res.status).toBe(422)
  })

  it("retorna 422 quando channel inválido", async () => {
    // "chat" era o enum antigo — inválido no schema v2 (use "webchat")
    const res = await request(app)
      .post("/v1/pools")
      .set(headers)
      .send({ ...validPool, channel_types: ["chat"] })

    expect(res.status).toBe(422)
  })
})

describe("GET /v1/pools", () => {
  it("retorna lista de pools do tenant", async () => {
    vi.mocked(prisma.pool.findMany).mockResolvedValue([dbPool] as never)

    const res = await request(app)
      .get("/v1/pools")
      .set(headers)

    expect(res.status).toBe(200)
    expect(res.body.pools).toHaveLength(1)
    expect(res.body.total).toBe(1)
  })
})

describe("GET /v1/pools/:pool_id", () => {
  it("retorna pool existente", async () => {
    vi.mocked(prisma.pool.findUnique).mockResolvedValue(dbPool as never)

    const res = await request(app)
      .get("/v1/pools/retencao_humano")
      .set(headers)

    expect(res.status).toBe(200)
    expect(res.body.pool_id).toBe("retencao_humano")
  })

  it("retorna 404 quando pool não existe", async () => {
    vi.mocked(prisma.pool.findUnique).mockResolvedValue(null)

    const res = await request(app)
      .get("/v1/pools/nao_existe")
      .set(headers)

    expect(res.status).toBe(404)
  })
})
