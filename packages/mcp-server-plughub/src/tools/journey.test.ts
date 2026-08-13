/**
 * tools/journey.test.ts
 * Primeiros testes do mcp-server-plughub (o pacote não tinha NENHUM).
 *
 * Foco: o invariante do `journey_merge` — **acíclico por construção**.
 *
 * O que estes testes protegem, e por quê:
 *
 * A v1 tentava garantir a floresta ordenando as arestas por IDADE (novo→antigo). Isso
 * falhava de duas formas, e ambas eram invisíveis:
 *   • a idade vinha de `session:{root}:meta.started_at`, campo que só o adapter webchat
 *     escreve — e as raízes de journey são sessões WEBHOOK, que não o têm. O swap nunca
 *     rodava; o chamador sempre decidia o sobrevivente.
 *   • mesmo com a idade, ordenar por timestamp só evita ciclo se TODA aresta for ordenada.
 *     Um caso sem timestamp reabre o ciclo, e o guard vivia só na LEITURA (tolerava).
 *
 * Agora a aresta liga sempre RAIZ(source) → RAIZ(canonical), duas componentes disjuntas —
 * o que não pode fechar ciclo. Os testes abaixo fixam essa propriedade: em particular
 * `merge encadeado não fecha ciclo`, que é o caso que a v1 deixava passar.
 */
import { describe, it, expect, beforeEach, vi } from "vitest"
import RedisMock from "ioredis-mock"

// O JWT é ortogonal ao invariante — mockado para focar no que importa.
vi.mock("../infra/jwt", () => ({
  verifySessionToken: (token: string) => {
    if (token === "bad") throw new InvalidTokenErrorStub()
    return { tenant_id: "t1", instance_id: "agent-1", agent_type_id: "a1" }
  },
  InvalidTokenError: class InvalidTokenErrorStub extends Error {},
}))
class InvalidTokenErrorStub extends Error {}

import { registerJourneyTools, resolveJourneyRoot, aliasKey, journeyCtxKey } from "./journey"

type Handler = (input: Record<string, unknown>) => Promise<{
  isError?: true
  content: Array<{ type: "text"; text: string }>
}>

/** Payload cru — usado nos casos em que o erro É o resultado esperado. */
function parseResult(res: { content: Array<{ text: string }> }): any {
  return JSON.parse(res.content[0]!.text)
}

/**
 * Payload de SUCESSO. Falha com a mensagem da tool quando ela devolve `isError` — sem
 * isto, um erro vira um `expect(undefined)` e o teste esconde a causa (foi o que
 * aconteceu na primeira rodada: 7 falhas todas dizendo "expected undefined").
 */
function parseOk(res: { isError?: true; content: Array<{ text: string }> }): any {
  if (res.isError) throw new Error(`tool returned error: ${res.content[0]!.text}`)
  return JSON.parse(res.content[0]!.text)
}

describe("journey_merge", () => {
  let redis: any
  let published: Array<{ topic: string; msg: any }>
  let merge: Handler

  beforeEach(async () => {
    redis     = new RedisMock()
    // ioredis-mock COMPARTILHA o store entre instâncias — sem o flush, as arestas de um
    // teste vazam para o seguinte e o merge vira (corretamente) no-op `already_same_journey`,
    // fazendo o teste falhar por um motivo que não é o do código.
    await redis.flushall()
    published = []

    const kafka = {
      publish: async (topic: string, msg: unknown) => { published.push({ topic, msg }) },
    } as any

    const handlers: Record<string, Handler> = {}
    const server = {
      tool: (name: string, _desc: string, _schema: unknown, fn: Handler) => {
        handlers[name] = fn
      },
    } as any

    registerJourneyTools(server, { redis, kafka })
    merge = handlers["journey_merge"]!
  })

  const call = (source: string, canonical: string) =>
    merge({ session_token: "ok", source_root: source, canonical_root: canonical })

  // ── invariante 1: a aresta liga raízes de componentes ────────────────────────

  it("grava a aresta e publica journey.merges", async () => {
    const res = parseOk(await call("B", "A"))

    expect(res.merged).toBe(true)
    expect(res.canonical_root).toBe("A")
    expect(res.source_root).toBe("B")

    expect(await redis.hget(aliasKey("t1"), "B")).toBe("A")
    expect(published).toHaveLength(1)
    expect(published[0]!.topic).toBe("journey.merges")
    expect(published[0]!.msg.canonical_root).toBe("A")
  })

  it("merge encadeado NÃO fecha ciclo — a aresta parte da RAIZ, não do nó", async () => {
    // B → A. Agora um merge que nomeia B como canônica: um produtor ingênuo gravaria
    // A → B e fecharia o ciclo A→B→A. Resolvendo B até sua raiz (A), o par vira (C, A).
    await call("B", "A")
    const res = parseOk(await call("C", "B"))

    expect(res.merged).toBe(true)
    expect(res.canonical_root).toBe("A")          // B resolveu para A
    expect(await redis.hget(aliasKey("t1"), "C")).toBe("A")
    expect(await redis.hget(aliasKey("t1"), "A")).toBeNull()   // A continua raiz — sem ciclo

    // Todo mundo resolve para a mesma raiz.
    for (const n of ["A", "B", "C"]) {
      expect(await resolveJourneyRoot(redis, "t1", n)).toBe("A")
    }
  })

  it("merge de duas raízes JÁ na mesma journey é no-op idempotente", async () => {
    await call("B", "A")
    published.length = 0

    const res = parseOk(await call("B", "A"))
    expect(res.merged).toBe(false)
    expect(res.reason).toBe("already_same_journey")
    expect(res.canonical_root).toBe("A")
    expect(published).toHaveLength(0)   // não republica
  })

  it("une duas componentes (não só dois nós soltos)", async () => {
    await call("B", "A")   // componente {A,B}, raiz A
    await call("D", "C")   // componente {C,D}, raiz C

    const res = parseOk(await call("D", "B"))   // nós internos das duas componentes
    expect(res.merged).toBe(true)

    const root = await resolveJourneyRoot(redis, "t1", "D")
    for (const n of ["A", "B", "C", "D"]) {
      expect(await resolveJourneyRoot(redis, "t1", n)).toBe(root)
    }
  })

  it("rejeita self-merge", async () => {
    const res = await call("A", "A")
    expect(res.isError).toBe(true)
    expect(parseResult(res).error).toBe("self_merge")
  })

  // ── auth alternativa: tenant_id direto, sem JWT (native-agent invoke steps) ──

  it("aceita tenant_id direto no lugar de session_token", async () => {
    const res = parseOk(await merge({
      tenant_id: "t1", actor: "skill_limite_entrada_v1",
      source_root: "B", canonical_root: "A",
    }))

    expect(res.merged).toBe(true)
    expect(await redis.hget(aliasKey("t1"), "B")).toBe("A")
    expect(published[0]!.msg.actor).toBe("skill_limite_entrada_v1")
  })

  it("tenant_id sem actor cai no default 'skill_flow'", async () => {
    await merge({ tenant_id: "t1", source_root: "B", canonical_root: "A" })
    expect(published[0]!.msg.actor).toBe("skill_flow")
  })

  it("rejeita quando nem session_token nem tenant_id vêm preenchidos", async () => {
    const res = await merge({ source_root: "B", canonical_root: "A" })
    expect(res.isError).toBe(true)
    expect(parseResult(res).error).toBe("missing_auth")
  })

  // ── política (best-effort): sobrevivente = a mais antiga ─────────────────────

  it("sobrevivente = a mais ANTIGA quando as idades resolvem", async () => {
    // Sem stream, cai no fallback do meta (é o caminho que ainda existe).
    await redis.set("session:NEW:meta", JSON.stringify({ started_at: "2026-07-02T10:00:00Z" }))
    await redis.set("session:OLD:meta", JSON.stringify({ started_at: "2026-07-01T10:00:00Z" }))

    // O chamador nomeia a NOVA como sobrevivente — a tool corrige para a antiga.
    const res = parseOk(await call("OLD", "NEW"))
    expect(res.canonical_root).toBe("OLD")
    expect(res.source_root).toBe("NEW")
  })

  it("sem idade resolvível, a designação do chamador vale — e segue acíclico", async () => {
    const res = parseOk(await call("B", "A"))
    expect(res.canonical_root).toBe("A")          // como o chamador pediu
    expect(await resolveJourneyRoot(redis, "t1", "B")).toBe("A")
  })

  // ── contexto compartilhado segue a journey sobrevivente ──────────────────────

  it("migra o @ctx.journey.* da componente absorvida para a canônica", async () => {
    const entry = JSON.stringify({ value: "x", confidence: 1 })
    await redis.hset(journeyCtxKey("t1", "B"), "journey.pedido", entry)

    const res = parseOk(await call("B", "A"))
    expect(res.merged).toBe(true)
    expect(res.context_tags_moved).toBe(1)

    expect(await redis.hget(journeyCtxKey("t1", "A"), "journey.pedido")).toBe(entry)
    expect(await redis.exists(journeyCtxKey("t1", "B"))).toBe(0)   // origem removida
  })

  it("em colisão de tag, a journey CANÔNICA vence", async () => {
    const fromCanon = JSON.stringify({ value: "canon", confidence: 1 })
    const fromSrc   = JSON.stringify({ value: "src",   confidence: 1 })
    await redis.hset(journeyCtxKey("t1", "A"), "journey.pedido", fromCanon)
    await redis.hset(journeyCtxKey("t1", "B"), "journey.pedido", fromSrc)

    await call("B", "A")
    expect(await redis.hget(journeyCtxKey("t1", "A"), "journey.pedido")).toBe(fromCanon)
  })
})

describe("resolveJourneyRoot", () => {
  let redis: any
  beforeEach(async () => {
    redis = new RedisMock()
    await redis.flushall()   // store compartilhado entre instâncias — ver nota acima
  })

  it("raiz sem aresta resolve para si mesma", async () => {
    expect(await resolveJourneyRoot(redis, "t1", "A")).toBe("A")
  })

  it("comprime o caminho (o find é chamado no caminho quente de toda ativação)", async () => {
    // Cadeia C → B → A montada à mão (como se as arestas viessem de merges antigos).
    await redis.hset(aliasKey("t1"), "C", "B")
    await redis.hset(aliasKey("t1"), "B", "A")

    expect(await resolveJourneyRoot(redis, "t1", "C")).toBe("A")
    // Após a compressão, C aponta DIRETO para A.
    expect(await redis.hget(aliasKey("t1"), "C")).toBe("A")
  })

  it("termina mesmo com dado corrompido em ciclo (guard de profundidade)", async () => {
    // Um ciclo não deveria existir — mas se entrar por outra via, o find NÃO pode
    // travar o caminho quente. Termina no teto, sem exceção.
    await redis.hset(aliasKey("t1"), "A", "B")
    await redis.hset(aliasKey("t1"), "B", "A")

    const root = await resolveJourneyRoot(redis, "t1", "A")
    expect(["A", "B"]).toContain(root)   // termina; qual dos dois é irrelevante
  })
})
