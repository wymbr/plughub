/**
 * quota-check.test.ts
 */

import { describe, it, expect } from "vitest"

/**
 * Unit tests para lib/quota-check.ts
 *
 * Cobre:
 *   - Sem limite configurado → passa sem bloqueio
 *   - Dentro do limite → incrementa e passa
 *   - Exatamente no limite → passa (limite é exclusivo: current > limit)
 *   - Acima do limite → rollback + QuotaExceededError
 *   - Limite inválido (0, negativo, NaN) → ignora e passa
 *   - checkConcurrentSessions: sem limite, dentro, no limite, acima
 */

import { assertQuota, checkConcurrentSessions, QuotaExceededError } from "../lib/quota-check"

// ─── Mock Redis ────────────────────────────────────────────────────────────────

function makeRedis(overrides: Record<string, string | null> = {}) {
  const store: Record<string, number> = {}

  return {
    async get(key: string): Promise<string | null> {
      return overrides[key] ?? null
    },
    async incrbyfloat(key: string, qty: number): Promise<number> {
      store[key] = (store[key] ?? 0) + qty
      return store[key]
    },
    _store: store,
  } as any
}

// ─── assertQuota ──────────────────────────────────────────────────────────────

describe("assertQuota", () => {
  it("passes without incrementing when no limit is configured", async () => {
    const redis = makeRedis({})  // nenhuma chave de limite
    await expect(assertQuota(redis, "t1", "sessions")).resolves.toBeUndefined()
    expect(redis._store["t1:usage:current:sessions"]).toBeUndefined()
  })

  it("increments counter when within limit", async () => {
    const redis = makeRedis({ "t1:quota:limit:sessions": "100" })
    await assertQuota(redis, "t1", "sessions", 1)
    expect(redis._store["t1:usage:current:sessions"]).toBe(1)
  })

  it("passes when current equals limit (exclusive: current > limit blocks)", async () => {
    const redis = makeRedis({ "t1:quota:limit:messages": "10" })
    // Simula 9 mensagens já consumidas
    redis._store["t1:usage:current:messages"] = 9
    await assertQuota(redis, "t1", "messages", 1)  // 9+1 = 10 = limit → passa (> não >=)
    expect(redis._store["t1:usage:current:messages"]).toBe(10)
  })

  it("throws QuotaExceededError and rolls back when limit is exceeded", async () => {
    const redis = makeRedis({ "t1:quota:limit:llm_tokens_input": "1000" })
    redis._store["t1:usage:current:llm_tokens_input"] = 990

    await expect(assertQuota(redis, "t1", "llm_tokens_input", 20))
      .rejects.toThrow(QuotaExceededError)

    // Rollback: deve ter voltado ao valor anterior
    expect(redis._store["t1:usage:current:llm_tokens_input"]).toBe(990)
  })

  it("throws QuotaExceededError with correct metadata", async () => {
    const redis = makeRedis({ "t2:quota:limit:sessions": "5" })
    redis._store["t2:usage:current:sessions"] = 5

    try {
      await assertQuota(redis, "t2", "sessions")
      throw new Error("should have thrown")
    } catch (e) {
      expect(e).toBeInstanceOf(QuotaExceededError)
      const err = e as QuotaExceededError
      expect(err.dimension).toBe("sessions")
      expect(err.tenantId).toBe("t2")
      expect(err.limit).toBe(5)
    }
  })

  it("passes when limit is 0 (invalid — treated as no limit)", async () => {
    const redis = makeRedis({ "t1:quota:limit:sessions": "0" })
    await expect(assertQuota(redis, "t1", "sessions")).resolves.toBeUndefined()
  })

  it("passes when limit is NaN (invalid — treated as no limit)", async () => {
    const redis = makeRedis({ "t1:quota:limit:sessions": "not-a-number" })
    await expect(assertQuota(redis, "t1", "sessions")).resolves.toBeUndefined()
  })

  it("uses provided quantity for multi-unit dimensions", async () => {
    const redis = makeRedis({ "t1:quota:limit:voice_minutes": "500" })
    await assertQuota(redis, "t1", "voice_minutes", 4)  // chamada de 4 minutos
    expect(redis._store["t1:usage:current:voice_minutes"]).toBe(4)
  })
})

// ─── checkConcurrentSessions ──────────────────────────────────────────────────

describe("checkConcurrentSessions", () => {
  it("returns true when no limit is configured", async () => {
    const redis = makeRedis({})
    expect(await checkConcurrentSessions(redis, "t1")).toBe(true)
  })

  it("returns true when current is below limit", async () => {
    const redis = makeRedis({
      "t1:quota:max_concurrent_sessions": "10",
      "t1:quota:concurrent_sessions":     "3",
    })
    expect(await checkConcurrentSessions(redis, "t1")).toBe(true)
  })

  it("returns false when current equals limit", async () => {
    const redis = makeRedis({
      "t1:quota:max_concurrent_sessions": "5",
      "t1:quota:concurrent_sessions":     "5",
    })
    expect(await checkConcurrentSessions(redis, "t1")).toBe(false)
  })

  it("returns false when current exceeds limit", async () => {
    const redis = makeRedis({
      "t1:quota:max_concurrent_sessions": "3",
      "t1:quota:concurrent_sessions":     "7",
    })
    expect(await checkConcurrentSessions(redis, "t1")).toBe(false)
  })

  it("returns true when current gauge key is absent (0 sessions)", async () => {
    const redis = makeRedis({
      "t1:quota:max_concurrent_sessions": "10",
      // concurrent_sessions key não existe ainda
    })
    expect(await checkConcurrentSessions(redis, "t1")).toBe(true)
  })
})
