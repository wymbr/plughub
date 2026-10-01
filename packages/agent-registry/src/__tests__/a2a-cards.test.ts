/**
 * a2a-cards.test.ts — AAS-03 (2026-10-01): o AgentCard é projeção do pool, servido por
 * endereço `a2a` registrado, e só quando o pool pode de fato atender.
 *
 * PROPOSIÇÃO: `GET /v1/a2a-cards/:slug` devolve o card SÓ para endpoint a2a ativo e externo,
 * de pool de contato ativo com o canal, descritor válido e DESCOBRÍVEL, e deploy `current`;
 * cada recusa diz o motivo. E o cadastro de endpoint a2a recusa slug fora de forma e pool que
 * não expõe A2A — no create e na troca de pool.
 */
import { describe, it, expect, vi, beforeEach } from "vitest"
import crypto from "node:crypto"
import request from "supertest"

const TEST_JWT_SECRET = vi.hoisted(() => {
  const s = "segredo-de-teste-aas03"
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
    poolSkillSlot:   { findMany: vi.fn(), findUnique: vi.fn() },
    skillDeployment: { findMany: vi.fn(), findFirst: vi.fn() },
    channelEndpoint: { findFirst: vi.fn(), findMany: vi.fn(), create: vi.fn(), update: vi.fn() },
  },
  Prisma: { DbNull: null },
}))

const { app }    = await import("../app")
const { prisma } = await import("../db")
const ep_ = (prisma as unknown as { channelEndpoint: Record<string, ReturnType<typeof vi.fn>> }).channelEndpoint

function mintToken(): string {
  const b64 = (o: unknown) => Buffer.from(JSON.stringify(o)).toString("base64url")
  const head = b64({ alg: "HS256", typ: "JWT" })
  const body = b64({
    sub: "user_001", tenant_id: "tenant_test", exp: Math.floor(Date.now() / 1000) + 3600,
    module_config: { config: { channels: { access: "read_write" } } },
  })
  const sig = crypto.createHmac("sha256", TEST_JWT_SECRET).update(`${head}.${body}`).digest("base64url")
  return `${head}.${body}.${sig}`
}

const BASE = "https://atende.exemplo.com.br"
const descritor = {
  display_name:    "Segunda via de boleto",
  description:     "Emite a segunda via de um boleto em aberto.",
  input_schema:    { type: "object", properties: { cpf: { type: "string" } }, required: ["cpf"] },
  output_schema:   { type: "object", properties: { linha_digitavel: { type: "string" } } },
  skills:          [{ id: "segunda_via", name: "Segunda via", description: "Emite.", tags: ["boleto"], examples: ["quero a 2a via"] }],
  discoverable:    true,
  principal_kinds: ["partner", "customer_agent"],
}
const endpoint = { id: "ep1", tenant_id: "tenant_test", channel: "a2a", identifier: "segunda-via",
  pool_id: "segunda_via", display_name: "2a via", active: true, origin: "external" }
const pool = { pool_id: "segunda_via", tenant_id: "tenant_test", status: "active", purpose: "contact",
  agent_kind: "ai", channel_types: ["a2a"], a2a: descritor }
const SET_AT = new Date("2026-09-30T12:00:00.000Z")
const current = { pool_id: "segunda_via", slot: "current", skill_id: "skill_segunda_via", set_at: SET_AT }

function arma(over: { ep?: unknown; pool?: unknown; slot?: unknown } = {}) {
  ep_["findFirst"]!.mockResolvedValue("ep" in over ? over.ep : endpoint)
  vi.mocked(prisma.pool.findUnique).mockResolvedValue(("pool" in over ? over.pool : pool) as never)
  vi.mocked((prisma as any).poolSkillSlot.findUnique).mockResolvedValue("slot" in over ? over.slot : current)
}
const card = (slug = "segunda-via", base: string | null = BASE) =>
  request(app).get(`/v1/a2a-cards/${slug}`).query(base === null ? {} : { base_url: base }).set("x-tenant-id", "tenant_test")

beforeEach(() => vi.clearAllMocks())

describe("GET /v1/a2a-cards/:slug — o card é projeção", () => {
  it("controle: endereço completo devolve o card com a forma do proto v1.0", async () => {
    arma()
    const r = await card()
    expect(r.status).toBe(200)
    expect(r.body.name).toBe(descritor.display_name)
    expect(r.body.version).toBe(SET_AT.toISOString())                       // D2: versão = set_at do current
    expect(r.body.supportedInterfaces).toEqual([
      { url: `${BASE}/a2a/segunda-via/`, protocolBinding: "JSONRPC", protocolVersion: "1.0" }])
    expect(r.body.capabilities.extendedAgentCard).toBe(false)               // até AAS-04
    expect(r.body.capabilities.extensions[0].params).toEqual({
      input_schema: descritor.input_schema, output_schema: descritor.output_schema })
    expect(Object.keys(r.body.securitySchemes)).toEqual(["partner", "customer_agent"])
    expect(r.body.securitySchemes.partner).toEqual({ httpAuthSecurityScheme: expect.objectContaining({ scheme: "Bearer" }) })
    expect(r.body.securityRequirements).toEqual([
      { schemes: { partner: { list: [] } } }, { schemes: { customer_agent: { list: [] } } }])
    expect(r.body.defaultInputModes).toEqual(["text/plain", "application/json"])
    expect(r.body.skills).toEqual([{ id: "segunda_via", name: "Segunda via", description: "Emite.",
      tags: ["boleto"], examples: ["quero a 2a via"] }])
    // pergunta pelo endereço DO CANAL a2a — nunca por outro canal com o mesmo slug
    expect(ep_["findFirst"]).toHaveBeenCalledWith({ where: { tenant_id: "tenant_test", channel: "a2a", identifier: "segunda-via" } })
  })

  it("a barra final da base não duplica na URL da interface", async () => {
    arma()
    const r = await card("segunda-via", `${BASE}/`)
    expect(r.body.supportedInterfaces[0].url).toBe(`${BASE}/a2a/segunda-via/`)
  })

  it.each([
    ["endpoint_not_found",    { ep: null }],
    ["endpoint_inactive",     { ep: { ...endpoint, active: false } }],
    ["endpoint_not_external", { ep: { ...endpoint, origin: "internal" } }],
    ["pool_not_found",        { pool: null }],
    ["pool_inactive",         { pool: { ...pool, status: "inactive" } }],
    ["pool_not_contact",      { pool: { ...pool, purpose: "internal" } }],
    ["channel_absent",        { pool: { ...pool, channel_types: ["webchat"] } }],
    ["descriptor_absent",     { pool: { ...pool, a2a: null } }],
    ["descriptor_absent",     { pool: { ...pool, a2a: { ...descritor, input_schema: {} } } }],
    ["not_discoverable",      { pool: { ...pool, a2a: { ...descritor, discoverable: false } } }],
    ["no_current_deploy",     { slot: null }],
    ["no_current_deploy",     { slot: { ...current, skill_id: null } }],
  ])("recusa %s com 404 e o motivo", async (reason, over) => {
    arma(over)
    const r = await card()
    expect(r.status).toBe(404)
    expect(r.body.reason).toBe(reason)
  })

  it("descritor sem `discoverable` gravado vale como NÃO descobrível (default do schema)", async () => {
    const { discoverable: _d, ...semFlag } = descritor
    arma({ pool: { ...pool, a2a: semFlag } })
    expect((await card()).body.reason).toBe("not_discoverable")
  })

  it("base_url ausente ou não http(s) é 400 — erro de quem chama, não 'não existe'", async () => {
    arma()
    expect((await card("segunda-via", null)).status).toBe(400)
    expect((await card("segunda-via", "ftp://x")).status).toBe(400)
  })

  it("slug fora de forma é 404 sem consultar o banco", async () => {
    arma()
    const r = await card("Segunda%20Via")
    expect(r.status).toBe(404)
    expect(ep_["findFirst"]).not.toHaveBeenCalled()
  })
})

describe("POST/PUT /v1/channel-endpoints — endereço a2a só para pool que expõe A2A", () => {
  const H = { authorization: `Bearer ${mintToken()}`, "x-tenant-id": "tenant_test" }
  const novo = { channel: "a2a", identifier: "segunda-via", pool_id: "segunda_via", display_name: "2a via" }

  it("controle: pool de contato com canal e descritor aceita (201)", async () => {
    ep_["findFirst"]!.mockResolvedValue(null)
    vi.mocked(prisma.pool.findUnique).mockResolvedValue(pool as never)
    ep_["create"]!.mockResolvedValue({ ...endpoint, token_hash: null, token_prefix: null })
    const r = await request(app).post("/v1/channel-endpoints").set(H).send(novo)
    expect(r.status).toBe(201)
  })

  it.each([
    ["slug com maiúscula e espaço", { identifier: "Segunda Via" }, {}, "a2a_slug_invalid"],
    ["slug começando por separador", { identifier: "-x" }, {}, "a2a_slug_invalid"],
    ["pool sem o canal", {}, { channel_types: ["webchat"] }, "a2a_pool_not_exposed"],
    ["pool sem descritor", {}, { a2a: null }, "a2a_pool_not_exposed"],
    ["pool interno", {}, { purpose: "internal" }, "a2a_pool_not_contact"],
  ])("recusa %s (422)", async (_n, corpo, poolOver, reason) => {
    ep_["findFirst"]!.mockResolvedValue(null)
    vi.mocked(prisma.pool.findUnique).mockResolvedValue({ ...pool, ...poolOver } as never)
    const r = await request(app).post("/v1/channel-endpoints").set(H).send({ ...novo, ...corpo })
    expect(r.status).toBe(422)
    expect(r.body.reason).toBe(reason)
    expect(ep_["create"]).not.toHaveBeenCalled()
  })

  it("canal webhook NÃO passa pela régua do a2a (controle de escopo)", async () => {
    ep_["findFirst"]!.mockResolvedValue(null)
    vi.mocked(prisma.pool.findUnique).mockResolvedValue({ ...pool, channel_types: ["webhook"], a2a: null } as never)
    ep_["create"]!.mockResolvedValue({ ...endpoint, channel: "webhook", token_hash: null, token_prefix: null })
    const r = await request(app).post("/v1/channel-endpoints").set(H)
      .send({ ...novo, channel: "webhook", identifier: "Qualquer Coisa", auth_required: false })
    expect(r.status).toBe(201)
  })

  it("PUT trocando o pool de endereço a2a para pool que não expõe A2A recusa", async () => {
    ep_["findFirst"]!.mockResolvedValue(endpoint)
    vi.mocked(prisma.pool.findUnique).mockResolvedValue({ ...pool, pool_id: "outro", a2a: null } as never)
    const r = await request(app).put("/v1/channel-endpoints/ep1").set(H).send({ pool_id: "outro" })
    expect(r.status).toBe(422)
    expect(r.body.reason).toBe("a2a_pool_not_exposed")
    expect(ep_["update"]).not.toHaveBeenCalled()
  })

  it("PUT só do nome num endereço a2a não consulta o pool (controle)", async () => {
    ep_["findFirst"]!.mockResolvedValue(endpoint)
    ep_["update"]!.mockResolvedValue({ ...endpoint, display_name: "novo" })
    const r = await request(app).put("/v1/channel-endpoints/ep1").set(H).send({ display_name: "novo" })
    expect(r.status).toBe(200)
    expect(prisma.pool.findUnique).not.toHaveBeenCalled()
  })
})
