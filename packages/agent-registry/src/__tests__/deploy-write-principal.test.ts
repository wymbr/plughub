/**
 * deploy-write-principal.test.ts — PID-07 (2026-09-13)
 *
 * O QUE ESTE ARQUIVO PROVA
 * ------------------------
 * Três fatos medidos ao vivo antes do conserto, um bloco cada:
 *
 *   1. O DEPLOY tinha portão por acidente e no campo errado. `/v1/pools` casava por
 *      prefixo e julgava `/slots|promote|rollback` com `config.resources` (admin-only),
 *      e o devops, a quem o menu oferece a tela de Deploy (`skill_flows.operacao`),
 *      tomava 403 em set-next e promote.
 *   2. O TENANT da escrita vinha do header. Um token de `tenant_outro` com
 *      `x-tenant-id: tenant_demo` gravou um slot do tenant_demo (200) — em todos os
 *      routers, porque o portão conferia o grant e não o tenant.
 *   3. O AUTOR vinha do `x-user-id`. A UI não o manda (34 slots gravados como
 *      `system`), e quem manda escolhe o nome.
 *
 * POR QUE CADA NEGATIVO TEM O SEU POSITIVO
 * ----------------------------------------
 * Um portão que recusasse tudo passaria em qualquer 403. O campo de deploy é medido
 * atravessando (chega ao handler: 404 do pool mockado ausente) E recusando o vizinho;
 * o tenant divergente é recusado E o tenant ausente é preenchido com o do token; e o
 * serviço, identidade irrestrita, continua agindo pelo header.
 */
import { describe, it, expect, vi, beforeEach } from "vitest"
import crypto from "node:crypto"
import request from "supertest"

const { SECRET, SVC } = vi.hoisted(() => {
  const SECRET = "segredo-de-teste-pid07"
  const SVC    = "svc-de-teste-pid07"
  process.env["PLUGHUB_JWT_SECRET"]           = SECRET
  process.env["AGENT_REGISTRY_SERVICE_TOKEN"] = SVC
  return { SECRET, SVC }
})

vi.mock("../infra/kafka", () => ({
  publishRegistryEvent:   vi.fn().mockResolvedValue(undefined),
  publishRegistryChanged: vi.fn().mockResolvedValue(undefined),
  disconnectKafka:        vi.fn().mockResolvedValue(undefined),
}))

const tx = vi.hoisted(() => ({
  poolSkillSlot: { upsert: vi.fn().mockResolvedValue({}), deleteMany: vi.fn().mockResolvedValue({}) },
}))

vi.mock("../db", () => ({
  prisma: {
    pool:            { findUnique: vi.fn(), findMany: vi.fn(), create: vi.fn(), update: vi.fn() },
    skill:           { findUnique: vi.fn(), findMany: vi.fn(), create: vi.fn(), update: vi.fn(), upsert: vi.fn() },
    channel:         { findUnique: vi.fn(), findMany: vi.fn(), create: vi.fn(), update: vi.fn() },
    channelEndpoint: { findUnique: vi.fn(), findMany: vi.fn(), create: vi.fn(), update: vi.fn() },
    poolSkillSlot:   { findMany: vi.fn(), findUnique: vi.fn() },
    skillDeployment: { findMany: vi.fn(), findFirst: vi.fn() },
    $transaction:    vi.fn(async (fn: (t: unknown) => unknown) => fn(tx)),
  },
  Prisma: { DbNull: null },
}))

import { prisma } from "../db"
const { app } = await import("../app")

type Claims = Record<string, unknown>
function mint(grants: Array<[string, string]>, extra: Claims = {}): string {
  const b64 = (o: unknown) => Buffer.from(JSON.stringify(o)).toString("base64url")
  const module_config: Record<string, Record<string, { access: string }>> = {}
  for (const [m, c] of grants) (module_config[m] ??= {})[c] = { access: "read_write" }
  const head = b64({ alg: "HS256", typ: "JWT" })
  const body = b64({
    sub: "u-ana", email: "ana@tenant-a", tenant_id: "tenant_a",
    exp: Math.floor(Date.now() / 1000) + 3600, module_config, ...extra,
  })
  const sig = crypto.createHmac("sha256", SECRET).update(`${head}.${body}`).digest("base64url")
  return `${head}.${body}.${sig}`
}

const DEPLOY = mint([["skill_flows", "operacao"]])
const RESOURCES = mint([["config", "resources"]])
const TODOS = mint([["skill_flows", "operacao"], ["skill_flows", "editar"], ["config", "resources"], ["config", "channels"]])

const ROTAS_DEPLOY: Array<["put" | "post", string]> = [
  ["put",  "/v1/pools/p1/slots/next"],
  ["post", "/v1/pools/p1/promote"],
  ["post", "/v1/pools/p1/rollback"],
]

function send(method: "put" | "post", rota: string, headers: Record<string, string>, body: unknown = { skill_id: "skill_x" }) {
  return request(app)[method](rota).set(headers).send(body as object)
}

beforeEach(() => {
  vi.mocked(prisma.pool.findUnique).mockReset().mockResolvedValue(null as never)
  vi.mocked((prisma as any).poolSkillSlot.findMany).mockReset().mockResolvedValue([])
  tx.poolSkillSlot.upsert.mockClear()
})

describe("1 · o deploy responde ao campo da TELA de Deploy", () => {
  it("`skill_flows.operacao` atravessa o portão nas três rotas (chega ao handler)", async () => {
    for (const [m, rota] of ROTAS_DEPLOY) {
      const res = await send(m, rota, { authorization: `Bearer ${DEPLOY}` })
      expect(res.status, rota).toBe(404)
      expect(res.body.error, rota).toBe("Pool não encontrado")
    }
  })

  it("`config.resources` sozinho é recusado, nomeando `skill_flows.operacao`", async () => {
    for (const [m, rota] of ROTAS_DEPLOY) {
      const res = await send(m, rota, { authorization: `Bearer ${RESOURCES}` })
      expect(res.status, rota).toBe(403)
      expect(res.body.message, rota).toContain("skill_flows.operacao")
    }
  })

  it("sem credencial, as três recusam com 401", async () => {
    for (const [m, rota] of ROTAS_DEPLOY) {
      expect((await send(m, rota, {})).status, rota).toBe(401)
    }
  })

  it("controle: o portão do deploy não engoliu a edição do POOL, que segue em `config.resources`", async () => {
    const semCampo = await send("put", "/v1/pools/p1", { authorization: `Bearer ${DEPLOY}` }, {})
    expect(semCampo.status).toBe(403)
    expect(semCampo.body.message).toContain("config.resources")
    const comCampo = await send("put", "/v1/pools/p1", { authorization: `Bearer ${RESOURCES}` }, {})
    expect(comCampo.status).not.toBe(401)
    expect(comCampo.status).not.toBe(403)
  })
})

describe("2 · o tenant da escrita é o do TOKEN", () => {
  it("header de outro tenant é recusado em todos os routers de escrita", async () => {
    const rotas: Array<["put" | "post", string]> = [
      ...ROTAS_DEPLOY, ["post", "/v1/pools"], ["post", "/v1/skills"], ["post", "/v1/channels"], ["post", "/v1/channel-endpoints"],
    ]
    for (const [m, rota] of rotas) {
      const res = await send(m, rota, { authorization: `Bearer ${TODOS}`, "x-tenant-id": "tenant_b" }, { invalido: true })
      expect(res.status, rota).toBe(403)
      expect(res.body.error, rota).toBe("tenant_mismatch")
    }
    expect(prisma.pool.findUnique).not.toHaveBeenCalled()
  })

  it("controle: o MESMO tenant no header atravessa", async () => {
    const res = await send("put", "/v1/pools/p1/slots/next", { authorization: `Bearer ${DEPLOY}`, "x-tenant-id": "tenant_a" })
    expect(res.status).toBe(404)
  })

  it("header ausente: o handler lê o tenant do token, não o default", async () => {
    await send("post", "/v1/pools/p1/promote", { authorization: `Bearer ${DEPLOY}` })
    expect(prisma.pool.findUnique).toHaveBeenCalledWith({
      where: { pool_id_tenant_id: { pool_id: "p1", tenant_id: "tenant_a" } },
    })
  })

  it("token sem `tenant_id` é recusado — não há tenant em nome do qual gravar", async () => {
    const semTenant = mint([["skill_flows", "operacao"]], { tenant_id: undefined })
    const res = await send("put", "/v1/pools/p1/slots/next", { authorization: `Bearer ${semTenant}`, "x-tenant-id": "tenant_a" })
    expect(res.status).toBe(403)
    expect(res.body.error).toBe("tenant_claim_missing")
  })

  it("controle: credencial de SERVIÇO continua agindo pelo tenant do header", async () => {
    await send("post", "/v1/pools/p1/promote", { "x-service-token": SVC, "x-tenant-id": "tenant_b" })
    expect(prisma.pool.findUnique).toHaveBeenCalledWith({
      where: { pool_id_tenant_id: { pool_id: "p1", tenant_id: "tenant_b" } },
    })
  })
})

describe("3 · o autor é o da credencial", () => {
  beforeEach(() => {
    vi.mocked(prisma.pool.findUnique).mockResolvedValue({ pool_id: "p1", tenant_id: "tenant_a" } as never)
    vi.mocked((prisma as any).poolSkillSlot.findMany).mockResolvedValue([
      { slot: "previous", skill_id: "skill_x", config_json: {}, yaml_snapshot: { steps: [] } },
    ])
  })

  const setBy = () => (tx.poolSkillSlot.upsert.mock.calls[0]![0] as { update: { set_by: string } }).update.set_by

  it("com Bearer, `x-user-id` forjado é ignorado e o autor é o email do token", async () => {
    const res = await send("post", "/v1/pools/p1/rollback", { authorization: `Bearer ${DEPLOY}`, "x-user-id": "forjado" }, {})
    expect(res.status).toBe(200)
    expect(setBy()).toBe("ana@tenant-a")
  })

  it("sem email, o autor é o `sub` — nunca `system`", async () => {
    const semEmail = mint([["skill_flows", "operacao"]], { email: undefined })
    await send("post", "/v1/pools/p1/rollback", { authorization: `Bearer ${semEmail}` }, {})
    expect(setBy()).toBe("u-ana")
  })

  it("controle: o SERVIÇO diz em nome de quem age", async () => {
    await send("post", "/v1/pools/p1/rollback", { "x-service-token": SVC, "x-tenant-id": "tenant_a", "x-user-id": "registry-syncer" }, {})
    expect(setBy()).toBe("registry-syncer")
  })
})
