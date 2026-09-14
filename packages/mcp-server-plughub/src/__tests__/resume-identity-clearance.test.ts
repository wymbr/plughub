/**
 * resume-identity-clearance.test.ts — PID-13 (2026-09-14)
 *
 * O `workflow_resume` julga a exigência do TOKEN contra a evidência da sessão que retoma,
 * lendo o registro por token — a mesma casa que o gateway confere. Só com prova desta
 * sessão ele atesta; o gateway recusa o resto. Controle positivo em cada recusa.
 */
import { describe, it, expect } from "vitest"
import { resumeIdentityClearance } from "../tools/workflow"
import type { RedisClient } from "../infra/redis"

const T = "t"
const NOW = Date.parse("2026-09-14T12:00:00Z")

function fakeRedis(meta: Record<string, unknown> | null) {
  const hashes = new Map<string, Map<string, string>>()
  const redis = {
    get:     async (k: string) => (k === `${T}:resume_meta:tk` && meta ? JSON.stringify(meta) : null),
    hget:    async (k: string, f: string) => hashes.get(k)?.get(f) ?? null,
    hgetall: async (k: string) => Object.fromEntries(hashes.get(k) ?? new Map()),
  } as unknown as RedisClient
  const provou = (sessao: string, quando = "2026-09-14T11:58:00.000Z") => {
    const k = `${T}:ctx:journey:${sessao}`
    const h = hashes.get(k) ?? new Map<string, string>()
    const e = (v: string) => JSON.stringify({ value: v })
    h.set("core.journey.identity.otp.status", e("verified"))
    h.set("core.journey.identity.otp.verified_at", e(quando))
    h.set("core.journey.identity.otp.proven_in_session", e(sessao))
    hashes.set(k, h)
  }
  return { redis, provou }
}

describe("PID-13 — resumeIdentityClearance", () => {
  it("token com exigência, sessão que NÃO provou → não satisfaz, nomeando o que falta", async () => {
    const { redis, provou } = fakeRedis({ session_id: "proc", resume_requires: ["otp"] })
    provou("S1")
    const c = await resumeIdentityClearance(redis, T, "S2", "tk", NOW)
    expect(c.requires).toEqual(["otp"])
    expect(c.satisfied).toBe(false)
    // S2 tem journey própria, onde nada foi provado; a prova de S1 mora na de S1
    expect(c.missing).toEqual([{ mechanism: "otp", reason: "not_verified" }])
  })

  it("controle: a sessão que provou agora → satisfaz", async () => {
    const { redis, provou } = fakeRedis({ session_id: "proc", resume_requires: ["otp"] })
    provou("S1")
    expect(await resumeIdentityClearance(redis, T, "S1", "tk", NOW)).toMatchObject({ requires: ["otp"], satisfied: true })
  })

  it("prova vencida não atesta", async () => {
    const { redis, provou } = fakeRedis({ session_id: "proc", resume_requires: ["otp"] })
    provou("S1", "2026-09-14T09:00:00.000Z")
    expect((await resumeIdentityClearance(redis, T, "S1", "tk", NOW)).satisfied).toBe(false)
  })

  it("sem registro, sem o campo, ou com [] → sem exigência (os skills que devolvem ao pai seguem)", async () => {
    for (const meta of [null, { session_id: "proc" }, { session_id: "proc", resume_requires: [] }]) {
      expect(await resumeIdentityClearance(fakeRedis(meta).redis, T, "S2", "tk", NOW))
        .toEqual({ requires: null, satisfied: true, missing: [] })
    }
  })
})
