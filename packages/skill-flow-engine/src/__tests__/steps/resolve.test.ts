/**
 * steps/resolve.test.ts
 * Unit tests for executeResolve() — Arc 5 / Context-Aware Fase 3.
 *
 * Coverage:
 *   1.  No contextStore → on_success immediately with method=no_contextstore
 *   2.  All fields complete in cache → on_success with method=cache (0 LLM calls)
 *   3.  CRM lookup fills gaps → on_success with method=crm (0 LLM calls)
 *   4.  CRM lookup error is non-fatal → falls through to question generation
 *   5.  LLM question generation failure → on_success with method=skipped (non-blocking)
 *   6.  notification_send failure → on_failure (catastrophic)
 *   7.  BLPOP timeout → on_success with method=timeout
 *   8.  Client disconnect during wait → on_success with method=disconnected
 *   9.  @mention trigger_step interrupt → jumps to declared step
 *  10.  @mention terminate_self interrupt → on_failure
 *  11.  Successful customer input → LLM extracts fields, writes to contextStore → on_success method=customer_input
 *  12.  LLM extraction failure is non-fatal → on_success method=customer_input
 *  13.  Lock stolen before BLPOP → on_failure
 *  14.  output_as produces output_value in StepResult
 *  15.  waitingKey always deleted in finally (even on timeout)
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { executeResolve }                        from "../../steps/resolve"
import type { StepContext }                      from "../../executor"
import type { ResolveStep, PipelineState }       from "@plughub/schemas"

// ─────────────────────────────────────────────
// Helpers
// ─────────────────────────────────────────────

function makeState(): PipelineState {
  return {
    flow_id:         "test_flow",
    current_step_id: "resolve_ctx",
    status:          "in_progress",
    started_at:      new Date().toISOString(),
    updated_at:      new Date().toISOString(),
    results:         {},
    retry_counters:  {},
    transitions:     [],
  }
}

function makeStep(overrides: Partial<ResolveStep> = {}): ResolveStep {
  return {
    id:   "resolve_ctx",
    type: "resolve",
    required_fields: [
      { tag: "caller.nome",          confidence_min: 0.7, required: true },
      { tag: "caller.motivo_contato", confidence_min: 0.6, required: true },
    ],
    question_prompt_id: "resolve_generate_question_v1",
    extract_prompt_id:  "resolve_extract_fields_v1",
    timeout_s:          5,
    on_success:         "proximo",
    on_failure:         "falha",
    ...overrides,
  }
}

/** Builds a fake IContextStore with configurable getMissing response and set spy */
function makeContextStore(opts: {
  complete?:        boolean
  missing?:         string[]
  low_confidence?:  Array<{ tag: string; confidence: number; required: boolean }>
  /** After CRM call, return a different gaps report */
  completeAfterCrm?: boolean
} = {}) {
  const gapsFirst = {
    complete:       opts.complete       ?? false,
    missing:        opts.missing        ?? ["caller.nome", "caller.motivo_contato"],
    low_confidence: opts.low_confidence ?? [],
  }
  const gapsSecond = opts.completeAfterCrm
    ? { complete: true, missing: [], low_confidence: [] }
    : gapsFirst

  let callCount = 0
  return {
    getMissing: vi.fn(async () => {
      callCount++
      return callCount === 1 ? gapsFirst : gapsSecond
    }),
    set:       vi.fn().mockResolvedValue(undefined),
    get:       vi.fn().mockResolvedValue(null),
    getValue:  vi.fn().mockResolvedValue(null),
    getAll:    vi.fn().mockResolvedValue({}),
    getByPrefix: vi.fn().mockResolvedValue({}),
    delete:    vi.fn().mockResolvedValue(undefined),
    clearSession: vi.fn().mockResolvedValue(undefined),
  }
}

function makeCtx(overrides: Partial<StepContext> = {}): StepContext {
  const redisMock = {
    set:    vi.fn().mockResolvedValue("OK"),
    del:    vi.fn().mockResolvedValue(1),
    expire: vi.fn().mockResolvedValue(1),
    blpop:  vi.fn().mockResolvedValue(["menu:result:s1", "meu nome é João"]),
  }

  return {
    tenantId:             "tenant1",
    sessionId:            "s1",
    customerId:           "c1",
    instanceId:           "agente_sac_v1-001",
    sessionContext:       {},
    state:                makeState(),
    redis:                redisMock as any,
    contextStore:         makeContextStore() as any,
    mcpCall:              vi.fn().mockResolvedValue({ ok: true }),
    aiGatewayCall:        vi.fn()
      .mockResolvedValueOnce({ pergunta: "Qual é o seu nome e motivo do contato?" })
      .mockResolvedValueOnce({ fields: { "caller.nome": "João", "caller.motivo_contato": "cobrança" } }),
    saveState:            vi.fn().mockResolvedValue(undefined),
    retryStep:            vi.fn(),
    executeFallback:      vi.fn(),
    getJobId:             vi.fn().mockResolvedValue(null),
    setJobId:             vi.fn().mockResolvedValue(undefined),
    clearJobId:           vi.fn().mockResolvedValue(undefined),
    renewLock:            vi.fn().mockResolvedValue(true),
    maskedScope:          {},
    transactionOnFailure: null,
    ...overrides,
  }
}

// ─────────────────────────────────────────────
// Tests
// ─────────────────────────────────────────────

describe("executeResolve", () => {

  // ── 1. No contextStore ─────────────────────────────────────────────────────

  it("returns on_success with method=no_contextstore when contextStore is absent", async () => {
    const ctx  = makeCtx({})
    Reflect.deleteProperty(ctx, "contextStore") // remove so contextStore is truly absent
    const step = makeStep()
    const result = await executeResolve(step, ctx)

    expect(result.next_step_id).toBe("proximo")
    expect(result.transition_reason).toBe("on_success")
    // aiGatewayCall and mcpCall should not be called
    expect((ctx.aiGatewayCall as ReturnType<typeof vi.fn>).mock.calls).toHaveLength(0)
  })

  // ── 2. Cache hit — all fields complete ────────────────────────────────────

  it("returns on_success with method=cache immediately when gaps.complete=true", async () => {
    const contextStore = makeContextStore({ complete: true })
    const ctx          = makeCtx({ contextStore: contextStore as any })
    const step         = makeStep({ output_as: "ctx_result" })
    const result       = await executeResolve(step, ctx)

    expect(result.next_step_id).toBe("proximo")
    expect(result.transition_reason).toBe("on_success")
    expect((result.output_value as any).method).toBe("cache")
    expect((result.output_value as any).resolved).toBe(true)
    // No LLM or BLPOP
    expect((ctx.aiGatewayCall as ReturnType<typeof vi.fn>).mock.calls).toHaveLength(0)
    expect((ctx.redis as any).blpop.mock.calls).toHaveLength(0)
  })

  // ── 3. CRM lookup fills gaps ───────────────────────────────────────────────

  it("returns on_success with method=crm when CRM lookup resolves all gaps", async () => {
    const contextStore = makeContextStore({ completeAfterCrm: true })
    const ctx          = makeCtx({
      contextStore: contextStore as any,
      mcpCall: vi.fn().mockResolvedValue({ nome: "Maria", motivo: "cobranca" }),
    })
    const step = makeStep({
      crm_lookup: {
        mcp_server:   "mcp-server-crm",
        tool:         "customer_get",
        input:        { customer_id: "$.sessionContext.customer_id" },
        context_tags: {
          outputs: {
            nome:   { tag: "caller.nome",           confidence: 0.95, merge: "overwrite" },
            motivo: { tag: "caller.motivo_contato",  confidence: 0.95, merge: "overwrite" },
          },
        },
      },
    })
    const result = await executeResolve(step, ctx)

    expect(result.next_step_id).toBe("proximo")
    expect(result.transition_reason).toBe("on_success")
    // CRM tool was called
    expect((ctx.mcpCall as ReturnType<typeof vi.fn>).mock.calls[0]![0]).toBe("customer_get")
    expect((ctx.mcpCall as ReturnType<typeof vi.fn>).mock.calls[0]![2]).toBe("mcp-server-crm")
    // No BLPOP
    expect((ctx.redis as any).blpop.mock.calls).toHaveLength(0)
  })

  // ── 4. CRM error is non-fatal ─────────────────────────────────────────────

  it("continues to question generation when CRM lookup throws", async () => {
    const ctx = makeCtx({
      // Only the CRM customer_get fails; notification_send must succeed for BLPOP to run
      mcpCall: vi.fn().mockImplementation((tool: string) => {
        if (tool === "customer_get") return Promise.reject(new Error("crm unavailable"))
        return Promise.resolve({ ok: true })
      }),
    })
    const step = makeStep({
      crm_lookup: { mcp_server: "mcp-server-crm", tool: "customer_get" },
    })
    const result = await executeResolve(step, ctx)

    // Should reach customer_input phase (BLPOP succeeded in default ctx)
    expect(result.next_step_id).toBe("proximo")
    // At least one LLM call for question generation
    expect((ctx.aiGatewayCall as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThanOrEqual(1)
  })

  // ── 5. LLM question generation failure is non-fatal ───────────────────────

  it("returns on_success with method=skipped when question LLM fails", async () => {
    const ctx = makeCtx({
      aiGatewayCall: vi.fn().mockRejectedValue(new Error("LLM error")),
    })
    const step   = makeStep({ output_as: "ctx_result" })
    const result = await executeResolve(step, ctx)

    expect(result.next_step_id).toBe("proximo")
    expect((result.output_value as any).method).toBe("skipped")
    expect((result.output_value as any).resolved).toBe(false)
    // No BLPOP — skipped before reaching that phase
    expect((ctx.redis as any).blpop.mock.calls).toHaveLength(0)
  })

  // ── 6. notification_send failure → on_failure ─────────────────────────────

  it("returns on_failure when notification_send (mcpCall) throws", async () => {
    const ctx = makeCtx({
      mcpCall: vi.fn().mockRejectedValue(new Error("channel down")),
    })
    const step   = makeStep()
    const result = await executeResolve(step, ctx)

    expect(result.next_step_id).toBe("falha")
    expect(result.transition_reason).toBe("on_failure")
  })

  // ── 7. BLPOP timeout ──────────────────────────────────────────────────────

  it("returns on_success with method=timeout on BLPOP null result", async () => {
    const redisMock = {
      set:   vi.fn().mockResolvedValue("OK"),
      del:   vi.fn().mockResolvedValue(1),
      expire: vi.fn().mockResolvedValue(1),
      blpop: vi.fn().mockResolvedValue(null),  // null = timeout
    }
    const ctx    = makeCtx({ redis: redisMock as any })
    const step   = makeStep({ output_as: "ctx_result" })
    const result = await executeResolve(step, ctx)

    expect(result.next_step_id).toBe("proximo")
    expect((result.output_value as any).method).toBe("timeout")
    expect((result.output_value as any).resolved).toBe(false)
  })

  // ── 8. Client disconnect ──────────────────────────────────────────────────

  it("returns on_success with method=disconnected on session:closed signal", async () => {
    const redisMock = {
      set:   vi.fn().mockResolvedValue("OK"),
      del:   vi.fn().mockResolvedValue(1),
      expire: vi.fn().mockResolvedValue(1),
      blpop: vi.fn().mockResolvedValue(["session:closed:s1", "1"]),
    }
    const ctx    = makeCtx({ redis: redisMock as any })
    const step   = makeStep({ output_as: "ctx_result" })
    const result = await executeResolve(step, ctx)

    expect(result.next_step_id).toBe("proximo")
    expect((result.output_value as any).method).toBe("disconnected")
  })

  // ── 9. @mention trigger_step interrupt ────────────────────────────────────

  it("jumps to _mention_trigger_step when injected via resultKey", async () => {
    const redisMock = {
      set:   vi.fn().mockResolvedValue("OK"),
      del:   vi.fn().mockResolvedValue(1),
      expire: vi.fn().mockResolvedValue(1),
      blpop: vi.fn().mockResolvedValue([
        "menu:result:s1",
        JSON.stringify({ _mention_trigger_step: "step_escalada" }),
      ]),
    }
    const ctx    = makeCtx({ redis: redisMock as any })
    const step   = makeStep()
    const result = await executeResolve(step, ctx)

    expect(result.next_step_id).toBe("step_escalada")
    expect(result.transition_reason).toBe("on_success")
  })

  // ── 10. @mention terminate_self ──────────────────────────────────────────

  it("returns on_failure when _mention_terminate=true is injected", async () => {
    const redisMock = {
      set:   vi.fn().mockResolvedValue("OK"),
      del:   vi.fn().mockResolvedValue(1),
      expire: vi.fn().mockResolvedValue(1),
      blpop: vi.fn().mockResolvedValue([
        "menu:result:s1",
        JSON.stringify({ _mention_terminate: true }),
      ]),
    }
    const ctx    = makeCtx({ redis: redisMock as any })
    const step   = makeStep()
    const result = await executeResolve(step, ctx)

    expect(result.next_step_id).toBe("falha")
    expect(result.transition_reason).toBe("on_failure")
  })

  // ── 11. Successful customer input — fields extracted and written ──────────

  it("writes extracted fields to contextStore and returns method=customer_input", async () => {
    const contextStore = makeContextStore()
    const ctx = makeCtx({
      contextStore: contextStore as any,
      aiGatewayCall: vi.fn()
        .mockResolvedValueOnce({ pergunta: "Pode informar seu nome e motivo?" })
        .mockResolvedValueOnce({ fields: {
          "caller.nome":           "Ana",
          "caller.motivo_contato": "reclamação",
        }}),
    })
    const step   = makeStep({ output_as: "ctx_result" })
    const result = await executeResolve(step, ctx)

    expect(result.next_step_id).toBe("proximo")
    expect((result.output_value as any).method).toBe("customer_input")
    // contextStore.set was called for each extracted field
    expect(contextStore.set.mock.calls.length).toBeGreaterThanOrEqual(2)
    const tags = contextStore.set.mock.calls.map((c: any[]) => c[1])
    expect(tags).toContain("caller.nome")
    expect(tags).toContain("caller.motivo_contato")
  })

  // ── 12. LLM extraction failure is non-fatal ───────────────────────────────

  it("still returns on_success when extraction LLM throws", async () => {
    const ctx = makeCtx({
      aiGatewayCall: vi.fn()
        .mockResolvedValueOnce({ pergunta: "Qual é o seu nome?" })
        .mockRejectedValueOnce(new Error("extract failed")),
    })
    const step   = makeStep()
    const result = await executeResolve(step, ctx)

    expect(result.next_step_id).toBe("proximo")
    expect(result.transition_reason).toBe("on_success")
  })

  // ── 13. Lock stolen before BLPOP → on_failure ─────────────────────────────

  it("returns on_failure when renewLock returns false", async () => {
    const ctx    = makeCtx({ renewLock: vi.fn().mockResolvedValue(false) })
    const step   = makeStep()
    const result = await executeResolve(step, ctx)

    expect(result.next_step_id).toBe("falha")
    expect(result.transition_reason).toBe("on_failure")
    // BLPOP should not have been called
    expect((ctx.redis as any).blpop.mock.calls).toHaveLength(0)
  })

  // ── 14. output_as wires output_value into StepResult ─────────────────────

  it("does not include output_value when output_as is not set", async () => {
    const contextStore = makeContextStore({ complete: true })
    const ctx          = makeCtx({ contextStore: contextStore as any })
    const step         = makeStep()  // no output_as
    const result       = await executeResolve(step, ctx)

    expect(result.output_as).toBeUndefined()
    expect(result.output_value).toBeUndefined()
  })

  // ── 15. waitingKey always deleted in finally ──────────────────────────────

  it("always deletes waitingKey in finally, even on BLPOP timeout", async () => {
    const redisMock = {
      set:   vi.fn().mockResolvedValue("OK"),
      del:   vi.fn().mockResolvedValue(1),
      expire: vi.fn().mockResolvedValue(1),
      blpop: vi.fn().mockResolvedValue(null),
    }
    const ctx = makeCtx({ redis: redisMock as any })
    await executeResolve(makeStep(), ctx)

    const delCalls: string[][] = redisMock.del.mock.calls
    const deletedKeys = delCalls.map((c: string[]) => c[0]).filter((k): k is string => k !== undefined)
    expect(deletedKeys.some((k: string) => k.startsWith("menu:waiting:"))).toBe(true)
  })

})
