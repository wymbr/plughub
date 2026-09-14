/**
 * batch-deploy-retired.test.ts — PID-08 (2026-09-14)
 *
 * O QUE ESTE ARQUIVO PROVA
 * ------------------------
 * `POST /v1/skills/:id/deploy` gravava `skill.flow` e um `SkillDeployment` dizendo
 * "implantado nos pools X" sem tocar slot nenhum — e a produção executa o snapshot do
 * slot `current`. Medido antes de aposentar: 115 registros, 110 de promote, 5 de um seed
 * de demonstração, ZERO deploys reais. Três fatos, um bloco cada:
 *
 *   1. a rota responde 410 e NÃO escreve nada (nem skill, nem deployment);
 *   2. o portão de escrita continua na frente dela (401 sem credencial) — aposentar não
 *      pode virar porta anônima que responde;
 *   3. o `SkillDeployment` tem UM escritor no fonte: o promote do pool.
 *
 * POR QUE CADA NEGATIVO TEM O SEU POSITIVO
 * ----------------------------------------
 * Um app que respondesse 410 para tudo passaria no bloco 1: a leitura vizinha
 * (`GET /deployments`, que alimenta a lente e o handoff) tem de continuar respondendo
 * 200. E um censo que não achasse escritor nenhum passaria no bloco 3: ele exige
 * encontrar EXATAMENTE o do promote.
 */
import { describe, it, expect, vi, beforeEach } from "vitest"
import crypto from "node:crypto"
import fs from "node:fs"
import path from "node:path"
import request from "supertest"

const { SECRET } = vi.hoisted(() => {
  const SECRET = "segredo-de-teste-pid08"
  process.env["PLUGHUB_JWT_SECRET"]           = SECRET
  process.env["AGENT_REGISTRY_SERVICE_TOKEN"] = "svc-de-teste-pid08"
  return { SECRET }
})

vi.mock("../infra/kafka", () => ({
  publishRegistryEvent:   vi.fn().mockResolvedValue(undefined),
  publishRegistryChanged: vi.fn().mockResolvedValue(undefined),
  disconnectKafka:        vi.fn().mockResolvedValue(undefined),
}))

vi.mock("../lib/capacity", async (orig) => ({
  ...(await orig<typeof import("../lib/capacity")>()),
  deployViolation: vi.fn().mockResolvedValue(null),
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
const { app } = await import("../app")

function mint(grants: Array<[string, string]>): string {
  const b64 = (o: unknown) => Buffer.from(JSON.stringify(o)).toString("base64url")
  const module_config: Record<string, Record<string, { access: string }>> = {}
  for (const [m, c] of grants) (module_config[m] ??= {})[c] = { access: "read_write" }
  const head = b64({ alg: "HS256", typ: "JWT" })
  const body = b64({
    sub: "u-ana", tenant_id: "tenant_a", exp: Math.floor(Date.now() / 1000) + 3600, module_config,
  })
  const sig = crypto.createHmac("sha256", SECRET).update(`${head}.${body}`).digest("base64url")
  return `${head}.${body}.${sig}`
}

const EDITOR = mint([["skill_flows", "editar"], ["skill_flows", "operacao"]])
const p = prisma as any

beforeEach(() => {
  p.skill.findUnique.mockReset().mockResolvedValue({ skill_id: "skill_x", tenant_id: "tenant_a", flow_draft: { entry: "a", steps: [] } })
  p.skill.update.mockReset()
  p.skillDeployment.create.mockReset()
  p.skillDeployment.findMany.mockReset().mockResolvedValue([
    { id: "d1", skill_id: "skill_x", tenant_id: "tenant_a", notes: "promote", pool_ids: ["p1"], deployed_at: new Date() },
  ])
})

describe("1 · o deploy em lote responde 410 e não escreve", () => {
  it("POST /v1/skills/:id/deploy → 410 nomeando o caminho do pool, sem tocar skill nem deployment", async () => {
    const res = await request(app).post("/v1/skills/skill_x/deploy")
      .set({ authorization: `Bearer ${EDITOR}` }).send({ pool_ids: ["p1"], notes: "tentativa" })
    expect(res.status).toBe(410)
    expect(res.body.error).toBe("deploy_route_retired")
    expect(res.body.message).toContain("/promote")
    expect(p.skill.update).not.toHaveBeenCalled()
    expect(p.skillDeployment.create).not.toHaveBeenCalled()
  })

  it("GET /v1/skills/:id/deployments/scheduled → 410 (listava o workflow aposentado)", async () => {
    const res = await request(app).get("/v1/skills/skill_x/deployments/scheduled").set({ "x-tenant-id": "tenant_a" })
    expect(res.status).toBe(410)
    expect(res.body.error).toBe("scheduled_deploy_retired")
  })

  it("controle: a leitura vizinha `GET /deployments` segue respondendo 200 com o registro do promote", async () => {
    const res = await request(app).get("/v1/skills/skill_x/deployments").set({ "x-tenant-id": "tenant_a" })
    expect(res.status).toBe(200)
    expect(res.body.total).toBe(1)
    expect(res.body.deployments[0].notes).toBe("promote")
  })
})

describe("2 · o portão de escrita continua na frente da rota aposentada", () => {
  it("sem credencial → 401, não 410", async () => {
    const res = await request(app).post("/v1/skills/skill_x/deploy").send({})
    expect(res.status).toBe(401)
  })
})

describe("3b · o escritor único escreve — e, se falhar, não falha mudo", () => {
  const SVC_H = { "x-service-token": "svc-de-teste-pid08", "x-tenant-id": "tenant_a", "x-user-id": "ana" }

  beforeEach(() => {
    p.pool.findUnique.mockReset().mockResolvedValue({ pool_id: "p1", tenant_id: "tenant_a", channel_types: ["webchat"] })
    p.poolSkillSlot.findMany.mockReset().mockResolvedValue([
      { slot: "next", skill_id: "skill_x", config_json: {}, yaml_snapshot: { entry: "fim", steps: [] } },
    ])
    p.skill.findUnique.mockReset().mockResolvedValue({ skill_id: "skill_x", version: "7" })
    p.$transaction.mockReset().mockImplementation(async (fn: (t: unknown) => unknown) =>
      fn({ poolSkillSlot: { upsert: vi.fn(), deleteMany: vi.fn() } }))
  })

  it("promote grava o SkillDeployment com o pool, o autor e notes='promote'", async () => {
    const res = await request(app).post("/v1/pools/p1/promote").set(SVC_H).send({})
    expect(res.status).toBe(200)
    expect(res.body.action).toBe("promoted")
    expect(p.skillDeployment.create).toHaveBeenCalledTimes(1)
    const data = p.skillDeployment.create.mock.calls[0][0].data
    expect(data).toMatchObject({ skill_id: "skill_x", pool_ids: ["p1"], notes: "promote", deployed_by: "ana", version: "7" })
  })

  it("falha ao gravar o marker: o promote segue 200, e o motivo é LOGADO nomeando pool e skill", async () => {
    p.skillDeployment.create.mockRejectedValue(new Error("db fora"))
    const erro = vi.spyOn(console, "error").mockImplementation(() => {})
    const res = await request(app).post("/v1/pools/p1/promote").set(SVC_H).send({})
    expect(res.status).toBe(200)
    const linhas = erro.mock.calls.map(c => String(c[0]))
    expect(linhas.some(l => l.includes("SkillDeployment NÃO registrado") && l.includes("pool=p1") && l.includes("db fora"))).toBe(true)
    erro.mockRestore()
  })
})

describe("3 · o SkillDeployment tem UM escritor no fonte, e só o PROMOTE o chama", () => {
  // PID-16: o escritor mudou de casa (`lib/slot-promotion.ts`) para o promote em lote usar
  // a MESMA mecânica. Escritor único numa lib não prova nada sobre quem registra deploy,
  // então o censo passou a ter duas metades: quem ESCREVE e quem CHAMA quem escreve.
  it("censo sobre src/: só `lib/slot-promotion.ts` cria SkillDeployment", () => {
    const raiz = path.resolve(__dirname, "..")
    const escritores: string[] = []
    const varre = (dir: string) => {
      for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
        const f = path.join(dir, e.name)
        if (e.isDirectory()) { if (e.name !== "__tests__") varre(f); continue }
        if (!f.endsWith(".ts")) continue
        const src = fs.readFileSync(f, "utf8")
        const n = (src.match(/skillDeployment\s*\.\s*(create|createMany|upsert|update|updateMany)\s*\(/g) ?? []).length
        for (let i = 0; i < n; i++) escritores.push(path.relative(raiz, f).split(path.sep).join("/"))
      }
    }
    varre(raiz)
    expect(escritores).toEqual(["lib/slot-promotion.ts"])
  })

  it("censo sobre src/: só o promote de um pool e o promote em lote chamam `recordSkillDeployment`", () => {
    const raiz = path.resolve(__dirname, "..")
    const chamadores: string[] = []
    const varre = (dir: string) => {
      for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
        const f = path.join(dir, e.name)
        if (e.isDirectory()) { if (e.name !== "__tests__") varre(f); continue }
        if (!f.endsWith(".ts")) continue
        if (/(?<!function )recordSkillDeployment\s*\(/.test(fs.readFileSync(f, "utf8"))) {
          chamadores.push(path.relative(raiz, f).split(path.sep).join("/"))
        }
      }
    }
    varre(raiz)
    expect(chamadores.sort()).toEqual(["routes/pool-slots-batch.ts", "routes/pool-slots.ts"])
  })
})
