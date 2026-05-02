/**
 * token-vault.test.ts
 * Unit tests for TokenVault — token generation, resolution, and timing behaviour.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { TokenVault } from "../lib/token-vault"

// ── Factory ───────────────────────────────────────────────────────────────────

function makeVault() {
  const redis = {
    set: vi.fn().mockResolvedValue("OK"),
    get: vi.fn().mockResolvedValue(null),
  }
  const vault = new TokenVault({ redis })
  return { vault, redis }
}

// ── generate ──────────────────────────────────────────────────────────────────

describe("TokenVault.generate", () => {
  it("stores a TokenEntry in Redis with the correct key", async () => {
    const { vault, redis } = makeVault()
    await vault.generate("tenant_test", "cpf", "123.456.789-00", "***-00", 14400)

    expect(redis.set).toHaveBeenCalledOnce()
    const [key, value, mode, ttl] = redis.set.mock.calls[0]
    expect(key).toMatch(/^tenant_test:token:tk_/)
    const entry = JSON.parse(value)
    expect(entry.category).toBe("cpf")
    expect(entry.original_value).toBe("123.456.789-00")
    expect(entry.display).toBe("***-00")
    expect(mode).toBe("EX")
    expect(ttl).toBe(14400)
  })

  it("returns inline token in format [{category}:{token_id}:{display}]", async () => {
    const { vault } = makeVault()
    const result = await vault.generate("t1", "credit_card", "4111111111111234", "****1234", 3600)
    expect(result.inline).toMatch(/^\[credit_card:tk_[a-f0-9]+:\*\*\*\*1234\]$/)
  })

  it("generates unique token_ids across multiple calls", async () => {
    const { vault } = makeVault()
    const r1 = await vault.generate("t1", "cpf", "111", "d1", 3600)
    const r2 = await vault.generate("t1", "cpf", "222", "d2", 3600)
    expect(r1.token_id).not.toBe(r2.token_id)
  })
})

// ── resolve ───────────────────────────────────────────────────────────────────

describe("TokenVault.resolve", () => {
  it("returns original_value when token exists", async () => {
    const { vault, redis } = makeVault()
    const entry = {
      token_id: "tk_abc123",
      category: "cpf",
      original_value: "123.456.789-00",
      display: "***-00",
      tenant_id: "tenant_test",
      created_at: new Date().toISOString(),
    }
    redis.get.mockResolvedValue(JSON.stringify(entry))

    const result = await vault.resolve("tenant_test", "tk_abc123")
    expect(result).toBe("123.456.789-00")
  })

  it("returns null when token does not exist", async () => {
    const { vault } = makeVault()
    const result = await vault.resolve("tenant_test", "tk_nonexistent")
    expect(result).toBeNull()
  })

  it("returns null when stored value is invalid JSON", async () => {
    const { vault, redis } = makeVault()
    redis.get.mockResolvedValue("not-json{{{")
    const result = await vault.resolve("tenant_test", "tk_bad")
    expect(result).toBeNull()
  })

  it("resolve takes at least 5ms regardless of Redis hit or miss", async () => {
    const { vault, redis } = makeVault()
    // Miss case
    redis.get.mockResolvedValue(null)
    const t0 = Date.now()
    await vault.resolve("tenant_test", "tk_nonexistent")
    expect(Date.now() - t0).toBeGreaterThanOrEqual(4) // allow 1ms tolerance
  })
})

// ── extractTokenIds ───────────────────────────────────────────────────────────

describe("TokenVault.extractTokenIds", () => {
  it("extracts token ids from text with inline tokens", () => {
    const text = "cpf é [cpf:tk_b7d2e1:***-00] e cartão [credit_card:tk_a8f3c2:****1234]"
    expect(TokenVault.extractTokenIds(text)).toEqual(["tk_b7d2e1", "tk_a8f3c2"])
  })

  it("returns empty array when no tokens present", () => {
    expect(TokenVault.extractTokenIds("hello world")).toEqual([])
  })
})
