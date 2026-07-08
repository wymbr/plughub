/**
 * dialog.test.ts — ask_when evaluator + forward-reference validation
 * (conditional skip-logic, adr-dialog-conditional-skip-logic).
 */
import { describe, it, expect } from "vitest"
import { evaluateAskWhen, askWhenForwardRefErrors, type AskWhen, type DialogForm } from "./dialog"

describe("evaluateAskWhen", () => {
  const answers = { csat: "2", nps: 9, operadora: "vivo" }

  it("no guard → always present", () => {
    expect(evaluateAskWhen(undefined, answers)).toBe(true)
  })

  it("numeric comparisons (string answers coerced)", () => {
    expect(evaluateAskWhen({ field: "csat", op: "lt", value: 3 }, answers)).toBe(true)
    expect(evaluateAskWhen({ field: "csat", op: "gte", value: 3 }, answers)).toBe(false)
    expect(evaluateAskWhen({ field: "nps", op: "gt", value: 8 }, answers)).toBe(true)
    expect(evaluateAskWhen({ field: "nps", op: "lte", value: 6 }, answers)).toBe(false)
  })

  it("eq / ne (numeric then string)", () => {
    expect(evaluateAskWhen({ field: "csat", op: "eq", value: 2 }, answers)).toBe(true)
    expect(evaluateAskWhen({ field: "operadora", op: "eq", value: "vivo" }, answers)).toBe(true)
    expect(evaluateAskWhen({ field: "operadora", op: "ne", value: "claro" }, answers)).toBe(true)
  })

  it("in (membership)", () => {
    expect(evaluateAskWhen({ field: "operadora", op: "in", value: ["vivo", "tim"] }, answers)).toBe(true)
    expect(evaluateAskWhen({ field: "operadora", op: "in", value: ["claro", "oi"] }, answers)).toBe(false)
    expect(evaluateAskWhen({ field: "csat", op: "in", value: [1, 2, 3] }, answers)).toBe(true)
  })

  it("absent/empty answer → skip (guard false)", () => {
    expect(evaluateAskWhen({ field: "missing", op: "lt", value: 3 }, answers)).toBe(false)
    expect(evaluateAskWhen({ field: "blank", op: "eq", value: "" }, { blank: "" })).toBe(false)
  })

  it("non-numeric answer with numeric op → false (NaN compare)", () => {
    expect(evaluateAskWhen({ field: "operadora", op: "lt", value: 3 }, answers)).toBe(false)
  })
})

describe("askWhenForwardRefErrors", () => {
  const q = (output_key: string, ask_when?: AskWhen) =>
    ({ id: output_key, kind: "question", prompt: "", interaction: "text", output_key, ask_when })

  it("accepts a backward reference", () => {
    const form = { nodes: [q("csat"), q("motivo", { field: "csat", op: "lt", value: 3 })] } as unknown as DialogForm
    expect(askWhenForwardRefErrors(form)).toEqual([])
  })

  it("flags a forward reference", () => {
    // 'motivo' guards on 'csat' which appears AFTER it → error
    const form = { nodes: [q("motivo", { field: "csat", op: "lt", value: 3 }), q("csat")] } as unknown as DialogForm
    expect(askWhenForwardRefErrors(form)).toEqual([{ node_id: "motivo", field: "csat" }])
  })

  it("flags an unknown reference", () => {
    const form = { nodes: [q("csat"), q("motivo", { field: "nope", op: "eq", value: 1 })] } as unknown as DialogForm
    expect(askWhenForwardRefErrors(form)).toEqual([{ node_id: "motivo", field: "nope" }])
  })
})
