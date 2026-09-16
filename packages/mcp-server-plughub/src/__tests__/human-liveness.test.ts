/**
 * human-liveness.test.ts — AGH-02: o agente humano sem conexão viva sai do pool, e o contato
 * que ele atendia não fica preso a ele. E, tão importante quanto, quem está vivo NÃO sai.
 *
 * O caso que motivou: reinício do mcp-server derrubando os sockets sem `close`; a instância
 * `human-*` ficou `ready`, sem TTL, recebendo contato de dois probes (2026-09-15).
 */

import { beforeEach, describe, expect, it } from "vitest"
import RedisMock from "ioredis-mock"
import {
  HUMAN_LIVENESS_TTL_S, humanInstanceIdFromKey, livenessKey, occupantSessionsOfPool,
  sweepAllowed, sweepHumanGhosts, type LeaveStatus, type SweepRedis,
} from "../lib/human-liveness"

const T = "tenant_t"
const redis = new RedisMock() as unknown as SweepRedis & {
  flushall(): Promise<unknown>; set(...a: unknown[]): Promise<unknown>; sadd(k: string, ...m: string[]): Promise<number>; ttl(k: string): Promise<number>
}

async function instancia(id: string, pools: string[], userId = id.slice(6)) {
  await redis.set(`${T}:instance:${id}`, JSON.stringify({ instance_id: id, user_id: userId, pools }))
}

function deps(over: { live?: Array<[string, string]>; leaveStatus?: LeaveStatus } = {}) {
  const published: Array<Record<string, unknown>> = []
  const leaves: Array<[string, string, string]> = []
  const live = new Set((over.live ?? []).map(([u, p]) => `${u}::${p}`))
  return {
    published, leaves,
    d: {
      redis, tenantId: T,
      kafka: { publish: async (_t: string, p: Record<string, unknown>) => { published.push(p) } },
      hasLiveConnection: (u: string, p: string) => live.has(`${u}::${p}`),
      leave: async (p: string, u: string, g: string) => { leaves.push([p, u, g]); return over.leaveStatus ?? "full_logout" },
    },
  }
}

beforeEach(async () => { await redis.flushall() })

describe("sweepHumanGhosts — fantasma sai", () => {
  it("instância sem liveness e sem conexão local sai do pool, com a liveness como guarda", async () => {
    await instancia("human-g", ["p1"])
    const { d, leaves } = deps()
    const r = await sweepHumanGhosts(d)
    expect(leaves).toEqual([["p1", "g", livenessKey(T, "human-g", "p1")]])
    expect(r.swept.map((s) => s.pool_id)).toEqual(["p1"])
  })

  it("o contato que o fantasma atendia NESTE pool recebe agent_disconnect (o caso do C1)", async () => {
    await instancia("human-g", ["p1"])
    await redis.sadd(`${T}:instance:human-g:sessions`, "s1::::p1")
    await redis.sadd("session:s1:human_agents", "human-g")
    const { d, published } = deps()
    await sweepHumanGhosts(d)
    expect(published).toEqual([
      { event_type: "contact_closed", session_id: "s1", instance_id: "human-g", reason: "agent_disconnect" },
    ])
  })

  it("só a sessão do pool varrido, e só se o humano ainda está nela", async () => {
    await instancia("human-g", ["p1", "p2"])
    await redis.set(livenessKey(T, "human-g", "p2"), "1", "EX", 60)   // vivo em p2
    await redis.sadd(`${T}:instance:human-g:sessions`, "s1::::p1", "s2::::p2", "s3::::p1", "__wrapup_hold__::o::p1::9")
    await redis.sadd("session:s1:human_agents", "human-g")
    await redis.sadd("session:s2:human_agents", "human-g")            // s3: já saiu por agent_done
    const { d, published, leaves } = deps({ leaveStatus: "partial" })
    await sweepHumanGhosts(d)
    expect(leaves.map((l) => l[0])).toEqual(["p1"])
    expect(published.map((p) => p["session_id"])).toEqual(["s1"])
  })
})

describe("sweepHumanGhosts — vivo fica", () => {
  it("liveness presente: nenhuma saída, nenhum evento", async () => {
    await instancia("human-l", ["p1"])
    await redis.sadd(`${T}:instance:human-l:sessions`, "s1::::p1")
    await redis.sadd("session:s1:human_agents", "human-l")
    await redis.set(livenessKey(T, "human-l", "p1"), "1", "EX", 60)
    const { d, leaves, published } = deps()
    await sweepHumanGhosts(d)
    expect(leaves).toEqual([])
    expect(published).toEqual([])
  })

  it("chave ausente mas conexão viva neste processo: reafirma a chave, não sai", async () => {
    await instancia("human-l", ["p1"])
    const { d, leaves } = deps({ live: [["l", "p1"]] })
    const r = await sweepHumanGhosts(d)
    expect(leaves).toEqual([])
    expect(r.renewed_local).toEqual([{ instance_id: "human-l", pool_id: "p1" }])
    const ttl = await redis.ttl(livenessKey(T, "human-l", "p1"))
    expect(ttl).toBeGreaterThan(0)
    expect(ttl).toBeLessThanOrEqual(HUMAN_LIVENESS_TTL_S)
  })

  it("a saída voltou `alive` (reconectou entre a leitura e o script): nenhum agent_disconnect", async () => {
    await instancia("human-g", ["p1"])
    await redis.sadd(`${T}:instance:human-g:sessions`, "s1::::p1")
    await redis.sadd("session:s1:human_agents", "human-g")
    const { d, published } = deps({ leaveStatus: "alive" })
    await sweepHumanGhosts(d)
    expect(published).toEqual([])
  })

  it("o semáforo `:sessions` e instância de IA não são instâncias humanas a varrer", async () => {
    await instancia("human-g", ["p1"])
    await redis.sadd(`${T}:instance:human-g:sessions`, "s1::::p1")
    await redis.set(`${T}:instance:agente_x-001`, JSON.stringify({ pools: ["p1"] }))
    const { d, leaves } = deps()
    const r = await sweepHumanGhosts(d)
    expect(r.scanned).toBe(1)
    expect(leaves.length).toBe(1)
  })
})

describe("funções puras", () => {
  it("carência de boot: não varre antes de um TTL inteiro", () => {
    expect(sweepAllowed(0, HUMAN_LIVENESS_TTL_S * 1000 - 1)).toBe(false)
    expect(sweepAllowed(0, HUMAN_LIVENESS_TTL_S * 1000)).toBe(true)
  })

  it("chave de instância × semáforo × outro tenant", () => {
    expect(humanInstanceIdFromKey(T, `${T}:instance:human-a`)).toBe("human-a")
    expect(humanInstanceIdFromKey(T, `${T}:instance:human-a:sessions`)).toBeNull()
    expect(humanInstanceIdFromKey(T, `outro:instance:human-a`)).toBeNull()
  })

  it("vaga segura de wrap-up não é contato, mesmo com o pool no 3º campo", () => {
    expect(occupantSessionsOfPool(["__wrapup_hold__::origem::p1::9999", "s1::::p1"], "p1")).toEqual(["s1"])
  })

  it("ocupante sem pool (anterior à F1) conta para o pool varrido", () => {
    expect(occupantSessionsOfPool(["s0", "s1::c::p2"], "p1")).toEqual(["s0"])
  })
})
