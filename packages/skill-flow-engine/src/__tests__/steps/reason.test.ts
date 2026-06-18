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
    redis:                {} as any,
    clearJobId:           async () => {},
    maskedScope:          {},
    transactionOnFailure: null,
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

// ── T7b-2a — json_schema (tool-use): pula a validação estática local ──────────

describe("executeReason — json_schema (T7b)", () => {
  const jsonSchema = {
    type: "object",
    required: ["criterion_responses"],
    properties: {
      criterion_responses: { type: "array", items: { type: "object" } },
    },
  }

  it("inline json_schema: repassa ao gateway e aceita o resultado sem validação estática", async () => {
    const aiGatewayCall = vi.fn().mockResolvedValue({ criterion_responses: [{ criterion_id: "c1" }] })
    const ctx = { ...makeCtx({}), aiGatewayCall }
    const s = { ...step, json_schema: jsonSchema } as ReasonStep
    const result = await executeReason(s, ctx)
    expect(result.next_step_id).toBe("proximo")             // on_success mesmo sem intencao/confianca
    expect(aiGatewayCall).toHaveBeenCalledTimes(1)          // sem retry de validação
    expect(aiGatewayCall.mock.calls[0][0]).toMatchObject({ json_schema: jsonSchema })
  })

  it("json_schema_ref: resolve do pipeline_state e repassa ao gateway", async () => {
    const aiGatewayCall = vi.fn().mockResolvedValue({ criterion_responses: [] })
    const ctx = makeCtx({})
    ctx.state.results = { eval_context: { evaluation_output_schema: jsonSchema } }
    const ctx2 = { ...ctx, aiGatewayCall }
    const s = { ...step, json_schema_ref: "$.pipeline_state.eval_context.evaluation_output_schema" } as ReasonStep
    const result = await executeReason(s, ctx2)
    expect(result.next_step_id).toBe("proximo")
    expect(aiGatewayCall.mock.calls[0][0]).toMatchObject({ json_schema: jsonSchema })
  })

  it("json_schema_ref ausente no contexto: cai no caminho flat (output_schema)", async () => {
    // Sem o schema no pipeline_state → resolveJsonSchema retorna undefined → valida flat.
    const aiGatewayCall = vi.fn().mockResolvedValue({ intencao: "portabilidade", confianca: 0.9 })
    const ctx = { ...makeCtx({}), aiGatewayCall }
    const s = { ...step, json_schema_ref: "$.pipeline_state.eval_context.nao_existe" } as ReasonStep
    const result = await executeReason(s, ctx)
    expect(result.next_step_id).toBe("proximo")
    expect(aiGatewayCall.mock.calls[0][0].json_schema).toBeUndefined()
  })
})
