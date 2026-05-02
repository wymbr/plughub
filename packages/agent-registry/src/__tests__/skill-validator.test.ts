/**
 * skill-validator.test.ts
 * Unit tests for validateMaskedBlock — reason step inside begin/end_transaction check.
 */

import { describe, it, expect } from "vitest"
import { validateMaskedBlock } from "../validators/skill"
import type { SkillFlow } from "@plughub/schemas"

// ─────────────────────────────────────────────
// Helpers to build minimal valid SkillFlow stubs
// ─────────────────────────────────────────────

const notifyStep = (id: string, on_success?: string): object => ({
  type: "notify",
  id,
  message: "hello",
  on_success,
})

const completeStep = (id: string): object => ({
  type:   "complete",
  id,
  outcome: "resolved",
  issue_status: "closed",
})

const reasonStep = (id: string, on_success?: string): object => ({
  type:   "reason",
  id,
  message: "think",
  output_schema: { campo: "string" },
  on_success,
})

const menuStep = (id: string, on_success?: string): object => ({
  type:        "menu",
  id,
  interaction: "text",
  prompt:      "Hi?",
  on_success,
})

const beginTx = (id: string, on_failure?: string): object => ({
  type:       "begin_transaction",
  id,
  on_failure: on_failure ?? "fim",
})

const endTx = (id: string, result_as?: string): object => ({
  type:      "end_transaction",
  id,
  result_as: result_as ?? "tx_result",
})

const choiceStep = (id: string, conditions: object[], defaultNext?: string): object => ({
  type:       "choice",
  id,
  conditions,
  default:    defaultNext,
})

// Build a flow. Steps are cast — we only test the validator logic.
function mkFlow(steps: object[]): SkillFlow {
  const arr = steps as SkillFlow["steps"]
  const entry = arr[0]?.id ?? "start"
  return { entry, steps: arr }
}

// ─────────────────────────────────────────────
// Tests
// ─────────────────────────────────────────────

describe("validateMaskedBlock", () => {

  // ── No begin_transaction at all → always valid ─────────────────────────────

  it("returns no errors for flow with no begin_transaction", () => {
    const flow = mkFlow([
      reasonStep("analyze", "respond"),
      notifyStep("respond", "fim"),
      completeStep("fim"),
    ])
    expect(validateMaskedBlock(flow)).toEqual([])
  })

  it("returns no errors for empty steps array", () => {
    // Edge case: technically SkillFlowSchema requires min(1), but the validator
    // should not crash on an empty array.
    const flow = { steps: [] } as unknown as SkillFlow
    expect(validateMaskedBlock(flow)).toEqual([])
  })

  // ── Valid: begin_transaction block with no reason step ──────────────────────

  it("returns no errors when block contains only menu and invoke steps", () => {
    const flow = mkFlow([
      beginTx("tx_start", "error"),
      menuStep("coletar_senha", "validar"),
      notifyStep("validar", "tx_fim"),
      endTx("tx_fim"),
      completeStep("error"),
    ])
    expect(validateMaskedBlock(flow)).toEqual([])
  })

  it("returns no errors when reason step appears BEFORE begin_transaction", () => {
    const flow = mkFlow([
      reasonStep("pre_reason", "tx_start"),
      beginTx("tx_start", "error"),
      menuStep("coletar", "tx_fim"),
      endTx("tx_fim"),
      completeStep("error"),
    ])
    expect(validateMaskedBlock(flow)).toEqual([])
  })

  it("returns no errors when reason step appears AFTER end_transaction", () => {
    const flow = mkFlow([
      beginTx("tx_start", "error"),
      menuStep("coletar", "tx_fim"),
      endTx("tx_fim"),
      reasonStep("pos_reason", "done"),
      completeStep("done"),
      completeStep("error"),
    ])
    expect(validateMaskedBlock(flow)).toEqual([])
  })

  // ── Invalid: reason step directly inside block ───────────────────────────────

  it("reports error when reason step is the first step inside the block", () => {
    // begin_transaction at position 0 → first-in-block is position 1
    const flow = mkFlow([
      beginTx("tx_start", "error"),
      reasonStep("bad_reason", "tx_fim"),    // ← directly after begin_transaction
      endTx("tx_fim"),
      completeStep("error"),
    ])
    const errors = validateMaskedBlock(flow)
    expect(errors).toHaveLength(1)
    expect(errors[0]).toContain("bad_reason")
    expect(errors[0]).toContain("tx_start")
    expect(errors[0]).toContain("reason")
    expect(errors[0]).toContain("begin_transaction")
  })

  it("reports error when reason step is reachable via on_success chain inside block", () => {
    const flow = mkFlow([
      beginTx("tx_start", "error"),
      menuStep("coletar", "mid"),        // on_success → mid
      notifyStep("mid", "bad_reason"),   // on_success → bad_reason  (still inside block)
      reasonStep("bad_reason", "tx_fim"),
      endTx("tx_fim"),
      completeStep("error"),
    ])
    const errors = validateMaskedBlock(flow)
    expect(errors).toHaveLength(1)
    expect(errors[0]).toContain("bad_reason")
  })

  it("reports error when reason is inside block reached via choice branch", () => {
    const flow = mkFlow([
      beginTx("tx_start", "error"),
      choiceStep("router", [
        { field: "$.pipeline_state.results.x", operator: "eq", value: "1", next: "bad_reason" },
      ], "tx_fim"),
      reasonStep("bad_reason", "tx_fim"),
      endTx("tx_fim"),
      completeStep("error"),
    ])
    const errors = validateMaskedBlock(flow)
    expect(errors).toHaveLength(1)
    expect(errors[0]).toContain("bad_reason")
  })

  it("reports error when reason is inside block reached via choice default branch", () => {
    const flow = mkFlow([
      beginTx("tx_start", "error"),
      choiceStep("router", [], "bad_reason"),   // default → bad_reason
      reasonStep("bad_reason", "tx_fim"),
      endTx("tx_fim"),
      completeStep("error"),
    ])
    const errors = validateMaskedBlock(flow)
    expect(errors).toHaveLength(1)
    expect(errors[0]).toContain("bad_reason")
  })

  // ── Valid: on_failure exit paths (outside the block) are not visited ─────────

  it("does NOT report error when reason is only on on_failure path (outside block)", () => {
    // on_failure exits the masked block — the validator only follows success edges
    const flow = mkFlow([
      beginTx("tx_start", "fallback_reason"),
      menuStep("coletar", "tx_fim"),
      endTx("tx_fim"),
      reasonStep("fallback_reason", "done"),   // reachable only via on_failure
      completeStep("done"),
    ])
    expect(validateMaskedBlock(flow)).toEqual([])
  })

  // ── Multiple begin_transaction blocks ────────────────────────────────────────

  it("reports errors for each separate block that contains a reason step", () => {
    const flow = mkFlow([
      beginTx("tx1_start", "error"),
      reasonStep("bad1", "tx1_fim"),
      endTx("tx1_fim"),
      notifyStep("between", "tx2_start"),
      beginTx("tx2_start", "error"),
      reasonStep("bad2", "tx2_fim"),
      endTx("tx2_fim"),
      completeStep("error"),
    ])
    const errors = validateMaskedBlock(flow)
    expect(errors).toHaveLength(2)
    expect(errors.some(e => e.includes("bad1"))).toBe(true)
    expect(errors.some(e => e.includes("bad2"))).toBe(true)
  })

  it("reports error only for the block that contains reason (clean block is fine)", () => {
    const flow = mkFlow([
      beginTx("tx1_start", "error"),
      menuStep("coletar_ok", "tx1_fim"),     // clean block
      endTx("tx1_fim"),
      notifyStep("between", "tx2_start"),
      beginTx("tx2_start", "error"),
      reasonStep("bad_reason", "tx2_fim"),   // invalid block
      endTx("tx2_fim"),
      completeStep("error"),
    ])
    const errors = validateMaskedBlock(flow)
    expect(errors).toHaveLength(1)
    expect(errors[0]).toContain("bad_reason")
    expect(errors[0]).toContain("tx2_start")
  })

  // ── Edge: begin_transaction is the last step (no first-in-block) ─────────────

  it("does not crash when begin_transaction is the last step in the array", () => {
    const flow = mkFlow([
      notifyStep("prior", "tx_start"),
      beginTx("tx_start"),
      // no steps after begin_transaction
    ])
    expect(validateMaskedBlock(flow)).toEqual([])
  })

  // ── end_transaction correctly stops BFS propagation ─────────────────────────

  it("does not follow edges through end_transaction to steps beyond the block", () => {
    // reason step is AFTER end_transaction — it should NOT be flagged
    const flow = mkFlow([
      beginTx("tx_start", "error"),
      menuStep("coletar", "tx_fim"),
      endTx("tx_fim"),          // end_transaction closes the block
      reasonStep("post_reason", "done"),   // outside block
      completeStep("done"),
      completeStep("error"),
    ])
    // end_transaction has no on_success; BFS stops there.
    // post_reason is only in the array after tx_fim — not inside the block.
    expect(validateMaskedBlock(flow)).toEqual([])
  })
})
