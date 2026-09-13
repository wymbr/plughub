/**
 * resume-requirement.test.ts — PID-06. O step que cria pendência de cliente leva a
 * exigência de retomada RESOLVIDA até o gateway, e FALHA FECHADO quando não resolve.
 *
 * Os dois `persist*` montam o objeto de parâmetros à mão (o slice3 existe porque campo
 * não repassado ali some sem erro); e o ref `$.config.*` só tem valor em runtime.
 */
import { describe, it, expect, vi } from "vitest"
import { executeDelegate } from "./delegate"
import { executeCollect } from "./collect"
import type { CollectStep, DelegateStep } from "@plughub/schemas"
import type { StepContext } from "../executor"

type DelegateParams = Parameters<NonNullable<StepContext["persistDelegate"]>>[0]
type CollectParams  = Parameters<NonNullable<StepContext["persistCollect"]>>[0]

const delegate = (extra: Record<string, unknown>) => ({
  type: "delegate", id: "aprovar", pool: "aprovacao", timeout_hours: 24, customer_resumable: true,
  on_resume: { next: "ok" }, on_timeout: { next: "expirou" }, ...extra,
}) as unknown as DelegateStep

const collect = (extra: Record<string, unknown>) => ({
  type: "collect", id: "parquear", target: { type: "customer", id: "cus_1" }, interaction: "text",
  prompt: "p", timeout_hours: 24, business_hours: false, customer_resumable: true, resume_policy: "offer",
  output_as: "entrega", on_response: { next: "ok" }, on_timeout: { next: "expirou" }, ...extra,
}) as unknown as CollectStep

function ctx(config: Record<string, unknown>, extra: Partial<StepContext>): StepContext {
  return {
    sessionId: "sess", sessionContext: {}, tenantId: "t",
    state: { results: {} }, config,
    saveState: vi.fn(async () => {}),
    contextStore: { get: async () => null, getValue: async () => undefined },
    ...extra,
  } as unknown as StepContext
}

describe("PID-06 — delegate", () => {
  it("resolve `$.config.resume_requires` e repassa a lista ao persistDelegate", async () => {
    let captured: DelegateParams | undefined
    const persistDelegate = vi.fn(async (p: DelegateParams) => { captured = p; return { child_session_id: "c" } })
    const r = await executeDelegate(delegate({ resume_requires: "$.config.resume_requires" }),
      ctx({ resume_requires: ["otp"] }, { persistDelegate }))
    expect(r.next_step_id).toBe("__suspended__")
    expect(captured?.resume_requires).toEqual(["otp"])
  })

  it("sem o campo, nada é repassado (fluxo antigo intacto)", async () => {
    let captured: DelegateParams | undefined
    const persistDelegate = vi.fn(async (p: DelegateParams) => { captured = p; return { child_session_id: "c" } })
    await executeDelegate(delegate({}), ctx({}, { persistDelegate }))
    expect(captured && "resume_requires" in captured).toBe(false)
  })

  it("ref que não resolve para lista FALHA FECHADO: nenhuma pendência nasce", async () => {
    const persistDelegate = vi.fn(async () => ({ child_session_id: "c" }))
    const persistSuspendWebhook = vi.fn(async () => ({ resume_expires_at: "x" }))
    const r = await executeDelegate(delegate({ resume_requires: "$.config.nao_existe" }),
      ctx({}, { persistDelegate, persistSuspendWebhook } as Partial<StepContext>))
    expect(r.next_step_id).toBe("expirou")
    expect(r.output_value).toEqual({ error: "resume_requires_unresolved" })
    expect(persistDelegate).not.toHaveBeenCalled()
    expect(persistSuspendWebhook).not.toHaveBeenCalled()
  })
})

describe("PID-06 — collect", () => {
  it("repassa a lista literal ao persistCollect", async () => {
    let captured: CollectParams | undefined
    const persistCollect = vi.fn(async (p: CollectParams) => { captured = p; return { send_at: "a", expires_at: "b" } })
    await executeCollect(collect({ resume_requires: ["otp"] }), ctx({}, { persistCollect }))
    expect(captured?.resume_requires).toEqual(["otp"])
  })

  it("valor inválido FALHA FECHADO antes do token", async () => {
    const persistCollect = vi.fn(async () => ({ send_at: "a", expires_at: "b" }))
    const saveState = vi.fn(async () => {})
    const r = await executeCollect(collect({ resume_requires: "$.config.nao_existe" }),
      ctx({}, { persistCollect, saveState } as Partial<StepContext>))
    expect(r.next_step_id).toBe("expirou")
    expect(persistCollect).not.toHaveBeenCalled()
    expect(saveState).not.toHaveBeenCalled()
  })
})
