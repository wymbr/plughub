/**
 * steps/reason.test.ts
 * Testes da validação de output_schema no step reason.
 */

import { describe, it, expect, vi } from "vitest"
import { executeReason }            from "../../steps/reason"
import type { StepContext }         from "../../executor"
import type { ReasonStep, PipelineState } from "@plughub/schemas"

function makeCtx(aiResult: unknown): StepContext {
  return {
    sessionId:      "s1",
    customerId:     "c1",
    sessionContext: {},
    state: {
      results: {}, retry_counters: {}, transitions: [], status: "in_progress",
      flow_id: "test", current_step_id: "s",
      started_at: new Date().toISOString(), updated_at: new Date().toISOString(),
    } as PipelineState,
    tenantId:        "tenant1",
    mcpCall:         async () => ({}),
    aiGatewayCall:   vi.fn().mockResolvedValue(aiResult),
    saveState:       async () => {},
    retryStep:       async () => ({ next_step_id: "", transition_reason: "on_success" as const }),
    executeFallback: async () => ({ next_step_id: "", transition_reason: "on_success" as const }),
    getJobId:        async () => null,
    setJobId:        async () => {},
    redis:           {} as any,
    clearJobId:      async () => {},
  }
}

const step: ReasonStep = {
  id:        "classificar",
  type:      "reason",
  prompt_id: "prompt_v1",
  output_schema: {
    intencao:  { type: "string", enum: ["portabilidade", "cancelamento", "suporte"] },
    confianca: { type: "number", minimum: 0, maximum: 1 },
  },
  output_as:          "classificacao",
  max_format_retries: 1,
  on_success:         "proximo",
  on_failure:         "escalar",
}

describe("executeReason — validação de output_schema", () => {
  it("aceita retorno válido e retorna on_success", async () => {
    const ctx = makeCtx({ intencao: "portabilidade", confianca: 0.92 })
    const result = await executeReason(step, ctx)
    expect(result.next_step_id).toBe("proximo")
    expect(result.output_value).toEqual({ intencao: "portabilidade", confianca: 0.92 })
  })

  it("rejeita enum inválido e retorna on_failure após retries", async () => {
    const ctx = makeCtx({ intencao: "INVALIDO", confianca: 0.80 })
    const result = await executeReason(step, ctx)
    expect(result.next_step_id).toBe("escalar")
  })

  it("rejeita número fora do range e retorna on_failure", async () => {
    const ctx = makeCtx({ intencao: "portabilidade", confianca: 1.5 })
    const result = await executeReason(step, ctx)
    expect(result.next_step_id).toBe("escalar")
  })

  it("rejeita campo obrigatório ausente", async () => {
    const ctx = makeCtx({ intencao: "portabilidade" })  // falta confianca
    const result = await executeReason(step, ctx)
    expect(result.next_step_id).toBe("escalar")
  })

  it("tenta max_format_retries vezes antes de falhar", async () => {
    const aiGatewayCall = vi.fn().mockResolvedValue({ intencao: "INVALIDO", confianca: 0.5 })
    const ctx = { ...makeCtx({}), aiGatewayCall }
    await executeReason(step, ctx)
    // max_format_retries: 1 → 2 chamadas (tentativa 0 + retry 1)
    expect(aiGatewayCall).toHaveBeenCalledTimes(2)
  })
})
