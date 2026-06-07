/**
 * steps/complete.test.ts
 * F1.2 (bancada de agentes): outcome dinâmico via `outcome_from` + fallback literal.
 */

import { describe, it, expect } from "vitest"
import { executeComplete }       from "../../steps/complete"
import type { StepContext }      from "../../executor"
import type { CompleteStep, PipelineState } from "@plughub/schemas"

function makeCtx(results: Record<string, unknown>): StepContext {
  return {
    sessionId:      "s1",
    customerId:     "c1",
    sessionContext: {},
    state: {
      results, retry_counters: {}, transitions: [], status: "in_progress",
      flow_id: "test", current_step_id: "fin",
      started_at: new Date().toISOString(), updated_at: new Date().toISOString(),
    } as PipelineState,
    tenantId:        "tenant1",
    mcpCall:         async () => ({}),
    aiGatewayCall:   async () => ({}),
    saveState:       async () => {},
    retryStep:       async () => ({ next_step_id: "", transition_reason: "on_success" as const }),
    executeFallback: async () => ({ next_step_id: "", transition_reason: "on_success" as const }),
    getJobId:        async () => null,
    setJobId:        async () => {},
    redis:                {} as any,
    clearJobId:           async () => {},
    maskedScope:          {},
    transactionOnFailure: null,
  }
}

describe("executeComplete", () => {
  it("usa o outcome literal quando não há outcome_from", () => {
    const step: CompleteStep = { id: "fin", type: "complete", outcome: "resolved" }
    const result = executeComplete(step, makeCtx({}))
    expect(result.next_step_id).toBe("__complete__")
    expect(result.outcome).toBe("resolved")
  })

  it("resolve o outcome dinâmico de pipeline_state via outcome_from", () => {
    const step: CompleteStep = {
      id: "fin", type: "complete", outcome: "resolved",
      outcome_from: "wrapup_classificacao",
    }
    const ctx = makeCtx({ wrapup_classificacao: "escalated" })
    expect(executeComplete(step, ctx).outcome).toBe("escalated")
  })

  it.each(["resolved", "escalated", "abandoned", "suspended"])(
    "aceita valor dinâmico normalizado '%s'",
    (value) => {
      const step: CompleteStep = {
        id: "fin", type: "complete", outcome: "failed",
        outcome_from: "wrapup_classificacao",
      }
      const ctx = makeCtx({ wrapup_classificacao: value })
      expect(executeComplete(step, ctx).outcome).toBe(value)
    }
  )

  it("cai no literal (fallback) quando a chave está ausente", () => {
    const step: CompleteStep = {
      id: "fin", type: "complete", outcome: "resolved",
      outcome_from: "wrapup_classificacao",
    }
    expect(executeComplete(step, makeCtx({})).outcome).toBe("resolved")
  })

  it("cai no literal (fallback) quando o valor dinâmico é inválido", () => {
    const step: CompleteStep = {
      id: "fin", type: "complete", outcome: "resolved",
      outcome_from: "wrapup_classificacao",
    }
    const ctx = makeCtx({ wrapup_classificacao: "nao_existe" })
    expect(executeComplete(step, ctx).outcome).toBe("resolved")
  })
})
