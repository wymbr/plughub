/**
 * steps/transaction.test.ts
 * Unit tests for begin_transaction and end_transaction step executors.
 * Spec: docs/guias/masked-input.md
 *
 * Covered:
 *   1. begin_transaction — clears maskedScope, sets transactionOnFailure, returns __transaction_begin__
 *   2. begin_transaction — overwrites any existing maskedScope (idempotency)
 *   3. end_transaction — clears maskedScope, clears transactionOnFailure, returns __transaction_end__
 *   4. end_transaction with result_as — persists status (not values) in output
 *   5. end_transaction with explicit on_success — uses it instead of __transaction_end__
 *   6. end_transaction status includes fields_collected (field names, not values)
 */

import { describe, it, expect, vi } from "vitest"
import { executeBeginTransaction } from "../../steps/begin-transaction"
import { executeEndTransaction }   from "../../steps/end-transaction"
import type { StepContext }        from "../../executor"
import type { PipelineState }      from "@plughub/schemas"

// ─────────────────────────────────────────────
// Minimal context helper
// ─────────────────────────────────────────────

function makeCtx(overrides: Partial<StepContext> = {}): StepContext {
  return {
    tenantId:             "t1",
    sessionId:            "s1",
    customerId:           "c1",
    sessionContext:       {},
    state:                {
      flow_id: "f", current_step_id: "tx", status: "in_progress",
      started_at: "", updated_at: "", results: {}, retry_counters: {}, transitions: [],
    } as PipelineState,
    redis:                {} as any,
    mcpCall:              vi.fn(),
    aiGatewayCall:        vi.fn(),
    saveState:            vi.fn(),
    retryStep:            vi.fn(),
    executeFallback:      vi.fn(),
    getJobId:             vi.fn(),
    setJobId:             vi.fn(),
    clearJobId:           vi.fn(),
    maskedScope:          {},
    transactionOnFailure: null,
    ...overrides,
  }
}

// ─────────────────────────────────────────────
// begin_transaction
// ─────────────────────────────────────────────

describe("executeBeginTransaction", () => {

  it("clears maskedScope and sets transactionOnFailure", async () => {
    const ctx = makeCtx({
      maskedScope:          { existing: "value" },  // stale data
      transactionOnFailure: null,
    })

    const step = { id: "tx_start", type: "begin_transaction" as const, on_failure: "recolher" }
    await executeBeginTransaction(step, ctx)

    expect(ctx.maskedScope).toEqual({})             // cleared
    expect(ctx.transactionOnFailure).toBe("recolher")
  })

  it("returns __transaction_begin__ transition", async () => {
    const ctx  = makeCtx()
    const step = { id: "tx_start", type: "begin_transaction" as const, on_failure: "recolher" }
    const result = await executeBeginTransaction(step, ctx)

    expect(result.next_step_id).toBe("__transaction_begin__")
    expect(result.transition_reason).toBe("on_success")
  })

  it("overwrites an existing transactionOnFailure when called again (nested begin edge)", async () => {
    const ctx = makeCtx({ transactionOnFailure: "old_target" })
    const step = { id: "tx2", type: "begin_transaction" as const, on_failure: "new_target" }

    await executeBeginTransaction(step, ctx)

    expect(ctx.transactionOnFailure).toBe("new_target")
  })
})

// ─────────────────────────────────────────────
// end_transaction
// ─────────────────────────────────────────────

describe("executeEndTransaction", () => {

  it("clears maskedScope and transactionOnFailure", async () => {
    const ctx = makeCtx({
      maskedScope:          { senha: "abc123", pin: "9999" },
      transactionOnFailure: "recolher",
    })

    const step = { id: "tx_fim", type: "end_transaction" as const }
    await executeEndTransaction(step, ctx)

    expect(ctx.maskedScope).toEqual({})       // sensitive values discarded
    expect(ctx.transactionOnFailure).toBeNull()
  })

  it("returns __transaction_end__ when on_success is not declared", async () => {
    const ctx  = makeCtx()
    const step = { id: "tx_fim", type: "end_transaction" as const }
    const result = await executeEndTransaction(step, ctx)

    expect(result.next_step_id).toBe("__transaction_end__")
    expect(result.transition_reason).toBe("on_success")
  })

  it("uses explicit on_success when declared", async () => {
    const ctx  = makeCtx()
    const step = { id: "tx_fim", type: "end_transaction" as const, on_success: "confirmar" }
    const result = await executeEndTransaction(step, ctx)

    expect(result.next_step_id).toBe("confirmar")
  })

  it("does NOT include result_as output when result_as is absent", async () => {
    const ctx  = makeCtx()
    const step = { id: "tx_fim", type: "end_transaction" as const }
    const result = await executeEndTransaction(step, ctx)

    expect(result.output_as).toBeUndefined()
    expect(result.output_value).toBeUndefined()
  })

  it("persists transaction status (not values) in output when result_as is set", async () => {
    const ctx = makeCtx({
      maskedScope: { senha: "abc123", pin: "9999" },
    })
    const step = { id: "tx_fim", type: "end_transaction" as const, result_as: "operacao_status" }
    const result = await executeEndTransaction(step, ctx)

    expect(result.output_as).toBe("operacao_status")

    const status = result.output_value as Record<string, unknown>
    expect(status["status"]).toBe("ok")
    // Field names are persisted, never values
    expect(status["fields_collected"]).toContain("senha")
    expect(status["fields_collected"]).toContain("pin")
    // The actual sensitive values must NOT be in the output
    expect(JSON.stringify(status)).not.toContain("abc123")
    expect(JSON.stringify(status)).not.toContain("9999")
    expect(status["completed_at"]).toBeDefined()
  })

  it("records empty fields_collected when maskedScope was empty", async () => {
    const ctx  = makeCtx({ maskedScope: {} })
    const step = { id: "tx_fim", type: "end_transaction" as const, result_as: "r" }
    const result = await executeEndTransaction(step, ctx)

    const status = result.output_value as Record<string, unknown>
    expect(status["fields_collected"]).toEqual([])
  })
})
