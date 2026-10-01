/**
 * aas09-customer-agent.test.ts — AAS-09 (2026-10-01)
 *
 * `freshProofs` (quem provou a posse NESTA sessão, agora) e a política `customer_agent` do
 * descritor A2A do pool.
 */
import { describe, it, expect } from "vitest"
import { freshProofs, RESUME_EVIDENCE_MAX_AGE_S } from "./resume-requirement"
import { identityEvidenceTag } from "./identity-evidence"
import { PoolA2ADescriptorSchema } from "./agent-registry"

const NOW = Date.parse("2026-10-01T12:00:00Z")
const ent = (v: unknown) => JSON.stringify({ value: v })

function hash(mech: "otp" | "whatsapp", over: Record<string, unknown> = {}): Record<string, string> {
  const base: Record<string, unknown> = {
    status: "verified", proven_in_session: "s1", verified_at: "2026-10-01T11:59:00Z", customer_id: "c1", ...over,
  }
  const out: Record<string, string> = {}
  for (const [k, v] of Object.entries(base)) if (v !== undefined) out[identityEvidenceTag(mech, k as never)] = ent(v)
  return out
}

describe("AAS-09 — freshProofs", () => {
  it("prova verificada, desta sessão, recente e com cliente → é dele", () => {
    expect(freshProofs(hash("otp"), { sessionId: "s1", nowMs: NOW }))
      .toEqual([{ customer_id: "c1", mechanism: "otp", verified_at: "2026-10-01T11:59:00Z" }])
  })

  it.each([
    ["outra sessão", { proven_in_session: "s2" }],
    ["não verificada", { status: "failed" }],
    ["velha", { verified_at: new Date(NOW - (RESUME_EVIDENCE_MAX_AGE_S + 1) * 1000).toISOString() }],
    ["sem cliente", { customer_id: undefined }],
  ])("%s → nada", (_n, over) => {
    expect(freshProofs(hash("otp", over), { sessionId: "s1", nowMs: NOW })).toEqual([])
  })

  it("a chegada pelo WhatsApp também é prova (SATISFIED_BY.otp)", () => {
    expect(freshProofs(hash("whatsapp"), { sessionId: "s1", nowMs: NOW })[0]?.mechanism).toBe("whatsapp")
  })
})

describe("AAS-09 — política customer_agent no descritor", () => {
  const base = {
    display_name: "x", description: "y",
    input_schema: { type: "object" }, output_schema: { type: "object" },
    skills: [{ id: "s", name: "s", description: "s" }],
    principal_kinds: ["customer_agent"],
  }
  const pol = { validity_days: 7, max_active_tasks: 2, max_tasks_per_day: 20 }

  it("controle: política válida com o tipo admitido", () => {
    expect(PoolA2ADescriptorSchema.safeParse({ ...base, customer_agent: pol }).success).toBe(true)
  })

  it("ausente é aceito (o pool só não EMITE)", () => {
    expect(PoolA2ADescriptorSchema.safeParse(base).success).toBe(true)
  })

  it("política sem o tipo customer_agent é recusada", () => {
    expect(PoolA2ADescriptorSchema.safeParse({ ...base, principal_kinds: ["partner"], customer_agent: pol }).success)
      .toBe(false)
  })

  it.each([
    [{ validity_days: 31 }], [{ validity_days: 0 }], [{ max_active_tasks: 21 }], [{ max_tasks_per_day: 501 }],
    [{ validity_days: 1.5 }], [{ extra: 1 }],
  ])("fora dos tetos ou campo a mais (%j) → recusa", (over) => {
    expect(PoolA2ADescriptorSchema.safeParse({ ...base, customer_agent: { ...pol, ...over } }).success).toBe(false)
  })
})
