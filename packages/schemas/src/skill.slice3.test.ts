/**
 * skill.slice3.test.ts — Identity Resolver (nível b) Slice 3
 *
 * Verifies the retomada channel-abstract fields (customer_resumable /
 * resume_policy) on the delegate and collect steps, their defaults, and the
 * profile guardrail (suspend must NOT carry them).
 */
import { describe, it, expect } from "vitest"
import { FlowStepSchema, CollectStepSchema } from "./skill"

describe("Slice 3 — customer_resumable / resume_policy on delegate", () => {
  const baseDelegate = {
    type: "delegate",
    id:   "confirmar",
    pool: "loja_checkout_io",
    on_resume:  { next: "revisar" },
    on_timeout: { next: "expirou" },
  }

  it("defaults customer_resumable=false, resume_policy=offer", () => {
    const parsed = FlowStepSchema.parse(baseDelegate)
    expect(parsed).toMatchObject({ customer_resumable: false, resume_policy: "offer" })
  })

  it("accepts customer_resumable=true + resume_policy=auto", () => {
    const parsed = FlowStepSchema.parse({
      ...baseDelegate,
      customer_resumable: true,
      resume_policy:      "auto",
    })
    expect(parsed).toMatchObject({ customer_resumable: true, resume_policy: "auto" })
  })

  it("rejects an unknown resume_policy value", () => {
    expect(() =>
      FlowStepSchema.parse({ ...baseDelegate, resume_policy: "always" }),
    ).toThrow()
  })
})

describe("Slice 3 — customer_resumable / resume_policy on collect", () => {
  const baseCollect = {
    id:     "coletar",
    type:   "collect",
    target: { type: "customer", id: "cus_1" },
    channel: "whatsapp",
    prompt: "Confirma?",
    output_as: "resp",
    on_response: { next: "ok" },
    on_timeout:  { next: "no" },
  }

  it("defaults customer_resumable=false, resume_policy=offer", () => {
    const parsed = CollectStepSchema.parse(baseCollect)
    expect(parsed).toMatchObject({ customer_resumable: false, resume_policy: "offer" })
  })

  it("accepts customer_resumable=true", () => {
    const parsed = CollectStepSchema.parse({ ...baseCollect, customer_resumable: true })
    expect(parsed.customer_resumable).toBe(true)
  })
})

describe("Slice 3 — profile guardrail: suspend does not carry the fields", () => {
  const baseSuspend = {
    type:       "suspend",
    id:         "aguardar_pagamento",
    reason:     "webhook",
    on_resume:  { next: "seguir" },
    on_timeout: { next: "expirou" },
  }

  it("strips customer_resumable from a suspend step (Zod discriminated union)", () => {
    const parsed = FlowStepSchema.parse({ ...baseSuspend, customer_resumable: true }) as Record<string, unknown>
    // suspend has no such field — it must not survive the parse.
    expect(parsed["customer_resumable"]).toBeUndefined()
  })
})
