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

function makeCtx(
  persistDelegate: StepContext["persistDelegate"],
  tags: Record<string, unknown> = {},
): StepContext {
  return {
    sessionId:      "sess_parent",
    sessionContext: {},
    state:          { results: {} },
    saveState:      vi.fn(async () => {}),
    persistDelegate,
    // ContextStore mínimo — só o suficiente para resolver refs @ctx.*
    contextStore: {
      get:      async (_s: string, tag: string) =>
        tag in tags ? { value: tags[tag], confidence: 1 } : null,
      getValue: async (_s: string, tag: string) => tags[tag],
    },
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

// ── I5 — prazo do ACW como fato do POOL DE ORIGEM ──────────────────────────────
// O `timeout_hours` aceita ref porque o prazo pode ser do CHAMADOR, não do skill:
// o wrap-up é genérico e o prazo é do pool que disparou o hook.
describe("executeDelegate — timeout_hours por referência", () => {
  it("resolve o prazo de @ctx.hook.* e o repassa ao persistDelegate", async () => {
    let captured: DelegateParams | undefined
    const persistDelegate = vi.fn(async (p: DelegateParams) => {
      captured = p
      return { child_session_id: "sess_child" }
    })
    const ctx = makeCtx(persistDelegate, { "hook.acw_timeout_hours": "2" })

    await executeDelegate(
      makeStep({ timeout_hours: "@ctx.hook.acw_timeout_hours" } as Partial<DelegateStep>),
      ctx,
    )

    expect(captured?.timeout_hours).toBe(2)
  })

  it("ref ausente cai no default de 24h e NÃO falha o delegate", async () => {
    // Diferente do `pool` (falha dura): prazo tem default seguro, alvo não tem.
    // O que não pode é degradar em silêncio — daí o console.warn assertado.
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {})
    let captured: DelegateParams | undefined
    const persistDelegate = vi.fn(async (p: DelegateParams) => {
      captured = p
      return { child_session_id: "sess_child" }
    })
    const ctx = makeCtx(persistDelegate)   // sem a tag

    const result = await executeDelegate(
      makeStep({ timeout_hours: "@ctx.hook.acw_timeout_hours" } as Partial<DelegateStep>),
      ctx,
    )

    expect(result.next_step_id).toBe("__suspended__")   // delegate seguiu
    expect(captured?.timeout_hours).toBe(24)
    expect(warn).toHaveBeenCalled()                     // degradação nomeada
    warn.mockRestore()
  })

  it("valor numérico literal passa direto (retrocompat)", async () => {
    let captured: DelegateParams | undefined
    const persistDelegate = vi.fn(async (p: DelegateParams) => {
      captured = p
      return { child_session_id: "sess_child" }
    })
    await executeDelegate(makeStep({ timeout_hours: 6 }), makeCtx(persistDelegate))
    expect(captured?.timeout_hours).toBe(6)
  })
})
