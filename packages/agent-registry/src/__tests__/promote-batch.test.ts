/**
 * promote-batch.test.ts — PID-16 (2026-09-14)
 *
 * O QUE ESTE ARQUIVO PROVA
 * ------------------------
 * `POST /v1/pool-slots/promote-batch` promove o MESMO snapshot de um skill em N pools
 * nomeados, tudo ou nada, e cada pool com a SUA config. Medido antes: o
 * `skill_intake_runner_v1` roda em duas portas com um snapshot e duas configs, e o release
 * era um `set-next` + `promote` por pool — snapshots congelados em instantes diferentes, e
 * uma recusa no meio deixando o release pela metade.
 *
 * POR QUE CADA NEGATIVO TEM O SEU POSITIVO
 * ----------------------------------------
 * Um lote que recusasse tudo passaria em todo "nada mudou". Por isso o bloqueio de um pool
 * é medido junto do mesmo lote SEM aquele pool (que passa), o `next` pendente junto do
 * `replace_pending_next`, a config indefinida junto da config declarada, e a capacidade
 * somada junto de cada pool sozinho (que caberia).
 */
import { describe, it, expect, vi, beforeEach } from "vitest"
import crypto from "node:crypto"
import request from "supertest"

const { SECRET, SVC } = vi.hoisted(() => {
  const SECRET = "segredo-de-teste-pid16"
  const SVC    = "svc-de-teste-pid16"
  process.env["PLUGHUB_JWT_SECRET"]           = SECRET
  process.env["AGENT_REGISTRY_SERVICE_TOKEN"] = SVC
  return { SECRET, SVC }
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
    poolSkillSlot:   { findMany: vi.fn(), findUnique: vi.fn() },
    skillDeployment: { findMany: vi.fn(), findFirst: vi.fn(), create: vi.fn() },
    $transaction:    vi.fn(),
  },
  Prisma: { DbNull: null },
}))

import { prisma } from "../db"
import { publishRegistryChanged } from "../infra/kafka"
import { deployViolation, deployViolationBatch } from "../lib/capacity"
const { app } = await import("../app")

function mint(grants: Array<[string, string]>): string {
  const b64 = (o: unknown) => Buffer.from(JSON.stringify(o)).toString("base64url")
  const module_config: Record<string, Record<string, { access: string }>> = {}
  for (const [m, c] of grants) (module_config[m] ??= {})[c] = { access: "read_write" }
  const head = b64({ alg: "HS256", typ: "JWT" })
  const body = b64({
    sub: "u-ana", email: "ana@tenant-a", tenant_id: "tenant_a",
    exp: Math.floor(Date.now() / 1000) + 3600, module_config,
  })
  const sig = crypto.createHmac("sha256", SECRET).update(`${head}.${body}`).digest("base64url")
  return `${head}.${body}.${sig}`
}

const p = prisma as any
const ROTA = "/v1/pool-slots/promote-batch"
const SVC_H = { "x-service-token": SVC, "x-tenant-id": "tenant_a", "x-user-id": "ana" }

const FLOW_NOVO  = { entry: "fim", steps: [{ id: "fim", type: "complete", outcome: "resolved" }] }
const FLOW_VELHO = { entry: "ini", steps: [{ id: "ini", type: "complete", outcome: "resolved" }] }
const PARAMS = [{ key: "dialog_form_id", required: true }]

function pool(id: string, extra: Record<string, unknown> = {}) {
  return { pool_id: id, tenant_id: "tenant_a", agent_kind: "ai", channel_types: ["webchat"], ...extra }
}
function slot(pool_id: string, slotName: string, skill_id: string, config_json: unknown, yaml_snapshot: unknown = FLOW_VELHO) {
  return { pool_id, tenant_id: "tenant_a", slot: slotName, skill_id, config_json, yaml_snapshot, set_at: "2026-09-14T10:00:00Z" }
}

let slots: Array<Record<string, unknown>>

beforeEach(() => {
  p.skill.findUnique.mockReset().mockResolvedValue({
    skill_id: "skill_porta", tenant_id: "tenant_a", version: "3", flow: FLOW_NOVO, config_params: PARAMS,
  })
  p.pool.findMany.mockReset().mockResolvedValue([pool("porta_a"), pool("porta_b")])
  slots = [
    slot("porta_a", "current", "skill_porta", { dialog_form_id: "roteiro_a", max_concurrent_sessions: 5 }),
    slot("porta_b", "current", "skill_porta", { dialog_form_id: "roteiro_b", max_concurrent_sessions: 10 }),
  ]
  // `findMany` serve às duas leituras: os slots do lote (pool_id IN) e, na capacidade,
  // os `current` do tenant inteiro.
  p.poolSkillSlot.findMany.mockReset().mockImplementation(async (q: any) => {
    const w = q?.where ?? {}
    return slots.filter(s =>
      (!w.slot || s["slot"] === w.slot)
      && (!w.pool_id?.in || w.pool_id.in.includes(s["pool_id"])))
  })
  p.poolSkillSlot.findUnique.mockReset().mockImplementation(async (q: any) => {
    const k = q.where.pool_id_tenant_id_slot
    return slots.find(s => s["pool_id"] === k.pool_id && s["slot"] === k.slot) ?? null
  })
  p.skillDeployment.create.mockReset().mockResolvedValue({})
  p.$transaction.mockReset().mockImplementation(async (fn: (t: unknown) => unknown) => fn(tx))
  tx.poolSkillSlot.upsert.mockReset().mockResolvedValue({})
  tx.poolSkillSlot.deleteMany.mockReset().mockResolvedValue({})
  vi.mocked(publishRegistryChanged).mockClear()
  redisGet.mockReset().mockResolvedValue(null)   // sem C → capacidade fail-open
})

function lote(body: Record<string, unknown>, headers: Record<string, string> = SVC_H) {
  return request(app).post(ROTA).set(headers).send(body)
}
const upsertsDe = (slotName: string) =>
  tx.poolSkillSlot.upsert.mock.calls.map(c => c[0]).filter((a: any) => a.where.pool_id_tenant_id_slot.slot === slotName)

describe("1 · o portão é o do deploy", () => {
  it("sem credencial → 401", async () => {
    expect((await request(app).post(ROTA).send({ skill_id: "skill_porta", pools: ["porta_a"] })).status).toBe(401)
  })

  it("`config.resources` sozinho é recusado nomeando `skill_flows.operacao`", async () => {
    const res = await lote({ skill_id: "skill_porta", pools: ["porta_a"] },
      { authorization: `Bearer ${mint([["config", "resources"]])}` })
    expect(res.status).toBe(403)
    expect(res.body.message).toContain("skill_flows.operacao")
  })

  it("controle: `skill_flows.operacao` atravessa e o autor sai do TOKEN", async () => {
    const res = await lote({ skill_id: "skill_porta", pools: ["porta_a"] },
      { authorization: `Bearer ${mint([["skill_flows", "operacao"]])}` })
    expect(res.status).toBe(200)
    expect(p.skillDeployment.create.mock.calls[0][0].data.deployed_by).toBe("ana@tenant-a")
  })
})

describe("2 · a forma do pedido", () => {
  it.each([
    [{ pools: ["porta_a"] },                                          "skill_id é obrigatório"],
    [{ skill_id: "skill_porta", pools: [] },                         "pools deve ser uma lista"],
    [{ skill_id: "skill_porta", pools: ["porta_a", "porta_a"] },     "pool repetido no lote"],
    [{ skill_id: "skill_porta", pools: ["porta_a"], configs: { porta_z: {} } }, "configs nomeia pool fora do lote"],
  ])("%j → 400", async (body, msg) => {
    const res = await lote(body)
    expect(res.status).toBe(400)
    expect(res.body.error).toContain(msg)
    expect(p.$transaction).not.toHaveBeenCalled()
  })
})

describe("3 · UM snapshot, a config de CADA pool, UMA transação", () => {
  it("dois pools: mesmo snapshot em ambos, cada um com a sua config, um SkillDeployment e um evento por pool", async () => {
    const res = await lote({ skill_id: "skill_porta", pools: ["porta_a", "porta_b"] })
    expect(res.status).toBe(200)
    expect(res.body).toMatchObject({ skill_id: "skill_porta", promoted: 2, unchanged: 0 })
    expect(p.$transaction).toHaveBeenCalledTimes(1)

    const current = upsertsDe("current")
    expect(current.map((a: any) => a.where.pool_id_tenant_id_slot.pool_id)).toEqual(["porta_a", "porta_b"])
    expect(current.every((a: any) => a.update.yaml_snapshot === FLOW_NOVO)).toBe(true)
    expect(current[0].update.config_json).toEqual({ dialog_form_id: "roteiro_a", max_concurrent_sessions: 5 })
    expect(current[1].update.config_json).toEqual({ dialog_form_id: "roteiro_b", max_concurrent_sessions: 10 })

    const previous = upsertsDe("previous")
    expect(previous.map((a: any) => a.update.yaml_snapshot)).toEqual([FLOW_VELHO, FLOW_VELHO])

    const deps = p.skillDeployment.create.mock.calls.map((c: any) => c[0].data)
    expect(deps.map((d: any) => d.pool_ids)).toEqual([["porta_a"], ["porta_b"]])
    expect(deps[0].notes).toBe(deps[1].notes)
    expect(deps[0].notes).toContain(`promote-batch:${res.body.batch_id}`)
    expect(deps[0].notes).toContain("pools=porta_a,porta_b")
    expect(vi.mocked(publishRegistryChanged).mock.calls.map(c => c[2])).toEqual(["porta_a", "porta_b"])
  })

  it("configs[pool] explícita substitui a do current só daquele pool", async () => {
    const res = await lote({
      skill_id: "skill_porta", pools: ["porta_a", "porta_b"],
      configs: { porta_b: { dialog_form_id: "roteiro_b2" } },
    })
    expect(res.status).toBe(200)
    const current = upsertsDe("current")
    expect(current[0].update.config_json).toEqual({ dialog_form_id: "roteiro_a", max_concurrent_sessions: 5 })
    expect(current[1].update.config_json).toEqual({ dialog_form_id: "roteiro_b2" })
  })
})

describe("4 · tudo ou nada", () => {
  it("um pool bloqueado (config obrigatória vazia) → 422 nomeando o pool, e NADA muda nos dois", async () => {
    slots[1]!["config_json"] = { max_concurrent_sessions: 10 }   // porta_b perdeu dialog_form_id
    const res = await lote({ skill_id: "skill_porta", pools: ["porta_a", "porta_b"] })
    expect(res.status).toBe(422)
    expect(res.body.error).toBe("lote_recusado")
    expect(res.body.pools).toEqual([expect.objectContaining({ pool_id: "porta_b", error: "config_obrigatoria_ausente" })])
    expect(p.$transaction).not.toHaveBeenCalled()
    expect(p.skillDeployment.create).not.toHaveBeenCalled()
    expect(publishRegistryChanged).not.toHaveBeenCalled()
  })

  it("controle: o MESMO lote sem o pool bloqueado passa", async () => {
    slots[1]!["config_json"] = { max_concurrent_sessions: 10 }
    const res = await lote({ skill_id: "skill_porta", pools: ["porta_a"] })
    expect(res.status).toBe(200)
    expect(res.body.promoted).toBe(1)
  })

  it("pool inexistente e pool humano entram na lista de recusas, cada um com o seu motivo", async () => {
    p.pool.findMany.mockResolvedValue([pool("porta_a"), pool("porta_h", { agent_kind: "human" })])
    slots.push(slot("porta_h", "current", "skill_porta", { dialog_form_id: "x" }))
    const res = await lote({ skill_id: "skill_porta", pools: ["porta_a", "porta_z", "porta_h"] })
    expect(res.status).toBe(422)
    expect(res.body.pools.map((x: any) => [x.pool_id, x.error])).toEqual([
      ["porta_z", "pool_nao_encontrado"],
      ["porta_h", expect.stringContaining("human")],
    ])
    expect(p.$transaction).not.toHaveBeenCalled()
  })
})

describe("5 · a config não é adivinhada", () => {
  it("pool cujo current roda OUTRO skill, sem configs[pool] → config_indefinida", async () => {
    slots[1] = slot("porta_b", "current", "skill_outro", { dialog_form_id: "do_outro" })
    const res = await lote({ skill_id: "skill_porta", pools: ["porta_a", "porta_b"] })
    expect(res.status).toBe(422)
    expect(res.body.pools).toEqual([expect.objectContaining({ pool_id: "porta_b", error: "config_indefinida" })])
    expect(res.body.pools[0].message).toContain("skill_outro")
  })

  it("controle: com configs[pool] declarada, o mesmo pool entra — e o previous guarda o outro skill", async () => {
    slots[1] = slot("porta_b", "current", "skill_outro", { dialog_form_id: "do_outro" })
    const res = await lote({ skill_id: "skill_porta", pools: ["porta_a", "porta_b"], configs: { porta_b: { dialog_form_id: "novo" } } })
    expect(res.status).toBe(200)
    expect(res.body.pools[1]).toMatchObject({ pool_id: "porta_b", previous_skill_id: "skill_outro" })
    expect(upsertsDe("previous")[1].update.skill_id).toBe("skill_outro")
  })
})

describe("6 · o lote não atropela um next pendente", () => {
  beforeEach(() => { slots.push(slot("porta_b", "next", "skill_outro", {}, FLOW_VELHO)) })

  it("next pendente → recusa nomeando o que estava lá", async () => {
    const res = await lote({ skill_id: "skill_porta", pools: ["porta_a", "porta_b"] })
    expect(res.status).toBe(422)
    expect(res.body.pools).toEqual([expect.objectContaining({ pool_id: "porta_b", error: "next_pendente" })])
    expect(res.body.pools[0].message).toContain("skill_outro")
    expect(p.$transaction).not.toHaveBeenCalled()
  })

  it("controle: replace_pending_next: true substitui de propósito, e o next é limpo", async () => {
    const res = await lote({ skill_id: "skill_porta", pools: ["porta_a", "porta_b"], replace_pending_next: true })
    expect(res.status).toBe(200)
    const limpos = tx.poolSkillSlot.deleteMany.mock.calls.map(c => c[0].where)
    expect(limpos).toContainEqual({ pool_id: "porta_b", tenant_id: "tenant_a", slot: "next" })
  })
})

describe("7 · pool que já roda exatamente isto não é tocado (o previous sobrevive)", () => {
  it("current com o MESMO snapshot e a MESMA config → unchanged, fora da transação e sem SkillDeployment", async () => {
    slots[0]!["yaml_snapshot"] = { steps: [{ outcome: "resolved", type: "complete", id: "fim" }], entry: "fim" }  // mesmas chaves, outra ordem
    const res = await lote({ skill_id: "skill_porta", pools: ["porta_a", "porta_b"] })
    expect(res.status).toBe(200)
    expect(res.body).toMatchObject({ promoted: 1, unchanged: 1 })
    expect(res.body.pools[0]).toEqual({ pool_id: "porta_a", action: "unchanged" })
    const tocados = tx.poolSkillSlot.upsert.mock.calls.map(c => c[0].where.pool_id_tenant_id_slot.pool_id)
    expect(tocados).not.toContain("porta_a")
    expect(p.skillDeployment.create.mock.calls.map((c: any) => c[0].data.pool_ids)).toEqual([["porta_b"]])
  })

  it("controle: mesma config e snapshot DIFERENTE → promovido", async () => {
    const res = await lote({ skill_id: "skill_porta", pools: ["porta_a"] })
    expect(res.body.pools[0].action).toBe("promoted")
  })
})

describe("8 · capacidade é julgada sobre o LOTE somado", () => {
  beforeEach(() => {
    redisGet.mockResolvedValue("10")   // C = 10
    slots = [
      slot("porta_a", "current", "skill_porta", { dialog_form_id: "a", max_concurrent_sessions: 1 }),
      slot("porta_b", "current", "skill_porta", { dialog_form_id: "b", max_concurrent_sessions: 1 }),
      slot("outro",   "current", "skill_x",     { max_concurrent_sessions: 6 }),
    ]
  })
  const configs = { porta_a: { dialog_form_id: "a", max_concurrent_sessions: 3 }, porta_b: { dialog_form_id: "b", max_concurrent_sessions: 3 } }

  it("controle: cada aumento SOZINHO cabe (6 + 1 + 3 = 10)", async () => {
    expect(await deployViolation("tenant_a", "porta_a", 3)).toBeNull()
    expect(await deployViolation("tenant_a", "porta_b", 3)).toBeNull()
  })

  it("os dois JUNTOS estouram (6 + 3 + 3 = 12 > 10) → 422 nos dois pools, nada muda", async () => {
    const res = await lote({ skill_id: "skill_porta", pools: ["porta_a", "porta_b"], configs })
    expect(res.status).toBe(422)
    expect(res.body.pools.map((x: any) => [x.pool_id, x.error])).toEqual([["porta_a", "capacidade"], ["porta_b", "capacidade"]])
    expect(res.body.pools[0].details.details).toMatchObject({ contracted: 10, declared_others: 6, requested: 6, declared_total: 12 })
    expect(p.$transaction).not.toHaveBeenCalled()
  })

  it("lote em que ninguém aumenta passa mesmo acima de C (redução/igual sempre passa)", async () => {
    redisGet.mockResolvedValue("2")
    expect(await deployViolationBatch("tenant_a", [{ poolId: "porta_a", declared: 1 }, { poolId: "porta_b", declared: 1 }])).toBeNull()
  })
})
