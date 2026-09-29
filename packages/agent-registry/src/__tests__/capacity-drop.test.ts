/**
 * capacity-drop.test.ts — PRM-04 (2026-09-29)
 *
 * O QUE ESTE ARQUIVO PROVA
 * ------------------------
 * Um deploy que declara MENOS concorrência do que o pool roda é aceito (reduzir é
 * legítimo) e DITO, nas três portas que declaram ou efetivam: `set-next`, `promote` e
 * `promote-batch`. Medido em 2026-09-08: o formulário propôs `1` para o `demo_ia`, que
 * rodava `10`; o promote aceitou calado e o bootstrap derrubou nove instâncias.
 *
 * POR QUE CADA AVISO TEM O SEU CONTROLE
 * -------------------------------------
 * Um aviso emitido sempre passaria em todo "avisou". Por isso cada queda é medida junto
 * do mesmo pedido mantendo o valor (sem aviso), e o campo ausente — que vale 1 e foi o
 * default do estrago — é nomeado como ausente.
 */
import { describe, it, expect, vi, beforeEach } from "vitest"
import request from "supertest"

const { SVC } = vi.hoisted(() => {
  const SVC = "svc-de-teste-prm04"
  process.env["PLUGHUB_JWT_SECRET"]           = "segredo-de-teste-prm04"
  process.env["AGENT_REGISTRY_SERVICE_TOKEN"] = SVC
  return { SVC }
})

vi.mock("../infra/kafka", () => ({
  publishRegistryEvent:   vi.fn().mockResolvedValue(undefined),
  publishRegistryChanged: vi.fn().mockResolvedValue(undefined),
  disconnectKafka:        vi.fn().mockResolvedValue(undefined),
}))

const redisGet = vi.hoisted(() => vi.fn())
vi.mock("../infra/redis", () => ({ getRedis: () => ({ get: redisGet }) }))

const tx = vi.hoisted(() => ({
  poolSkillSlot: { upsert: vi.fn(), deleteMany: vi.fn() },
}))

vi.mock("../db", () => ({
  prisma: {
    pool:            { findUnique: vi.fn(), findMany: vi.fn(), update: vi.fn() },
    skill:           { findUnique: vi.fn(), findMany: vi.fn(), update: vi.fn(), upsert: vi.fn() },
    poolSkillSlot:   { findMany: vi.fn(), findUnique: vi.fn(), upsert: vi.fn() },
    skillDeployment: { findMany: vi.fn(), findFirst: vi.fn(), create: vi.fn() },
    $transaction:    vi.fn(),
  },
  Prisma: { DbNull: null },
}))

import { prisma } from "../db"
import { capacityDropWarning } from "../lib/capacity"
const { app } = await import("../app")

const p = prisma as any
const H = { "x-service-token": SVC, "x-tenant-id": "tenant_a", "x-user-id": "ana" }
const FLOW = { entry: "fim", steps: [{ id: "fim", type: "complete", outcome: "resolved" }] }

function slot(slotName: string, config_json: unknown, skill_id = "skill_demo") {
  return { pool_id: "demo_ia", tenant_id: "tenant_a", slot: slotName, skill_id, config_json,
           yaml_snapshot: FLOW, set_at: "2026-09-29T10:00:00Z" }
}

let slots: Array<Record<string, unknown>>

beforeEach(() => {
  p.pool.findUnique.mockReset().mockResolvedValue(
    { pool_id: "demo_ia", tenant_id: "tenant_a", agent_kind: "ai", channel_types: ["webchat"] })
  p.pool.findMany.mockReset().mockResolvedValue(
    [{ pool_id: "demo_ia", tenant_id: "tenant_a", agent_kind: "ai", channel_types: ["webchat"] }])
  p.skill.findUnique.mockReset().mockResolvedValue(
    { skill_id: "skill_demo", tenant_id: "tenant_a", version: "1", flow: FLOW, config_params: [] })
  slots = [slot("current", { max_concurrent_sessions: 10 })]
  p.poolSkillSlot.findMany.mockReset().mockImplementation(async (q: any) => {
    const w = q?.where ?? {}
    return slots.filter(s => !w.slot || s["slot"] === w.slot)
  })
  p.poolSkillSlot.findUnique.mockReset().mockImplementation(async (q: any) => {
    const k = q.where.pool_id_tenant_id_slot
    return slots.find(s => s["slot"] === k.slot) ?? null
  })
  p.poolSkillSlot.upsert.mockReset().mockImplementation(async (a: any) => ({ ...a.create, set_at: new Date() }))
  p.skillDeployment.create.mockReset().mockResolvedValue({})
  p.$transaction.mockReset().mockImplementation(async (fn: (t: unknown) => unknown) => fn(tx))
  tx.poolSkillSlot.upsert.mockReset().mockResolvedValue({})
  tx.poolSkillSlot.deleteMany.mockReset().mockResolvedValue({})
  redisGet.mockReset().mockResolvedValue(null)   // sem C → capacidade fail-open
})

const quedaDe = (body: any): string[] =>
  (body.warnings ?? []).filter((w: string) => w.startsWith("capacidade_reduzida"))

describe("1 · a regra (pura)", () => {
  it("queda declarada → aviso com os dois números", () => {
    const w = capacityDropWarning("demo_ia", { skill_id: "s", config_json: { max_concurrent_sessions: 10 } },
      { max_concurrent_sessions: 1 })
    expect(w).toContain("max_concurrent_sessions=10")
    expect(w).toContain("declara 1")
    expect(w).not.toContain("AUSENTE")
  })

  it("campo AUSENTE é nomeado — vale 1, o default do estrago", () => {
    const w = capacityDropWarning("demo_ia", { skill_id: "s", config_json: { max_concurrent_sessions: 10 } }, {})
    expect(w).toContain("AUSENTE")
  })

  it.each([
    ["igual",            { max_concurrent_sessions: 10 }],
    ["aumento",          { max_concurrent_sessions: 20 }],
  ])("controle: %s → sem aviso", (_n, cfg) => {
    expect(capacityDropWarning("demo_ia", { skill_id: "s", config_json: { max_concurrent_sessions: 10 } }, cfg)).toBeNull()
  })

  it("controle: sem `current` (primeiro deploy) não há o que reduzir", () => {
    expect(capacityDropWarning("demo_ia", null, { max_concurrent_sessions: 1 })).toBeNull()
    expect(capacityDropWarning("demo_ia", { skill_id: null, config_json: {} }, {})).toBeNull()
  })
})

describe("2 · set-next avisa, e não recusa", () => {
  const setNext = (config_json: unknown) =>
    request(app).put("/v1/pools/demo_ia/slots/next").set(H).send({ skill_id: "skill_demo", config_json })

  it("declarar 1 num pool que roda 10 → 200 com o aviso", async () => {
    const res = await setNext({ max_concurrent_sessions: 1 })
    expect(res.status).toBe(200)
    expect(quedaDe(res.body)).toHaveLength(1)
    expect(p.poolSkillSlot.upsert).toHaveBeenCalledTimes(1)
  })

  it("controle: manter 10 → sem aviso", async () => {
    const res = await setNext({ max_concurrent_sessions: 10 })
    expect(res.status).toBe(200)
    expect(quedaDe(res.body)).toHaveLength(0)
  })
})

describe("3 · promote re-diz, porque quem promove pode não ser quem declarou", () => {
  it("next com 1 sobre current com 10 → promovido com o aviso", async () => {
    slots.push(slot("next", { max_concurrent_sessions: 1 }))
    const res = await request(app).post("/v1/pools/demo_ia/promote").set(H).send({})
    expect(res.status).toBe(200)
    expect(res.body.action).toBe("promoted")
    expect(quedaDe(res.body)).toHaveLength(1)
  })

  it("controle: next com 10 → promovido sem aviso", async () => {
    slots.push(slot("next", { max_concurrent_sessions: 10 }))
    const res = await request(app).post("/v1/pools/demo_ia/promote").set(H).send({})
    expect(res.status).toBe(200)
    expect(quedaDe(res.body)).toHaveLength(0)
  })
})

describe("4 · promote-batch: só a config DECLARADA pode cair", () => {
  const lote = (body: Record<string, unknown>) =>
    request(app).post("/v1/pool-slots/promote-batch").set(H).send({ skill_id: "skill_demo", ...body })

  beforeEach(() => {
    // snapshot diferente do current, para o pool não sair como `unchanged`
    slots = [{ ...slot("current", { max_concurrent_sessions: 10 }), yaml_snapshot: { entry: "velho", steps: [] } }]
  })

  it("configs[pool] com 1 → aviso no pool", async () => {
    const res = await lote({ pools: ["demo_ia"], configs: { demo_ia: { max_concurrent_sessions: 1 } } })
    expect(res.status).toBe(200)
    expect(quedaDe(res.body.pools[0])).toHaveLength(1)
  })

  it("controle: config herdada do current → sem aviso", async () => {
    const res = await lote({ pools: ["demo_ia"] })
    expect(res.status).toBe(200)
    expect(quedaDe(res.body.pools[0])).toHaveLength(0)
  })
})
