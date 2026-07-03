/**
 * delegate.slice3.test.ts — Identity Resolver (nível b) Slice 3.
 *
 * The executeDelegate call site builds an EXPLICIT persistDelegate param object,
 * so new step fields are dropped unless forwarded there. This locks the
 * propagation of customer_resumable / resume_policy from the step to the
 * persistDelegate callback.
 */
import { describe, it, expect, vi } from "vitest"
import { executeDelegate } from "./delegate"
import type { DelegateStep } from "@plughub/schemas"
import type { StepContext } from "../executor"

function makeStep(overrides: Partial<DelegateStep> = {}): DelegateStep {
  return {
    type:          "delegate",
    id:            "confirmar",
    pool:          "loja_checkout_io",
    timeout_hours: 24,
    on_resume:     { next: "revisar" },
    on_timeout:    { next: "expirou" },
    ...overrides,
  } as unknown as DelegateStep
}

function makeCtx(persistDelegate: StepContext["persistDelegate"]): StepContext {
  return {
    sessionId:      "sess_parent",
    sessionContext: {},
    state:          { results: {} },
    saveState:      vi.fn(async () => {}),
    persistDelegate,
  } as unknown as StepContext
}

type DelegateParams = Parameters<NonNullable<StepContext["persistDelegate"]>>[0]

describe("executeDelegate — Slice 3 field propagation", () => {
  it("forwards customer_resumable + resume_policy to persistDelegate", async () => {
    let captured: DelegateParams | undefined
    const persistDelegate = vi.fn(async (p: DelegateParams) => {
      captured = p
      return { child_session_id: "sess_child" }
    })
    const ctx = makeCtx(persistDelegate)

    const result = await executeDelegate(
      makeStep({ customer_resumable: true, resume_policy: "auto" }),
      ctx,
    )

    expect(result.next_step_id).toBe("__suspended__")
    expect(persistDelegate).toHaveBeenCalledTimes(1)
    expect(captured).toMatchObject({
      pool:               "loja_checkout_io",
      customer_resumable: true,
      resume_policy:      "auto",
    })
  })

  it("forwards the false/offer default when not resumable", async () => {
    let captured: DelegateParams | undefined
    const persistDelegate = vi.fn(async (p: DelegateParams) => {
      captured = p
      return { child_session_id: "sess_child" }
    })
    const ctx = makeCtx(persistDelegate)

    await executeDelegate(
      makeStep({ customer_resumable: false, resume_policy: "offer" }),
      ctx,
    )

    expect(captured).toMatchObject({
      customer_resumable: false,
      resume_policy:      "offer",
    })
  })
})
