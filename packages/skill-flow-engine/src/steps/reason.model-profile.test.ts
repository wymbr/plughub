/**
 * reason.model-profile.test.ts — R8d.
 *
 * Garante que o `model_profile` do reason step é resolvido corretamente:
 * estático ("evaluation"), referência `$.pipeline_state.*` (revisor heterogêneo lê o
 * perfil da config da campanha injetada no pipeline_state), ausente → undefined
 * (o ai-gateway cai no default "balanced").
 */
import { describe, it, expect } from "vitest"
import { resolveModelProfile } from "./reason"
import type { ReasonStep } from "@plughub/schemas"
import type { StepContext } from "../executor"

function makeCtx(results: Record<string, unknown>): StepContext {
  return { state: { results }, sessionContext: {} } as unknown as StepContext
}

function makeStep(model_profile?: string): ReasonStep {
  return { id: "evaluate", type: "reason", model_profile } as unknown as ReasonStep
}

describe("resolveModelProfile (R8d)", () => {
  it("returns a static profile as-is", () => {
    expect(resolveModelProfile(makeStep("evaluation"), makeCtx({}))).toBe("evaluation")
  })

  it("resolves a $.pipeline_state reference to a string", () => {
    const ctx = makeCtx({ review_config: { reviewer_model_profile: "powerful" } })
    const step = makeStep("$.pipeline_state.review_config.reviewer_model_profile")
    expect(resolveModelProfile(step, ctx)).toBe("powerful")
  })

  it("returns undefined when the reference does not resolve to a string", () => {
    const ctx = makeCtx({ review_config: {} })
    const step = makeStep("$.pipeline_state.review_config.missing")
    expect(resolveModelProfile(step, ctx)).toBeUndefined()
  })

  it("returns undefined when model_profile is absent", () => {
    expect(resolveModelProfile(makeStep(undefined), makeCtx({}))).toBeUndefined()
  })
})
