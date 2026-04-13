/**
 * steps/choice.test.ts
 * Testes da lógica de avaliação de condições JSONPath.
 */

import { describe, it, expect } from "vitest"
import { executeChoice }        from "../../steps/choice"
import type { StepContext }     from "../../executor"
import type { ChoiceStep, PipelineState } from "@plughub/schemas"

function makeCtx(results: Record<string, unknown>): StepContext {
  return {
    sessionId:      "s1",
    customerId:     "c1",
    sessionContext: {},
    state: {
      results, retry_counters: {}, transitions: [], status: "in_progress",
      flow_id: "test", current_step_id: "s",
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
    redis:           {} as any,
    clearJobId:      async () => {},
  }
}

const step: ChoiceStep = {
  id:   "rotear",
  type: "choice",
  conditions: [
    { field: "$.pipeline_state.classificacao.intencao", operator: "eq",  value: "portabilidade", next: "step_port" },
    { field: "$.pipeline_state.classificacao.confianca", operator: "lt",  value: 0.60,            next: "step_escalar" },
    { field: "$.pipeline_state.classificacao.confianca", operator: "gte", value: 0.85,            next: "step_autonomo" },
  ],
  default: "step_hibrido",
}

describe("executeChoice", () => {
  it("retorna o next da primeira condição satisfeita", () => {
    const ctx = makeCtx({ classificacao: { intencao: "portabilidade", confianca: 0.90 } })
    const result = executeChoice(step, ctx)
    expect(result.next_step_id).toBe("step_port")
  })

  it("avalia segunda condição quando primeira falha", () => {
    const ctx = makeCtx({ classificacao: { intencao: "cancelamento", confianca: 0.45 } })
    const result = executeChoice(step, ctx)
    expect(result.next_step_id).toBe("step_escalar")
  })

  it("retorna default quando nenhuma condição satisfeita", () => {
    const ctx = makeCtx({ classificacao: { intencao: "suporte", confianca: 0.72 } })
    const result = executeChoice(step, ctx)
    expect(result.next_step_id).toBe("step_hibrido")
  })

  it("operador contains funciona com string", () => {
    const stepContains: ChoiceStep = {
      id: "c", type: "choice",
      conditions: [{ field: "$.pipeline_state.msg", operator: "contains", value: "portabilidade", next: "match" }],
      default: "nomatch",
    }
    const ctx = makeCtx({ msg: "quero fazer portabilidade" })
    expect(executeChoice(stepContains, ctx).next_step_id).toBe("match")
  })

  it("retorna default quando campo JSONPath não existe", () => {
    const ctx = makeCtx({})
    const result = executeChoice(step, ctx)
    expect(result.next_step_id).toBe("step_hibrido")
  })
})
