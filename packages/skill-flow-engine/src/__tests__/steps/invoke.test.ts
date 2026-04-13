/**
 * steps/invoke.test.ts
 * Testes do executor do step invoke — incluindo os três cenários de idempotência.
 *
 * Cenários de idempotência:
 *   A. Primeira execução normal (sem sentinel)
 *   B. Retomada após crash pós-MCP mas pré-saveState (sentinel = "dispatched")
 *      → re-executa a chamada MCP (at-least-once para a janela residual)
 *   C. Retomada após crash pós-saveState do resultado (sentinel = "completed")
 *      → retorna resultado salvo sem re-chamar MCP (caso principal)
 */

import { describe, it, expect, vi } from "vitest"
import { executeInvoke }            from "../../steps/invoke"
import type { StepContext }         from "../../executor"
import type { InvokeStep, PipelineState } from "@plughub/schemas"

// ─────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────

function makeState(results: Record<string, unknown> = {}): PipelineState {
  return {
    flow_id:         "test_flow",
    current_step_id: "consultar",
    status:          "in_progress",
    started_at:      new Date().toISOString(),
    updated_at:      new Date().toISOString(),
    results,
    retry_counters:  {},
    transitions:     [],
  }
}

function makeCtx(overrides: Partial<StepContext> = {}): StepContext {
  return {
    tenantId:       "tenant-test",
    sessionId:      "session-001",
    customerId:     "customer-001",
    sessionContext: {},
    state:          makeState(),
    redis:          {} as never,
    mcpCall:        vi.fn(),
    aiGatewayCall:  vi.fn(),
    saveState:      vi.fn().mockImplementation(async function(this: void, s: PipelineState) {
      // saveState simula a actualização de ctx.state (como o engine faz)
      // Os testes que precisam disto fazem override manual
    }),
    retryStep:      vi.fn(),
    executeFallback: vi.fn(),
    getJobId:       vi.fn().mockResolvedValue(null),
    setJobId:       vi.fn().mockResolvedValue(undefined),
    clearJobId:     vi.fn().mockResolvedValue(undefined),
    renewLock:      vi.fn().mockResolvedValue(true),
    ...overrides,
  }
}

const step: InvokeStep = {
  id:         "consultar",
  type:       "invoke",
  target:     { mcp_server: "mcp-server-crm", tool: "customer_get" },
  input:      { customer_id: "c1" },
  output_as:  "cliente",
  on_success: "concluir",
  on_failure: "escalar",
}

// ─────────────────────────────────────────────
// Execução normal
// ─────────────────────────────────────────────

describe("executeInvoke — execução normal (sem sentinel)", () => {
  it("chama MCP, salva sentinel completed + resultado e retorna on_success", async () => {
    const mcpResult = { customer_id: "c1", tier: "gold" }
    const mcpCall   = vi.fn().mockResolvedValue(mcpResult)

    const savedStates: PipelineState[] = []
    const ctx = makeCtx({
      mcpCall,
      saveState: vi.fn().mockImplementation(async (s: PipelineState) => {
        savedStates.push(s)
        ctx.state = s
      }),
    })

    const result = await executeInvoke(step, ctx)

    // Dois saveState: fase 1 (dispatched) e fase 2 (completed + resultado)
    expect(savedStates).toHaveLength(2)
    expect(savedStates[0]!.results["consultar:__invoked__"]).toBe("dispatched")
    expect(savedStates[1]!.results["consultar:__invoked__"]).toBe("completed")
    expect(savedStates[1]!.results["cliente"]).toEqual(mcpResult)

    // Retorno correto
    expect(result.next_step_id).toBe("concluir")
    expect(result.transition_reason).toBe("on_success")
    expect(result.output_value).toEqual(mcpResult)

    // MCP chamado exatamente uma vez
    expect(mcpCall).toHaveBeenCalledTimes(1)
    expect(mcpCall).toHaveBeenCalledWith("customer_get", { customer_id: "c1" }, "mcp-server-crm")
  })

  it("retorna on_failure e mantém sentinel 'dispatched' quando MCP lança erro", async () => {
    const mcpCall = vi.fn().mockRejectedValue(new Error("CRM indisponível"))
    const savedStates: PipelineState[] = []
    const ctx = makeCtx({
      mcpCall,
      saveState: vi.fn().mockImplementation(async (s: PipelineState) => {
        savedStates.push(s)
        ctx.state = s
      }),
    })

    const result = await executeInvoke(step, ctx)

    // Apenas um saveState: fase 1 (dispatched) — fase 2 não ocorre em caso de erro
    expect(savedStates).toHaveLength(1)
    expect(savedStates[0]!.results["consultar:__invoked__"]).toBe("dispatched")

    expect(result.next_step_id).toBe("escalar")
    expect(result.transition_reason).toBe("on_failure")
    expect((result.output_value as Record<string, string>).error).toBe("CRM indisponível")
  })
})

// ─────────────────────────────────────────────
// Cenário B: crash pós-MCP, pré-save do resultado
// (sentinel = "dispatched", sem resultado)
// ─────────────────────────────────────────────

describe("executeInvoke — cenário B: retomada com sentinel 'dispatched'", () => {
  it("re-executa a chamada MCP (at-least-once para janela residual)", async () => {
    const mcpResult = { customer_id: "c1", tier: "gold" }
    const mcpCall   = vi.fn().mockResolvedValue(mcpResult)

    // Estado da retomada: sentinel "dispatched" mas sem resultado
    const ctx = makeCtx({
      state:   makeState({ "consultar:__invoked__": "dispatched" }),
      mcpCall,
      saveState: vi.fn().mockImplementation(async (s: PipelineState) => {
        ctx.state = s
      }),
    })

    const result = await executeInvoke(step, ctx)

    // MCP re-executado
    expect(mcpCall).toHaveBeenCalledTimes(1)
    expect(result.next_step_id).toBe("concluir")
    expect(result.output_value).toEqual(mcpResult)
  })
})

// ─────────────────────────────────────────────
// Cenário C: retomada com sentinel "completed" (caso principal)
// ─────────────────────────────────────────────

describe("executeInvoke — cenário C: retomada com sentinel 'completed'", () => {
  it("retorna resultado salvo sem re-chamar MCP", async () => {
    const storedResult = { customer_id: "c1", tier: "gold" }
    const mcpCall      = vi.fn()

    // Estado da retomada: sentinel "completed" + resultado presente
    const ctx = makeCtx({
      state: makeState({
        "consultar:__invoked__": "completed",
        "cliente":               storedResult,
      }),
      mcpCall,
    })

    const result = await executeInvoke(step, ctx)

    // MCP NÃO chamado
    expect(mcpCall).not.toHaveBeenCalled()

    // Resultado salvo retornado
    expect(result.next_step_id).toBe("concluir")
    expect(result.transition_reason).toBe("on_success")
    expect(result.output_value).toEqual(storedResult)
  })

  it("retorna on_success mesmo quando output_as é undefined (step sem resultado)", async () => {
    const stepNoOutput: InvokeStep = {
      id:         "notificar_crm",
      type:       "invoke",
      target:     { mcp_server: "mcp-server-crm", tool: "event_register" },
      input:      { event: "portabilidade_iniciada" },
      // output_as ausente
      on_success: "concluir",
      on_failure: "escalar",
    }

    const ctx = makeCtx({
      state:   makeState({ "notificar_crm:__invoked__": "completed" }),
      mcpCall: vi.fn(),
    })

    const result = await executeInvoke(stepNoOutput, ctx)

    expect(result.next_step_id).toBe("concluir")
    expect(result.transition_reason).toBe("on_success")
    expect(result.output_as).toBeUndefined()
  })
})

// ─────────────────────────────────────────────
// JSONPath resolution
// ─────────────────────────────────────────────

describe("executeInvoke — resolução de inputs com JSONPath", () => {
  it("resolve referência JSONPath de pipeline_state", async () => {
    const mcpCall = vi.fn().mockResolvedValue({ ok: true })
    const ctx = makeCtx({
      state:   makeState({ classificacao: { customer_id: "c-resolved" } }),
      mcpCall,
      saveState: vi.fn().mockImplementation(async (s: PipelineState) => { ctx.state = s }),
    })

    const stepWithRef: InvokeStep = {
      id:         "buscar",
      type:       "invoke",
      target:     { mcp_server: "mcp-server-crm", tool: "customer_get" },
      input:      { customer_id: "$.pipeline_state.classificacao.customer_id" },
      on_success: "ok",
      on_failure: "fail",
    }

    await executeInvoke(stepWithRef, ctx)

    expect(mcpCall).toHaveBeenCalledWith(
      "customer_get",
      { customer_id: "c-resolved" },
      "mcp-server-crm",
    )
  })
})
