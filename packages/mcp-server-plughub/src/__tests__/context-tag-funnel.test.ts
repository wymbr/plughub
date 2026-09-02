/**
 * context-tag-funnel.test.ts — ALW-02 passo 1: o funil INTEIRO.
 *
 * `context-map.stamp.test.ts` (em `@plughub/schemas`) prova a metade PURA. Este prova a
 * costura: que `writeContextTag` de fato carimba o que grava, nos DOIS destinos, e que a
 * rota e o carimbo não se atrapalham.
 *
 * Por que os dois testes existem, em vez de só este: a função pura é o que o gêmeo Python
 * vai espelhar no passo 2, e o gate comparativo roda contra ela. Testar só aqui deixaria a
 * proposição que o gate precisa medir sem asserção própria.
 *
 * ── O caso que carrega o peso ────────────────────────────────────────────────
 *
 * `journey.* grava no hash do PROCESSO **carimbado**`. Antes da ALW-02 este helper
 * prometia no docstring *"só decide a CHAVE e o TTL, nunca o conteúdo"* — a rota e o
 * conteúdo eram preocupações separadas. Ao juntá-las, o modo de falha novo é carimbar a
 * entrada da sessão e esquecer a do processo (ou o inverso), porque são dois `hset` em
 * ramos diferentes. Um teste que só olhasse o ramo da sessão ficaria verde com metade do
 * ContextStore sem carimbo — e o furo, de novo, mudo.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { writeContextTag } from "../tools/journey"
import type { RedisClient } from "../infra/redis"

/** Mapa que o config-api devolveria. Mínimo — o teste não é sobre o conteúdo do mapa. */
const MAPA = {
  mode: "audit",
  dynamic_prefixes: ["segment."],
  contexto: {
    session: { cliente: { cpf: { tipo: "cpf_br", legado: ["caller.cpf"] } } },
    journey: { pedido:  { id:  { tipo: "texto" } } },
  },
}

interface FakeRedis {
  hashes: Map<string, Map<string, string>>
  expires: Map<string, number>
}

function fakeRedis(): { redis: RedisClient; state: FakeRedis } {
  const state: FakeRedis = { hashes: new Map(), expires: new Map() }
  const redis = {
    hset: async (key: string, field: string, value: string) => {
      if (!state.hashes.has(key)) state.hashes.set(key, new Map())
      state.hashes.get(key)!.set(field, value)
      return 1
    },
    hget: async (key: string, field: string) =>
      state.hashes.get(key)?.get(field) ?? null,
    expire: async (key: string, ttl: number) => { state.expires.set(key, ttl); return 1 },
    pipeline: () => ({ hset: () => {}, exec: async () => [] }),
  } as unknown as RedisClient
  return { redis, state }
}

/** Lê o que foi gravado, já desserializado. */
function lido(state: FakeRedis, key: string, field: string): Record<string, unknown> {
  const raw = state.hashes.get(key)?.get(field)
  expect(raw, `nada gravado em ${key}[${field}]`).toBeDefined()
  return JSON.parse(raw as string) as Record<string, unknown>
}

/** Tenant novo a cada caso — `getContextMap` cacheia 60 s POR TENANT. */
let n = 0
const proximoTenant = () => `t_funnel_${++n}`

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn(async () => ({
    ok: true,
    status: 200,
    json: async () => ({ entries: { context_map: { value: MAPA } } }),
  })))
})
afterEach(() => vi.unstubAllGlobals())

const entrada = () => ({
  value:      "123.456.789-00",
  confidence: 1.0,
  source:     "test",
  visibility: "agents_only",
  updated_at: "2026-09-02T00:00:00.000Z",
})

describe("writeContextTag — carimba o que grava", () => {
  it("tag de sessão vai para o hash da sessão, CARIMBADA", async () => {
    const t = proximoTenant()
    const { redis, state } = fakeRedis()
    const r = await writeContextTag(redis, t, "sess_1", "session.cliente.cpf", entrada())

    expect(r.scope).toBe("session")
    expect(r.atributo).toEqual({ tipo: "cpf_br", origem: "canonical" })
    expect(lido(state, `${t}:ctx:sess_1`, "session.cliente.cpf")["atributo"])
      .toEqual({ tipo: "cpf_br", origem: "canonical" })
  })

  it("journey.* vai para o hash do PROCESSO, também CARIMBADA", async () => {
    // O caso que carrega o peso: são dois `hset` em ramos diferentes, e carimbar só um
    // deixaria metade do ContextStore sem selo, sem nada ficar vermelho.
    const t = proximoTenant()
    const { redis, state } = fakeRedis()
    const r = await writeContextTag(redis, t, "sess_1", "journey.pedido.id", entrada())

    expect(r.scope).toBe("journey")
    expect(r.journeyRoot).toBe("sess_1")   // sem aresta de alias: a raiz é ela mesma
    expect(lido(state, `${t}:ctx:journey:sess_1`, "journey.pedido.id")["atributo"])
      .toEqual({ tipo: "texto", origem: "canonical" })
    // O TTL do processo continua sendo aplicado — o carimbo não pode ter comido a rota.
    expect(state.expires.get(`${t}:ctx:journey:sess_1`)).toBe(30 * 24 * 3600)
  })

  it("a grafia legada grava sob o nome LEGADO e o carimbo aponta a canônica", async () => {
    // Renomear no caminho de escrita quebraria todo leitor que usa a grafia velha; o
    // carimbo é o que torna a entrada autodescritiva sem mexer na chave.
    const t = proximoTenant()
    const { redis, state } = fakeRedis()
    await writeContextTag(redis, t, "sess_1", "caller.cpf", entrada())

    expect(state.hashes.get(`${t}:ctx:sess_1`)?.has("caller.cpf")).toBe(true)
    expect(lido(state, `${t}:ctx:sess_1`, "caller.cpf")["atributo"]).toEqual({
      tipo: "cpf_br", origem: "alias", canonica: "session.cliente.cpf",
    })
  })

  it("tag NÃO cadastrada é gravada assim mesmo, com origem `unknown`", async () => {
    // D3/D9: runtime nunca recusa. Recusar trocaria vazamento por quebra muda.
    const t = proximoTenant()
    const { redis, state } = fakeRedis()
    await writeContextTag(redis, t, "sess_1", "session.nao.cadastrado", entrada())

    expect(lido(state, `${t}:ctx:sess_1`, "session.nao.cadastrado")["atributo"])
      .toEqual({ origem: "unknown" })
  })
})

describe("writeContextTag — degradação é BOUNDED e declarada", () => {
  it("config-api fora: grava do mesmo jeito, marcado como `fallback`", async () => {
    // O que NÃO pode acontecer é a escrita ficar refém da config. O que também não pode
    // é o carimbo afirmar o que o tenant declarou quando ele veio do código.
    const t = proximoTenant()
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("ECONNREFUSED") }))
    const { redis, state } = fakeRedis()

    const r = await writeContextTag(redis, t, "sess_1", "session.cliente.cpf", entrada())
    const gravado = lido(state, `${t}:ctx:sess_1`, "session.cliente.cpf")

    expect(gravado["value"]).toBe("123.456.789-00")     // a escrita ACONTECEU
    expect((r.atributo as Record<string, unknown>)["fallback"]).toBe(true)
    expect((gravado["atributo"] as Record<string, unknown>)["fallback"]).toBe(true)
  })
})

describe("writeContextTag — testemunha negativa", () => {
  it("os campos do escritor chegam intactos ao Redis", async () => {
    const t = proximoTenant()
    const { redis, state } = fakeRedis()
    await writeContextTag(redis, t, "sess_1", "session.cliente.cpf", entrada())

    const gravado = lido(state, `${t}:ctx:sess_1`, "session.cliente.cpf")
    for (const [k, v] of Object.entries(entrada())) expect(gravado[k]).toEqual(v)
  })
})
