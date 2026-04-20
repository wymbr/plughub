/**
 * integration/canary.integration.test.ts
 * Testes de integração do processo de canário.
 * Spec: PlugHub v24.0 seção 4.5
 *
 * Progressão esperada: 0.10 → 0.20 → 0.50 → 1.00
 * Rollback: restaura versão anterior para weight 1.0, arquiva atual.
 */

import { describe, it, expect, beforeEach, afterAll } from "vitest"
import request from "supertest"
import { app }  from "../../app"
import {
  createTestPrisma, truncateAll,
  TENANT, HEADERS, VALID_POOL, VALID_AGENT_TYPE,
} from "./helpers"

const prisma = createTestPrisma()

// ─── Setup ────────────────────────────────────────────────────────────────────

beforeEach(async () => {
  await truncateAll(prisma)
  await request(app).post("/v1/pools").set(HEADERS).send(VALID_POOL)
})

afterAll(async () => {
  await prisma.$disconnect()
})

// ─── PATCH /v1/agent-types/:id/canary ────────────────────────────────────────

describe("PATCH /v1/agent-types/:id/canary", () => {
  it("ajusta traffic_weight para 0.10 (início do canário)", async () => {
    await request(app).post("/v1/agent-types").set(HEADERS).send(VALID_AGENT_TYPE)

    const res = await request(app)
      .patch("/v1/agent-types/agente_retencao_v1/canary")
      .set(HEADERS)
      .send({ traffic_weight: 0.10 })

    expect(res.status).toBe(200)
    expect(res.body.traffic_weight).toBe(0.10)
    expect(res.body.canary.progression_target).toBe(0.20)
  })

  it("progressão: 0.10 → 0.20 → 0.50 → 1.00", async () => {
    await request(app).post("/v1/agent-types").set(HEADERS).send(VALID_AGENT_TYPE)

    const steps = [
      { weight: 0.10, next: 0.20 },
      { weight: 0.20, next: 0.50 },
      { weight: 0.50, next: 1.00 },
      { weight: 1.00, next: null },
    ]

    for (const step of steps) {
      const res = await request(app)
        .patch("/v1/agent-types/agente_retencao_v1/canary")
        .set(HEADERS)
        .send({ traffic_weight: step.weight })

      expect(res.status).toBe(200)
      expect(res.body.traffic_weight).toBe(step.weight)
      expect(res.body.canary.progression_target).toBe(step.next)
    }

    // Verifica persistência final no banco
    const inDb = await prisma.agentType.findUnique({
      where: { agent_type_id_tenant_id: { agent_type_id: "agente_retencao_v1", tenant_id: TENANT } },
    })
    expect(inDb!.traffic_weight).toBe(1.0)
  })

  it("persiste traffic_weight no banco", async () => {
    await request(app).post("/v1/agent-types").set(HEADERS).send(VALID_AGENT_TYPE)

    await request(app)
      .patch("/v1/agent-types/agente_retencao_v1/canary")
      .set(HEADERS)
      .send({ traffic_weight: 0.50 })

    const inDb = await prisma.agentType.findUnique({
      where: { agent_type_id_tenant_id: { agent_type_id: "agente_retencao_v1", tenant_id: TENANT } },
    })
    expect(inDb!.traffic_weight).toBe(0.50)
  })

  it("retorna 422 quando traffic_weight > 1.0", async () => {
    await request(app).post("/v1/agent-types").set(HEADERS).send(VALID_AGENT_TYPE)

    const res = await request(app)
      .patch("/v1/agent-types/agente_retencao_v1/canary")
      .set(HEADERS)
      .send({ traffic_weight: 1.5 })

    expect(res.status).toBe(422)
  })

  it("retorna 422 quando traffic_weight < 0.0", async () => {
    await request(app).post("/v1/agent-types").set(HEADERS).send(VALID_AGENT_TYPE)

    const res = await request(app)
      .patch("/v1/agent-types/agente_retencao_v1/canary")
      .set(HEADERS)
      .send({ traffic_weight: -0.1 })

    expect(res.status).toBe(422)
  })

  it("retorna 422 quando traffic_weight está ausente", async () => {
    await request(app).post("/v1/agent-types").set(HEADERS).send(VALID_AGENT_TYPE)

    const res = await request(app)
      .patch("/v1/agent-types/agente_retencao_v1/canary")
      .set(HEADERS)
      .send({})

    expect(res.status).toBe(422)
  })

  it("retorna 404 quando agent_type_id não existe", async () => {
    const res = await request(app)
      .patch("/v1/agent-types/agente_inexistente_v1/canary")
      .set(HEADERS)
      .send({ traffic_weight: 0.20 })

    expect(res.status).toBe(404)
  })

  it("aceita traffic_weight = 0.0 (congelamento de tráfego)", async () => {
    await request(app).post("/v1/agent-types").set(HEADERS).send(VALID_AGENT_TYPE)

    const res = await request(app)
      .patch("/v1/agent-types/agente_retencao_v1/canary")
      .set(HEADERS)
      .send({ traffic_weight: 0.0 })

    expect(res.status).toBe(200)
    expect(res.body.traffic_weight).toBe(0.0)
  })
})

// ─── DELETE /v1/agent-types/:id/canary ───────────────────────────────────────

describe("DELETE /v1/agent-types/:id/canary — rollback", () => {
  it("restaura versão anterior (v1) para weight 1.0 e arquiva v2", async () => {
    // v1 em produção (weight 1.0)
    await request(app)
      .post("/v1/agent-types")
      .set(HEADERS)
      .send({ ...VALID_AGENT_TYPE, agent_type_id: "agente_retencao_v1" })

    // v2 em canário (weight 0.10)
    await request(app)
      .post("/v1/agent-types")
      .set(HEADERS)
      .send({ ...VALID_AGENT_TYPE, agent_type_id: "agente_retencao_v2" })
    await request(app)
      .patch("/v1/agent-types/agente_retencao_v2/canary")
      .set(HEADERS)
      .send({ traffic_weight: 0.10 })

    // Rollback da v2
    const res = await request(app)
      .delete("/v1/agent-types/agente_retencao_v2/canary")
      .set(HEADERS)

    expect(res.status).toBe(200)
    expect(res.body.rolled_back).toBe("agente_retencao_v2")
    expect(res.body.restored_to).toBe("agente_retencao_v1")
    expect(res.body.previous_weight).toBe(1.0)
    expect(res.body.archived_at).toBeTruthy()

    // Verificar no banco
    const v1 = await prisma.agentType.findUnique({
      where: { agent_type_id_tenant_id: { agent_type_id: "agente_retencao_v1", tenant_id: TENANT } },
    })
    expect(v1!.traffic_weight).toBe(1.0)
    expect(v1!.status).toBe("active")

    const v2 = await prisma.agentType.findUnique({
      where: { agent_type_id_tenant_id: { agent_type_id: "agente_retencao_v2", tenant_id: TENANT } },
    })
    expect(v2!.status).toBe("deprecated")
    expect(v2!.traffic_weight).toBe(0.0)
  })

  it("rollback sem versão anterior — arquiva current, restored_to = null", async () => {
    // Apenas v1 existe, sem v0 para restaurar
    await request(app)
      .post("/v1/agent-types")
      .set(HEADERS)
      .send({ ...VALID_AGENT_TYPE, agent_type_id: "agente_retencao_v1" })

    const res = await request(app)
      .delete("/v1/agent-types/agente_retencao_v1/canary")
      .set(HEADERS)

    expect(res.status).toBe(200)
    expect(res.body.rolled_back).toBe("agente_retencao_v1")
    expect(res.body.restored_to).toBeNull()
    expect(res.body.previous_weight).toBeNull()

    // v1 arquivado
    const v1 = await prisma.agentType.findUnique({
      where: { agent_type_id_tenant_id: { agent_type_id: "agente_retencao_v1", tenant_id: TENANT } },
    })
    expect(v1!.status).toBe("deprecated")
  })

  it("rollback de v3 restaura v2 (não necessariamente v1)", async () => {
    await request(app)
      .post("/v1/agent-types")
      .set(HEADERS)
      .send({ ...VALID_AGENT_TYPE, agent_type_id: "agente_retencao_v2" })

    await request(app)
      .post("/v1/agent-types")
      .set(HEADERS)
      .send({ ...VALID_AGENT_TYPE, agent_type_id: "agente_retencao_v3" })

    const res = await request(app)
      .delete("/v1/agent-types/agente_retencao_v3/canary")
      .set(HEADERS)

    expect(res.status).toBe(200)
    expect(res.body.restored_to).toBe("agente_retencao_v2")

    const v2 = await prisma.agentType.findUnique({
      where: { agent_type_id_tenant_id: { agent_type_id: "agente_retencao_v2", tenant_id: TENANT } },
    })
    expect(v2!.traffic_weight).toBe(1.0)
    expect(v2!.status).toBe("active")
  })

  it("retorna 404 quando agent_type_id não existe", async () => {
    const res = await request(app)
      .delete("/v1/agent-types/agente_inexistente_v1/canary")
      .set(HEADERS)

    expect(res.status).toBe(404)
  })

  it("rollback não afeta agent types de outro tenant", async () => {
    // v1 no TENANT principal
    await request(app)
      .post("/v1/agent-types")
      .set(HEADERS)
      .send({ ...VALID_AGENT_TYPE, agent_type_id: "agente_retencao_v1" })

    // v1 em outro tenant (setup independente)
    await request(app)
      .post("/v1/pools")
      .set({ "x-tenant-id": "outro_tenant", "x-user-id": "u" })
      .send(VALID_POOL)
    await request(app)
      .post("/v1/agent-types")
      .set({ "x-tenant-id": "outro_tenant", "x-user-id": "u" })
      .send({ ...VALID_AGENT_TYPE, agent_type_id: "agente_retencao_v1" })

    // v2 apenas no TENANT principal
    await request(app)
      .post("/v1/agent-types")
      .set(HEADERS)
      .send({ ...VALID_AGENT_TYPE, agent_type_id: "agente_retencao_v2" })

    await request(app)
      .delete("/v1/agent-types/agente_retencao_v2/canary")
      .set(HEADERS)

    // v1 do outro tenant não deve ter sido afetado
    const otherTenantV1 = await prisma.agentType.findUnique({
      where: { agent_type_id_tenant_id: { agent_type_id: "agente_retencao_v1", tenant_id: "outro_tenant" } },
    })
    expect(otherTenantV1!.traffic_weight).toBe(1.0)
    expect(otherTenantV1!.status).toBe("active")
  })
})
