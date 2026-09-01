/**
 * context-store-routing.test.ts — CNS-03: o escopo de uma tag do CORE é o SEGUNDO segmento.
 *
 * ── Por que este teste existe, e por que ele testa o que testa ────────────────
 *
 * A CNS-02 reservou o root `core.*` para a plataforma. Com isso o PRIMEIRO segmento
 * passou a carregar propriedade (core × tenant) em vez de escopo (session/journey/
 * customer), e o escopo desceu um nível.
 *
 * O risco que isto cria é silencioso: `core.customer.*` absorve `insight.historico.*`
 * e `pricing.*`, que roteiam para o hash do CLIENTE com **90 dias**. Se a rota não for
 * declarada, aquele prefixo cai no default (hash da SESSÃO, 4 h) e a migração move dado
 * de retenção trimestral para um hash que expira no mesmo dia — sem erro, sem log, sem
 * teste vermelho. A própria spec chegou a afirmar que "os nomes do core são todos de
 * sessão", e estava errada exatamente nesses dois.
 *
 * ⚠️ **O que faz este teste poder REPROVAR.** Uma rota que devolve o mesmo que o default
 * é decorativa por construção — um teste sobre ela passaria com a linha removida. Por
 * isso aqui só se asserta sobre as rotas que DIVERGEM do default (`core.customer.` →
 * cliente/90 d, `core.journey.` → journey/30 d), e cada uma vem com a testemunha do lado:
 * um irmão sob `core.` que continua de sessão. Sem o par, "roteou para o cliente" não
 * distingue a rota certa de um casador largo demais que mandaria TODO `core.*` para lá.
 *
 * Bateria de mutação — EXECUTADA, não afirmada (2026-09-01):
 *   · remover "core.customer." de LONG_TTL_PREFIXES     → 2 vermelhos (CS-1, CS-2)
 *   · remover "core.journey."  de JOURNEY_TTL_PREFIXES  → 1 vermelho  (CJ-1)
 *   · trocar o prefixo por "core." (casador largo)      → 3 vermelhos
 *
 * ⚠️ A terceira pega **3**, e eu havia previsto 2. O extra é o CJ-1: com o casador
 * largo, `core.journey.*` casa `isLongTtl` ANTES de `isJourneyTag` — a ordem das
 * guardas no `ttlFor` é significativa — e recebe 90 d em vez de 30 d. Ou seja, um
 * prefixo largo demais não só manda o que não devia para o hash do cliente: ele
 * também sequestra a rota da journey. O número medido fica aqui em vez da previsão,
 * porque previsão em comentário é a promessa-sem-mecanismo que este repo cataloga.
 */
import { describe, it, expect, beforeEach } from "vitest"
import { ContextStore } from "../context-store"

const TENANT = "tenant_test"
const SESSION = "sess_1"
const CUSTOMER = "cus_1"

const SESSION_TTL = 4 * 60 * 60
const JOURNEY_TTL = 30 * 24 * 60 * 60
const LONG_TTL = 90 * 24 * 60 * 60

/**
 * Redis de mentira que REGISTRA a chave e o TTL de cada escrita. O teste é sobre
 * roteamento, então é exatamente isso que precisa ser observável — asserta-se sobre o
 * efeito (para onde foi, por quanto tempo), nunca sobre o predicado interno, que seria
 * o teste reconstruindo a regra que julga.
 */
function fakeRedis() {
  const writes: Array<{ key: string; field: string; ttl?: number }> = []
  const hashes = new Map<string, Map<string, string>>()
  let lastKey = ""
  return {
    writes,
    async hset(key: string, field: string, value: string) {
      lastKey = key
      if (!hashes.has(key)) hashes.set(key, new Map())
      hashes.get(key)!.set(field, value)
      writes.push({ key, field })
      return 1
    },
    async hget(key: string, field: string) {
      return hashes.get(key)?.get(field) ?? null
    },
    async hgetall(key: string) {
      return Object.fromEntries(hashes.get(key) ?? new Map())
    },
    async expire(key: string, ttl: number) {
      const w = writes.filter(x => x.key === key).pop()
      if (w) w.ttl = ttl
      return 1
    },
    async del() { return 1 },
    async hdel() { return 1 },
    get lastKey() { return lastKey },
  } as never
}

function store(redis: unknown) {
  return new ContextStore({ redis: redis as never, tenantId: TENANT })
}

async function write(redis: unknown, tag: string): Promise<{ key: string; field: string; ttl?: number }> {
  const s = store(redis)
  await s.set(
    SESSION,
    tag,
    { value: "x", confidence: 1, source: "test", visibility: "agents_only" },
    undefined,
    CUSTOMER,
  )
  const w = (redis as { writes: Array<{ key: string; field: string; ttl?: number }> }).writes
  const last = w[w.length - 1]
  // Sem esta guarda o teste passaria por AUSÊNCIA: se `set` deixasse de escrever,
  // `last` seria undefined e cada `expect` compararia undefined com undefined.
  if (!last) throw new Error(`nenhuma escrita registrada para a tag "${tag}"`)
  return last
}

describe("CNS-03 — o escopo de uma tag do core é o SEGUNDO segmento", () => {
  let redis: ReturnType<typeof fakeRedis>
  beforeEach(() => { redis = fakeRedis() })

  it("CS-1: core.customer.* vai para o hash do CLIENTE (não o da sessão)", async () => {
    const w = await write(redis, "core.customer.plano")
    expect(w.key).toBe(`${TENANT}:ctx:customer:${CUSTOMER}`)
  })

  it("CS-2: core.customer.* recebe 90 dias — a retenção que insight.historico já tinha", async () => {
    const w = await write(redis, "core.customer.plano")
    expect(w.ttl).toBe(LONG_TTL)
  })

  it("CS-3 (testemunha): outro core.* NÃO é de cliente — fica na sessão, 4 h", async () => {
    const w = await write(redis, "core.contact.close_origin")
    expect(w.key).toBe(`${TENANT}:ctx:${SESSION}`)
    expect(w.ttl).toBe(SESSION_TTL)
  })

  it("CJ-1: core.journey.* recebe 30 dias, como journey.*", async () => {
    const w = await write(redis, "core.journey.parecer")
    expect(w.ttl).toBe(JOURNEY_TTL)
  })

  it("CJ-2 (testemunha): core.journey.* NÃO vai para o hash do cliente", async () => {
    const w = await write(redis, "core.journey.parecer")
    expect(w.key).not.toContain(":ctx:customer:")
  })

  it("REG: os prefixos que já existiam continuam roteando igual", async () => {
    expect((await write(redis, "insight.historico.resumo")).ttl).toBe(LONG_TTL)
    expect((await write(redis, "pricing.plano")).ttl).toBe(LONG_TTL)
    expect((await write(redis, "journey.resultado")).ttl).toBe(JOURNEY_TTL)
    expect((await write(redis, "session.cliente.nome")).ttl).toBe(SESSION_TTL)
  })
})
