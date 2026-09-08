/**
 * abac-field-per-router.test.ts — MOD-06 (corte #3 da D6, 2026-09-08)
 *
 * O QUE ESTE ARQUIVO PROVA
 * ------------------------
 * Cada router de config do Agent Registry exige o campo ABAC que a SUA tela declara
 * no menu, e não um campo único para todos. Até a MOD-06 os quatro exigiam
 * `config.resources`, e as duas consequências foram medidas ao vivo:
 *
 *   PUT  /v1/skills   com o preset real do `developer` -> 403 "requires config.resources"
 *   POST /v1/channels com `config.channels`            -> 403 "requires config.resources"
 *
 * A primeira é defeito de produto: o menu do developer OFERECE o Editor de Fluxo
 * (`skill_flows.operacao`, preset admin+developer) e `skill_flows.editar` existe
 * dizendo *"Criar e editar skill flows"* — mas quem salvava era um grant admin-only.
 *
 * POR QUE CADA CASO TEM O PAR
 * ---------------------------
 * Um gate que recusasse TUDO passaria em qualquer negativo. Por isso cada rota é
 * medida duas vezes: com o campo certo (tem de ATRAVESSAR o gate) e com o campo do
 * vizinho (tem de ser recusada, NOMEANDO o campo que falta). É a troca de campo que
 * se está afirmando — não a existência de um gate.
 *
 * O corpo enviado é INVÁLIDO de propósito no caso positivo: assim o handler recusa por
 * validação (4xx que não é 401/403) sem tocar no Prisma, e o teste continua medindo o
 * PORTÃO em vez do CRUD, que tem suíte própria.
 */
import { describe, it, expect, vi } from "vitest"
import crypto from "node:crypto"
import request from "supertest"

// Mesma disciplina de ambiente da `pools.test.ts`: o segredo é escrito ANTES dos
// imports (`config.ts` lê `process.env` no import), e o token de SERVIÇO é apagado —
// com ele a requisição passaria pelo atalho de serviço e o ramo Bearer+ABAC, que é o
// que a UI usa, ficaria sem teste.
const TEST_JWT_SECRET = vi.hoisted(() => {
  const s = "segredo-de-teste-mod06"
  process.env["PLUGHUB_JWT_SECRET"] = s
  delete process.env["AGENT_REGISTRY_SERVICE_TOKEN"]
  return s
})

vi.mock("../infra/kafka", () => ({
  publishRegistryEvent:   vi.fn().mockResolvedValue(undefined),
  publishRegistryChanged: vi.fn().mockResolvedValue(undefined),
  disconnectKafka:        vi.fn().mockResolvedValue(undefined),
}))

vi.mock("../db", () => ({
  prisma: {
    pool:            { findUnique: vi.fn(), findMany: vi.fn(), create: vi.fn(), update: vi.fn() },
    skill:           { findUnique: vi.fn(), findMany: vi.fn(), create: vi.fn(), update: vi.fn(), upsert: vi.fn() },
    channel:         { findUnique: vi.fn(), findMany: vi.fn(), create: vi.fn(), update: vi.fn() },
    channelEndpoint: { findUnique: vi.fn(), findMany: vi.fn(), create: vi.fn(), update: vi.fn() },
    poolSkillSlot:   { findMany: vi.fn(), findUnique: vi.fn() },
    skillDeployment: { findMany: vi.fn(), findFirst: vi.fn() },
  },
  Prisma: { DbNull: null },
}))

const { app } = await import("../app")

/** HS256 assinado com o MESMO segredo do serviço — exercita `verifyHs256` de verdade. */
function token(modulo: string | null, campo?: string): string {
  const b64 = (o: unknown) => Buffer.from(JSON.stringify(o)).toString("base64url")
  const head = b64({ alg: "HS256", typ: "JWT" })
  const body = b64({
    sub: "user_001",
    exp: Math.floor(Date.now() / 1000) + 3600,
    ...(modulo ? { module_config: { [modulo]: { [campo!]: { access: "read_write" } } } } : {}),
  })
  const sig = crypto.createHmac("sha256", TEST_JWT_SECRET).update(`${head}.${body}`).digest("base64url")
  return `${head}.${body}.${sig}`
}

const base = { "x-tenant-id": "tenant_test", "x-user-id": "user_001" }
const post = (rota: string, tok: string) =>
  request(app).post(rota).set({ ...base, authorization: `Bearer ${tok}` }).send({ invalido: true })

describe("o campo ABAC é POR ROUTER (MOD-06)", () => {
  it("/v1/skills responde a `skill_flows.editar` — o campo que o Editor declara", async () => {
    const res = await post("/v1/skills", token("skill_flows", "editar"))
    expect(res.status).not.toBe(401)
    expect(res.status).not.toBe(403)
  })

  it("/v1/skills RECUSA `config.resources`, nomeando o campo que falta", async () => {
    const res = await post("/v1/skills", token("config", "resources"))
    expect(res.status).toBe(403)
    expect(res.body.message).toContain("skill_flows.editar")
  })

  it("/v1/channels responde a `config.channels` — o campo do menu de Canais", async () => {
    const res = await post("/v1/channels", token("config", "channels"))
    expect(res.status).not.toBe(401)
    expect(res.status).not.toBe(403)
  })

  it("/v1/channels RECUSA `config.resources`, nomeando o campo que falta", async () => {
    const res = await post("/v1/channels", token("config", "resources"))
    expect(res.status).toBe(403)
    expect(res.body.message).toContain("config.channels")
  })

  it("/v1/channel-endpoints segue o mesmo campo da tela que o edita", async () => {
    const certo = await post("/v1/channel-endpoints", token("config", "channels"))
    const errado = await post("/v1/channel-endpoints", token("config", "resources"))
    expect(certo.status).not.toBe(403)
    expect(errado.status).toBe(403)
  })

  it("/v1/pools continua em `config.resources`, e não aceita os vizinhos", async () => {
    // A testemunha que impede o corte de virar "todo mundo abre tudo": trocar o campo
    // de três routers não pode ter afrouxado o quarto.
    const certo  = await post("/v1/pools", token("config", "resources"))
    const outro  = await post("/v1/pools", token("skill_flows", "editar"))
    expect(certo.status).not.toBe(403)
    expect(outro.status).toBe(403)
    expect(outro.body.message).toContain("config.resources")
  })

  it("sem Bearer nenhum, qualquer um dos quatro recusa com 401", async () => {
    for (const rota of ["/v1/pools", "/v1/skills", "/v1/channels", "/v1/channel-endpoints"]) {
      const res = await request(app).post(rota).set(base).send({ invalido: true })
      expect(res.status, rota).toBe(401)
    }
  })
})
