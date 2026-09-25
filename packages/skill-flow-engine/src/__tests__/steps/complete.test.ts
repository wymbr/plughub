/**
 * steps/complete.test.ts
 * F1.2 (bancada de agentes): outcome dinâmico via `outcome_from` + fallback literal.
 */

import { describe, it, expect, vi, afterEach } from "vitest"
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

// SFE-01: o fallback para o literal nunca é mudo — e o caminho válido não loga
// (testemunha: sem ela, um warn incondicional passaria nos ramos de fallback).
describe("executeComplete — fallback barulhento (SFE-01)", () => {
  afterEach(() => vi.restoreAllMocks())

  const step: CompleteStep = {
    id: "fin", type: "complete", outcome: "resolved",
    outcome_from: "wrapup_classificacao",
  }

  it("loga valor inválido, nomeando chave, valor, literal e sessão", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {})
    executeComplete(step, makeCtx({ wrapup_classificacao: "escalated_humano" }))
    expect(warn).toHaveBeenCalledTimes(1)
    const msg = String(warn.mock.calls[0][0])
    expect(msg).toContain("wrapup_classificacao")
    expect(msg).toContain("escalated_humano")
    expect(msg).toContain('"resolved"')
    expect(msg).toContain("s1")
  })

  it("loga chave ausente (output_as com outro nome)", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {})
    executeComplete(step, makeCtx({ outra_chave: "escalated" }))
    expect(warn).toHaveBeenCalledTimes(1)
    expect(String(warn.mock.calls[0][0])).toContain("ausente")
  })

  it("loga valor não-string", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {})
    executeComplete(step, makeCtx({ wrapup_classificacao: { outcome: "escalated" } }))
    expect(warn).toHaveBeenCalledTimes(1)
  })

  it("NÃO loga quando o valor dinâmico é válido", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {})
    expect(executeComplete(step, makeCtx({ wrapup_classificacao: "escalated" })).outcome).toBe("escalated")
    expect(warn).not.toHaveBeenCalled()
  })

  it("NÃO loga quando não há outcome_from", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {})
    executeComplete({ id: "fin", type: "complete", outcome: "resolved" }, makeCtx({}))
    expect(warn).not.toHaveBeenCalled()
  })
})
