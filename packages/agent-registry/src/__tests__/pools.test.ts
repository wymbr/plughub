/**
 * pools.test.ts
 * Testes das rotas de pools — validação e CRUD.
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import crypto from "node:crypto"
import request from "supertest"

// ── AMBIENTE DECLARADO (AUT-24, 2026-08-31) ─────────────────────────────────
//
// ⚠️ Ate aqui o veredicto desta suite dependia de ambiente NAO DECLARADO, e errava dos
// DOIS lados:
//   · na maquina de quem nao exporta `PLUGHUB_JWT_SECRET`, o `requireResourceWrite`
//     vira no-op e a suite ficava VERDE **sem nunca exercer o portao** — a familia
//     "teste que nao pode reprovar";
//   · dentro do container, onde o segredo existe, as mesmas 6 assercoes viravam
//     `expected 401 to be 201` — vermelho permanente que ensina a ignorar a suite.
//
// `config.ts` le `process.env` no import, e `../app` importa `config`. Logo o env tem
// de ser escrito ANTES dos imports: `vi.hoisted` roda antes deles, um `beforeAll` nao.
//
// O `AGENT_REGISTRY_SERVICE_TOKEN` e limpo DE PROPOSITO: com ele a requisicao passaria
// pelo atalho de servico e o ramo Bearer+ABAC — que e o que a UI usa — ficaria sem
// teste. Consertar o vermelho pelo caminho mais curto teria apagado a cobertura.
const TEST_JWT_SECRET = vi.hoisted(() => {
  const s = "segredo-de-teste-aut24"
  process.env["PLUGHUB_JWT_SECRET"] = s
  delete process.env["AGENT_REGISTRY_SERVICE_TOKEN"]
  return s
})

const { app } = await import("../app")

/** HS256 assinado com o MESMO segredo do serviço — exercita `verifyHs256` de verdade,
 *  em vez de mockar a verificação (que testaria o mock, não o portão). */
function mintToken(access: string | null = "read_write"): string {
  const b64 = (o: unknown) => Buffer.from(JSON.stringify(o)).toString("base64url")
  const head = b64({ alg: "HS256", typ: "JWT" })
  const body = b64({
    sub: "user_001",
    exp: Math.floor(Date.now() / 1000) + 3600,
    ...(access ? { module_config: { config: { resources: { access } } } } : {}),
  })
  const sig = crypto.createHmac("sha256", TEST_JWT_SECRET).update(`${head}.${body}`).digest("base64url")
  return `${head}.${body}.${sig}`
}

// Mock do Kafka — evita conexão real a localhost:9092 nos unit tests.
//
// ⚠️ `publishRegistryChanged` FALTAVA aqui (AUT-24, 2026-08-31). `pools.ts` a importa
// desde o arco de slots; sem ela no mock, o `POST` estourava TypeError e devolvia 500.
// Ninguém viu porque o 401 do portão de autorização parava a requisição ANTES do
// handler — **o vermelho de cima escondia o vermelho de baixo**. É a razão de suíte
// vermelha ser pior que suíte nenhuma: ela guarda falhas de naturezas diferentes umas
// atrás das outras.
vi.mock("../infra/kafka", () => ({
  publishRegistryEvent:   vi.fn().mockResolvedValue(undefined),
  publishRegistryChanged: vi.fn().mockResolvedValue(undefined),
  disconnectKafka:        vi.fn().mockResolvedValue(undefined),
}))

// Mock do Prisma — inclui Prisma.DbNull usado nos campos JSON opcionais.
// `poolSkillSlot`/`skillDeployment` também faltavam: o `GET /v1/pools` anexa o slot de
// deploy vivo desde a Fase 3b, e sem o modelo no mock a rota inteira ia a 500.
vi.mock("../db", () => ({
  prisma: {
    pool: {
      findUnique: vi.fn(),
      findMany:   vi.fn(),
      create:     vi.fn(),
      update:     vi.fn(),
    },
    poolSkillSlot:   { findMany: vi.fn(), findUnique: vi.fn() },
    skillDeployment: { findMany: vi.fn(), findFirst: vi.fn() },
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

// Credencial de OPERADOR: `config.resources` read_write, que e o que a tela usa.
const headers = {
  "x-tenant-id": "tenant_test",
  "x-user-id":   "user_001",
  authorization: `Bearer ${mintToken()}`,
}

beforeEach(() => {
  vi.clearAllMocks()
  // Default explicito: sem ele um `findMany` limpo devolve `undefined` e a rota quebra
  // com um 500 que parece defeito do produto. Vazio e o estado honesto de um pool sem
  // deploy.
  vi.mocked((prisma as unknown as { poolSkillSlot: { findMany: ReturnType<typeof vi.fn> } })
    .poolSkillSlot.findMany).mockResolvedValue([])
})

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

// ── O portao em si (AUT-24) ─────────────────────────────────────────────────
//
// Estes tres casos NAO existiam. Sem eles, apagar o `requireResourceWrite` do
// `app.ts` deixaria a suite inteira VERDE — os testes acima passariam por nao
// precisarem mais de credencial. Cobrir so o caminho feliz mede o handler, nunca a
// fronteira.
describe("POST /v1/pools — o portao `config.resources`", () => {
  it("SEM credencial recusa com 401", async () => {
    const res = await request(app)
      .post("/v1/pools")
      .set({ "x-tenant-id": "tenant_test", "x-user-id": "user_001" })
      .send(validPool)

    expect(res.status).toBe(401)
    expect(prisma.pool.create).not.toHaveBeenCalled()
  })

  it("com token SEM `config.resources` recusa com 403", async () => {
    const res = await request(app)
      .post("/v1/pools")
      .set({ ...headers, authorization: `Bearer ${mintToken(null)}` })
      .send(validPool)

    expect(res.status).toBe(403)
    expect(prisma.pool.create).not.toHaveBeenCalled()
  })

  it("com `read_only` recusa: o rank exige read_write", async () => {
    const res = await request(app)
      .post("/v1/pools")
      .set({ ...headers, authorization: `Bearer ${mintToken("read_only")}` })
      .send(validPool)

    expect(res.status).toBe(403)
  })
})

// LEITURA fica aberta por decisao (a tela de Access lista todos os pools do tenant
// para poder ATRIBUI-LOS). Sem esta testemunha, alguem "endurece" o gate para GET e
// quebra a atribuicao de escopo sem nada ficar vermelho.
describe("GET /v1/pools — leitura NAO exige credencial, por decisao", () => {
  it("sem Authorization ainda responde 200", async () => {
    vi.mocked(prisma.pool.findMany).mockResolvedValue([dbPool] as never)

    const res = await request(app)
      .get("/v1/pools")
      .set({ "x-tenant-id": "tenant_test" })

    expect(res.status).toBe(200)
  })
})
