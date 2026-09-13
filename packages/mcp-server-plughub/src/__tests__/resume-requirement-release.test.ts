/**
 * resume-requirement-release.test.ts — PID-06 (2026-09-13)
 *
 * O `resume_token` só sai para quem provou NESTA sessão o que a pendência exige. Medido
 * antes do fix, pelo transporte real: uma sessão que nunca fez OTP recebeu o token de um
 * cliente cujo celular outra sessão tinha provado — a liberação só olhava a posse DURÁVEL.
 * Cada recusa tem o seu controle positivo: um portão que retivesse tudo passaria nelas.
 */
import { describe, it, expect } from "vitest"
import { withholdUnprovenResume } from "../tools/workflow"
import type { RedisClient } from "../infra/redis"

function fakeRedis() {
  const hashes = new Map<string, Map<string, string>>()
  const redis = {
    hget:    async (k: string, f: string) => hashes.get(k)?.get(f) ?? null,
    hgetall: async (k: string) => Object.fromEntries(hashes.get(k) ?? new Map()),
    get:     async () => null,
  } as unknown as RedisClient
  const put = (k: string, tag: string, value: string) => {
    if (!hashes.has(k)) hashes.set(k, new Map())
    hashes.get(k)!.set(tag, JSON.stringify({ value, updated_at: "2026-09-13T11:58:00.000Z" }))
  }
  return { redis, put }
}

const T = "t"
const NOW = Date.parse("2026-09-13T12:00:00Z")
const E = (f: string) => `core.journey.identity.otp.${f}`

function provou(put: ReturnType<typeof fakeRedis>["put"], sessao: string, quando = "2026-09-13T11:58:00.000Z") {
  const k = `${T}:ctx:journey:${sessao}`   // sem root no ctx, a raiz da journey é a própria sessão
  put(k, E("status"), "verified")
  put(k, E("verified_at"), quando)
  put(k, E("proven_in_session"), sessao)
}

const pend = (token: string, resume_requires?: unknown) => ({
  session_id: `proc-${token}`, resume_token: token, pool: "aprovacao", policy: "offer",
  context_preview: { status: "x" }, root_session_id: `root-${token}`,
  ...(resume_requires !== undefined ? { resume_requires } : {}),
})
const resposta = (...pendings: ReturnType<typeof pend>[]) => ({
  customer_id: "cus_1", found: pendings.length > 0, count: pendings.length, pendings,
  ...(pendings[0] ? { resume_token: pendings[0].resume_token, pool: "aprovacao" } : {}),
})
const tokensDe = (b: Record<string, unknown>) =>
  [b["resume_token"], ...((b["pendings"] as Array<{ resume_token: string }> | undefined) ?? []).map(p => p.resume_token)].filter(Boolean)

describe("PID-06 — liberação do token contra a evidência DA SESSÃO", () => {
  it("o caso medido: S1 provou, S2 pede a mesma pendência — S2 não leva token", async () => {
    const { redis, put } = fakeRedis()
    provou(put, "S1")
    const r = await withholdUnprovenResume(redis, T, "S2", resposta(pend("tk1", ["otp"])), NOW)
    expect(tokensDe(r)).toEqual([])
    expect(r).toMatchObject({ found: false, verification_required: true, identity_required: ["otp"], customer_id: "cus_1" })
  })

  it("controle: a sessão que provou leva o token", async () => {
    const { redis, put } = fakeRedis()
    provou(put, "S1")
    const r = await withholdUnprovenResume(redis, T, "S1", resposta(pend("tk1", ["otp"])), NOW)
    expect(tokensDe(r)).toContain("tk1")
    expect(r["found"]).toBe(true)
  })

  it("prova vencida não libera", async () => {
    const { redis, put } = fakeRedis()
    provou(put, "S1", "2026-09-13T10:00:00.000Z")
    const r = await withholdUnprovenResume(redis, T, "S1", resposta(pend("tk1", ["otp"])), NOW)
    expect(tokensDe(r)).toEqual([])
  })

  it("pendência sem exigência, ou com [], passa intacta — inclusive para quem não provou", async () => {
    const { redis } = fakeRedis()
    const semCampo = resposta(pend("tk1"))
    expect(await withholdUnprovenResume(redis, T, "S2", semCampo, NOW)).toBe(semCampo)
    const vazia = resposta(pend("tk1", []))
    expect(await withholdUnprovenResume(redis, T, "S2", vazia, NOW)).toBe(vazia)
  })

  it("lista mista: retém só a que exige, e a vista achatada passa a ser a da liberada", async () => {
    const { redis } = fakeRedis()
    const r = await withholdUnprovenResume(redis, T, "S2", resposta(pend("tk-exige", ["otp"]), pend("tk-livre")), NOW)
    expect(tokensDe(r)).toEqual(["tk-livre", "tk-livre"])
    expect(r).toMatchObject({ found: true, count: 1, resume_token: "tk-livre", root_session_id: "root-tk-livre", withheld: 1 })
  })

  it("porta legada (resposta sem `pendings`) recebe o mesmo julgamento", async () => {
    const { redis, put } = fakeRedis()
    provou(put, "S1")
    const legado = { found: true, resume_token: "tk-legado", context: {}, resume_requires: ["otp"] }
    expect(tokensDe(await withholdUnprovenResume(redis, T, "S2", legado, NOW))).toEqual([])
    expect(tokensDe(await withholdUnprovenResume(redis, T, "S1", legado, NOW))).toEqual(["tk-legado"])
  })

  it("sem Redis, pendência com exigência é retida (falha fechada); sem exigência, passa", async () => {
    const exige = await withholdUnprovenResume(undefined, T, "S1", resposta(pend("tk1", ["otp"])), NOW)
    expect(tokensDe(exige)).toEqual([])
    const livre = resposta(pend("tk1"))
    expect(await withholdUnprovenResume(undefined, T, "S1", livre, NOW)).toBe(livre)
  })

  it("a prova vive na journey da sessão: sessão filha de uma raiz enxerga a prova da raiz se foi ELA quem provou", async () => {
    const { redis, put } = fakeRedis()
    // S3 tem root R; a prova está na journey R, feita por S3
    put(`${T}:ctx:S3`, "core.contact.root_session_id", "R")
    const k = `${T}:ctx:journey:R`
    put(k, E("status"), "verified"); put(k, E("verified_at"), "2026-09-13T11:58:00.000Z"); put(k, E("proven_in_session"), "S3")
    const r = await withholdUnprovenResume(redis, T, "S3", resposta(pend("tk1", ["otp"])), NOW)
    expect(tokensDe(r)).toContain("tk1")
  })
})
