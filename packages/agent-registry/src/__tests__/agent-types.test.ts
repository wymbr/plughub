/**
 * agent-types.test.ts
 * Testes das rotas de tipos de agente — validações cruzadas.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import request from "supertest"
import { app }  from "../app"

vi.mock("../db", () => ({
  prisma: {
    pool:       { findUnique: vi.fn(), findMany: vi.fn() },
    skill:      { findUnique: vi.fn(), findMany: vi.fn() },
    agentType:  { findUnique: vi.fn(), findMany: vi.fn(), create: vi.fn() },
  },
  Prisma: { DbNull: null },
}))

import { prisma } from "../db"

const headers = { "x-tenant-id": "tenant_test", "x-user-id": "user_001" }

const dbPool = { id: "pool-uuid-001", pool_id: "retencao_humano", tenant_id: "tenant_test" }
const dbSkill = { id: "skill-uuid-001", skill_id: "skill_portabilidade_telco_v2", tenant_id: "tenant_test" }

const validAgentType = {
  agent_type_id:   "agente_retencao_v1",
  framework:       "langgraph",
  execution_model: "stateless",
  pools:           ["retencao_humano"],
}

beforeEach(() => { vi.clearAllMocks() })

describe("POST /v1/agent-types", () => {
  it("cria agente válido sem skills", async () => {
    vi.mocked(prisma.pool.findMany).mockResolvedValue([dbPool] as never)
    vi.mocked(prisma.agentType.findUnique).mockResolvedValue(null)
    vi.mocked(prisma.agentType.create).mockResolvedValue({
      ...validAgentType, tenant_id: "tenant_test", pools: []
    } as never)

    const res = await request(app)
      .post("/v1/agent-types")
      .set(headers)
      .send(validAgentType)

    expect(res.status).toBe(201)
  })

  it("retorna 422 quando pool não existe no tenant", async () => {
    vi.mocked(prisma.pool.findMany).mockResolvedValue([])  // pool não encontrado

    const res = await request(app)
      .post("/v1/agent-types")
      .set(headers)
      .send(validAgentType)

    expect(res.status).toBe(422)
    expect(res.body.error).toBe("pools_not_found")
    expect(res.body.missing).toContain("retencao_humano")
  })

  it("retorna 422 quando skill_id não existe no tenant", async () => {
    vi.mocked(prisma.pool.findMany).mockResolvedValue([dbPool] as never)
    vi.mocked(prisma.skill.findMany).mockResolvedValue([])  // skill não encontrada

    const res = await request(app)
      .post("/v1/agent-types")
      .set(headers)
      .send({
        ...validAgentType,
        skills: [{ skill_id: "skill_inexistente_v1", version_policy: "stable" }],
      })

    expect(res.status).toBe(422)
    expect(res.body.error).toBe("skills_not_found")
    expect(res.body.missing).toContain("skill_inexistente_v1")
  })

  it("retorna 409 quando agent_type_id já existe", async () => {
    vi.mocked(prisma.pool.findMany).mockResolvedValue([dbPool] as never)
    vi.mocked(prisma.skill.findMany).mockResolvedValue([])
    vi.mocked(prisma.agentType.findUnique).mockResolvedValue({ id: "existing" } as never)

    const res = await request(app)
      .post("/v1/agent-types")
      .set(headers)
      .send(validAgentType)

    expect(res.status).toBe(409)
  })

  it("cria agente com skill válida", async () => {
    vi.mocked(prisma.pool.findMany).mockResolvedValue([dbPool] as never)
    vi.mocked(prisma.skill.findMany).mockResolvedValue([dbSkill] as never)
    vi.mocked(prisma.agentType.findUnique).mockResolvedValue(null)
    vi.mocked(prisma.agentType.create).mockResolvedValue({
      ...validAgentType, tenant_id: "tenant_test", pools: []
    } as never)

    const res = await request(app)
      .post("/v1/agent-types")
      .set(headers)
      .send({
        ...validAgentType,
        skills: [{ skill_id: "skill_portabilidade_telco_v2", version_policy: "stable" }],
      })

    expect(res.status).toBe(201)
  })
})
