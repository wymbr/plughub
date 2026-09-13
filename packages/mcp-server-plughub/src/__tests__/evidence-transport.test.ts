/**
 * evidence-transport.test.ts — PID-03 (2026-09-13)
 *
 * A evidência de identidade viaja como REGISTRO, e a mais recente vence — no `journey_merge`
 * e no `workflow_resume`. Medido na jornada do limite antes do fix: duas consultas provaram
 * posse e o processo ficou com a prova da PRIMEIRA, porque o merge fazia "canônica vence"
 * campo a campo. Aqui: a regra (`adoptNewerEvidence`) e o transporte do resume.
 */

import { describe, it, expect } from "vitest"
import { adoptNewerEvidence } from "../tools/journey"
import { transportEvidenceOnResume, resumedSessionOf } from "../tools/workflow"
import type { RedisClient } from "../infra/redis"

function fakeRedis() {
  const hashes = new Map<string, Map<string, string>>()
  const strings = new Map<string, string>()
  const redis = {
    hset: async (k: string, a: string | Record<string, string>, v?: string) => {
      if (!hashes.has(k)) hashes.set(k, new Map())
      const h = hashes.get(k)!
      if (typeof a === "string") h.set(a, v!)
      else for (const [f, x] of Object.entries(a)) h.set(f, x)
      return 1
    },
    hget: async (k: string, f: string) => hashes.get(k)?.get(f) ?? null,
    hgetall: async (k: string) => Object.fromEntries(hashes.get(k) ?? new Map()),
    hdel: async (k: string, ...fs: string[]) => { for (const f of fs) hashes.get(k)?.delete(f); return fs.length },
    get: async (k: string) => strings.get(k) ?? null,
    expire: async () => 1,
    pipeline: () => ({ hset: () => {}, exec: async () => [] }),
  } as unknown as RedisClient
  const put = (k: string, tag: string, value: string, updated_at = "2026-09-13T10:00:00.000Z") =>
    redis.hset(k, tag, JSON.stringify({ value, updated_at }))
  const val = (k: string, tag: string) => {
    const raw = hashes.get(k)?.get(tag)
    return raw === undefined ? undefined : (JSON.parse(raw) as { value: string }).value
  }
  return { redis, hashes, strings, put, val }
}

const E = (f: string) => `core.journey.identity.otp.${f}`
const T = "t"

async function registro(put: ReturnType<typeof fakeRedis>["put"], k: string, status: string, sessao: string, quando: string) {
  await put(k, E("status"), status, quando)
  await put(k, E("anchor_kind"), "phone", quando)
  if (status === "verified") {
    await put(k, E("verified_at"), quando, quando)
    await put(k, E("proven_in_session"), sessao, quando)
    await put(k, E("source"), "authoritative", quando)
  }
}

describe("PID-03 — adoptNewerEvidence (registro inteiro, o mais recente vence)", () => {
  it("o caso medido: a prova da segunda consulta substitui a da primeira", async () => {
    const { redis, put, val } = fakeRedis()
    await registro(put, "dst", "verified", "consulta_1", "2026-09-13T20:08:43.000Z")
    await registro(put, "src", "verified", "consulta_2", "2026-09-13T20:08:44.000Z")
    expect(await adoptNewerEvidence(redis, "src", "dst")).toEqual(["otp"])
    expect(val("dst", E("proven_in_session"))).toBe("consulta_2")
  })

  it("um failed MAIS NOVO substitui um verified velho, e leva a prova velha junto (não mistura campos)", async () => {
    const { redis, put, val } = fakeRedis()
    await registro(put, "dst", "verified", "velha", "2026-08-24T00:00:00.000Z")
    await registro(put, "src", "failed", "hoje", "2026-09-13T00:00:00.000Z")
    await adoptNewerEvidence(redis, "src", "dst")
    expect(val("dst", E("status"))).toBe("failed")
    expect([val("dst", E("verified_at")), val("dst", E("proven_in_session"))]).toEqual([undefined, undefined])
  })

  it("controle: registro MAIS VELHO na origem não viaja", async () => {
    const { redis, put, val } = fakeRedis()
    await registro(put, "dst", "verified", "nova", "2026-09-13T00:00:00.000Z")
    await registro(put, "src", "failed", "velha", "2026-09-01T00:00:00.000Z")
    expect(await adoptNewerEvidence(redis, "src", "dst")).toEqual([])
    expect(val("dst", E("status"))).toBe("verified")
  })

  it("sem registro no destino, viaja; sem status legível na origem, não viaja; mesma chave, nada", async () => {
    const { redis, put, val } = fakeRedis()
    await registro(put, "src", "verified", "s", "2026-09-13T00:00:00.000Z")
    expect(await adoptNewerEvidence(redis, "src", "vazio")).toEqual(["otp"])
    await put("so_campo", E("anchor_kind"), "phone")
    expect(await adoptNewerEvidence(redis, "so_campo", "outro")).toEqual([])
    expect(await adoptNewerEvidence(redis, "src", "src")).toEqual([])
    expect(val("vazio", E("proven_in_session"))).toBe("s")
  })
})

describe("PID-03 — workflow_resume transporta ao processo retomado", () => {
  const raiz = (redis: RedisClient, sessao: string, root: string) =>
    redis.hset(`${T}:ctx:${sessao}`, "core.contact.root_session_id", JSON.stringify({ value: root }))

  it("lê a sessão do token pelo hash e, na falta dele, pelo registro por token", async () => {
    const { redis, strings } = fakeRedis()
    await redis.hset(`${T}:resume_tokens`, "tok", "proc_b:passo:2030-01-01T00:00:00Z")
    expect(await resumedSessionOf(redis, T, "tok")).toBe("proc_b")
    strings.set(`${T}:resume_meta:tok2`, JSON.stringify({ session_id: "proc_c" }))
    expect(await resumedSessionOf(redis, T, "tok2")).toBe("proc_c")
    expect(await resumedSessionOf(redis, T, "nenhum")).toBeNull()
  })

  it("o filho de delegate que retoma leva a prova da journey do intake para a do processo", async () => {
    const { redis, put, val } = fakeRedis()
    await redis.hset(`${T}:resume_tokens`, "tok", "proc_b:passo:x")
    await raiz(redis, "intake_a", "intake_a")
    await raiz(redis, "filho_delegate", "intake_a")      // herda a raiz do chamador
    await raiz(redis, "proc_b", "proc_b")
    await registro(put, `${T}:ctx:journey:intake_a`, "verified", "intake_a", "2026-09-13T12:00:00.000Z")
    const r = await transportEvidenceOnResume(redis, T, "filho_delegate", "tok")
    expect(r).toMatchObject({ target: "proc_b", from_root: "intake_a", to_root: "proc_b", transported: ["otp"] })
    expect(val(`${T}:ctx:journey:proc_b`, E("proven_in_session"))).toBe("intake_a")
  })

  it("controle: já na mesma journey (houve merge), nada a transportar", async () => {
    const { redis, put } = fakeRedis()
    await redis.hset(`${T}:resume_tokens`, "tok", "proc_b:passo:x")
    await raiz(redis, "intake_a", "proc_b")
    await raiz(redis, "proc_b", "proc_b")
    await registro(put, `${T}:ctx:journey:proc_b`, "verified", "intake_a", "2026-09-13T12:00:00.000Z")
    const r = await transportEvidenceOnResume(redis, T, "intake_a", "tok")
    expect(r.transported).toEqual([])
    expect(r.from_root).toBe(r.to_root)
  })
})
