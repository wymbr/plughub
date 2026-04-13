/**
 * steps/catch.test.ts
 * Testes da lógica de retry e fallback do step catch.
 */

import { describe, it, expect, vi } from "vitest"
import { executeCatch }             from "../../steps/catch"
import type { StepContext }         from "../../executor"
import type { CatchStep, PipelineState } from "@plughub/schemas"

function makeCtx(overrides: Partial<StepContext> = {}): StepContext {
  return {
    sessionId:      "s1",
    customerId:     "c1",
    sessionContext: {},
    state: {
      results:         { step_a: { error: "falhou" } },
      retry_counters:  {},
      transitions:     [],
      status:          "in_progress",
      flow_id:         "test",
      current_step_id: "catch_step",
      started_at:      new Date().toISOString(),
      updated_at:      new Date().toISOString(),
    } as PipelineState,
    tenantId:        "tenant1",
    mcpCall:         async () => ({}),
    aiGatewayCall:   async () => ({}),
    saveState:       vi.fn().mockResolvedValue(undefined),
    retryStep:       vi.fn().mockResolvedValue({ next_step_id: "proximo", transition_reason: "on_success" as const }),
    executeFallback: vi.fn().mockResolvedValue({ next_step_id: "fallback_ok", transition_reason: "on_success" as const }),
    getJobId:        vi.fn().mockResolvedValue(null),
    setJobId:        vi.fn().mockResolvedValue(undefined),
    clearJobId:      vi.fn().mockResolvedValue(undefined),
    redis:           {} as any,
    ...overrides,
  }
}

const catchWithRetry: CatchStep = {
  id:            "tratar_erro",
  type:          "catch",
  error_context: "step_a",
  strategies: [
    { type: "retry", max_attempts: 2, delay_ms: 0, on_exhausted: "" },
  ],
  on_failure: "escalar",
}

describe("executeCatch", () => {
  it("retry com sucesso na primeira tentativa retorna on_success", async () => {
    const ctx = makeCtx()
    const result = await executeCatch(catchWithRetry, ctx)
    expect(result.transition_reason).toBe("on_success")
    expect(ctx.retryStep).toHaveBeenCalledWith("step_a")
  })

  it("esgota retry e vai para on_failure quando todas tentativas falham", async () => {
    const retryStep = vi.fn().mockResolvedValue({
      next_step_id:      "falhou",
      transition_reason: "on_failure" as const,
    })
    const ctx = makeCtx({ retryStep })
    const result = await executeCatch(catchWithRetry, ctx)
    expect(result.next_step_id).toBe("escalar")
  })

  it("incrementa retry_count a cada tentativa", async () => {
    const saveState = vi.fn().mockResolvedValue(undefined)
    const ctx = makeCtx({ saveState })
    await executeCatch(catchWithRetry, ctx)
    expect(saveState).toHaveBeenCalled()
  })

  it("fallback bem-sucedido retorna on_success", async () => {
    const catchWithFallback: CatchStep = {
      id:            "tratar_erro",
      type:          "catch",
      error_context: "step_a",
      strategies: [
        { type: "fallback", id: "fb", target: { skill_id: "skill_alt_v1" }, on_success: "proximo", on_failure: "escalar" },
      ],
      on_failure: "escalar",
    }
    const ctx = makeCtx()
    const result = await executeCatch(catchWithFallback, ctx)
    expect(result.next_step_id).toBe("proximo")
  })
})
