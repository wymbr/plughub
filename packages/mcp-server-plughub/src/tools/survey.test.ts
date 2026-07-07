/**
 * survey.test.ts — composeSurveySignals (server-side signal composition, ADR §D9).
 * Locks the answer→score mapping + dimension composition that survey_record uses.
 */
import { describe, it, expect } from "vitest"
import { composeSurveySignals, scoreOfAnswer, answersToMap } from "./survey"
import type { DialogForm } from "@plughub/schemas"

// Minimal DialogForm builder — composeSurveySignals only reads nodes + dimensions.
function form(nodes: unknown[], dimensions: unknown[] = []): DialogForm {
  return {
    form_id: "f", tenant_id: "t", name: "F", status: "published", version: 1,
    default_locale: "pt-BR", locales: ["pt-BR"],
    nodes, dimensions, tags: [],
    created_at: "2026-07-07T00:00:00.000Z", updated_at: "2026-07-07T00:00:00.000Z",
  } as unknown as DialogForm
}
const q = (output_key: string, capture: unknown, extra: Record<string, unknown> = {}) =>
  ({ id: output_key, kind: "question", prompt: "", interaction: "text", output_key, capture, ...extra })

const CSAT = { dimension_id: "csat", scale: { min: 1, max: 5 }, aggregation: "weighted_mean" }

describe("composeSurveySignals", () => {
  it("composes a weighted CSAT dimension from multiple questions", () => {
    const f = form(
      [
        q("atendimento", { dimension_id: "csat", weight: 3 }),
        q("resolucao",   { dimension_id: "csat", weight: 1 }),
      ],
      [CSAT],
    )
    // (5*3 + 1*1)/4 = 4.0 on the 1–5 scale
    const signals = composeSurveySignals(f, { atendimento: "5", resolucao: "1" })
    expect(signals).toEqual([{ metric: "csat", value: 4 }])
  })

  it("re-normalizes weights when a dimension question is NA/skipped", () => {
    const f = form(
      [
        q("atendimento", { dimension_id: "csat", weight: 3 }),
        q("resolucao",   { dimension_id: "csat", weight: 1 }),
      ],
      [CSAT],
    )
    // resolucao skipped → only atendimento (4) counts → 4
    const signals = composeSurveySignals(f, { atendimento: "4", resolucao: null })
    expect(signals).toEqual([{ metric: "csat", value: 4 }])
  })

  it("drops a dimension entirely when all its answers are NA", () => {
    const f = form(
      [q("a", { dimension_id: "csat" }), q("b", { dimension_id: "csat" })],
      [CSAT],
    )
    expect(composeSurveySignals(f, { a: null, b: null })).toEqual([])
  })

  it("emits a legacy single-question metric (no dimension)", () => {
    const f = form([q("nps", { metric: "nps" })])
    expect(composeSurveySignals(f, { nps: "9" })).toEqual([{ metric: "nps", value: 9 }])
  })

  it("maps an option answer through capture.value", () => {
    const f = form(
      [
        q("sat", { dimension_id: "csat" }, {
          interaction: "button",
          options: [
            { id: "otimo", value: "5", capture: { value: 5 } },
            { id: "ruim",  value: "1", capture: { value: 1 } },
          ],
        }),
      ],
      [CSAT],
    )
    // answer is the option value ("5") → capture.value 5
    expect(composeSurveySignals(f, { sat: "5" })).toEqual([{ metric: "csat", value: 5 }])
  })

  it("ignores verbatim questions with no capture", () => {
    const f = form([q("comment", undefined)])
    expect(composeSurveySignals(f, { comment: "great service" })).toEqual([])
  })

  it("composes a dimension and a legacy metric in the same form", () => {
    const f = form(
      [
        q("atendimento", { dimension_id: "csat" }),
        q("nps",         { metric: "nps" }),
      ],
      [CSAT],
    )
    const signals = composeSurveySignals(f, { atendimento: "4", nps: "8" })
    expect(signals).toContainEqual({ metric: "csat", value: 4 })
    expect(signals).toContainEqual({ metric: "nps", value: 8 })
    expect(signals).toHaveLength(2)
  })

  it("composes from the loop accumulator array (via answersToMap)", () => {
    const f = form(
      [
        q("atendimento", { dimension_id: "csat", weight: 2 }),
        q("resolucao",   { dimension_id: "csat", weight: 1 }),
      ],
      [CSAT],
    )
    // The loop step accumulates [{ value, output_key, metric? }]; the tool maps it.
    const loopAcc = [
      { value: "5", output_key: "atendimento", metric: undefined },
      { value: "3", output_key: "resolucao",   metric: undefined },
    ] as Array<{ output_key: string; value: string | number | null }>
    // weighted mean on 1–5: (5*2 + 3*1)/3 = 13/3 ≈ 4.333
    const signals = composeSurveySignals(f, answersToMap(loopAcc))
    expect(signals).toHaveLength(1)
    expect(signals[0]!.metric).toBe("csat")
    expect(signals[0]!.value).toBeCloseTo(13 / 3, 6)
  })
})

describe("answersToMap", () => {
  it("maps the loop array to a keyed record (last wins)", () => {
    expect(answersToMap([
      { output_key: "a", value: "1" },
      { output_key: "b", value: "2" },
      { output_key: "a", value: "9" },
    ])).toEqual({ a: "9", b: "2" })
  })
  it("passes a record through unchanged", () => {
    expect(answersToMap({ a: "1", b: null })).toEqual({ a: "1", b: null })
  })
})

describe("scoreOfAnswer", () => {
  const scalar = { id: "x", kind: "question", prompt: "", interaction: "text", output_key: "x" } as never
  it("parses a numeric scalar answer", () => {
    expect(scoreOfAnswer(scalar, "7")).toBe(7)
  })
  it("returns null for empty/NA answers", () => {
    expect(scoreOfAnswer(scalar, null)).toBeNull()
    expect(scoreOfAnswer(scalar, "")).toBeNull()
    expect(scoreOfAnswer(scalar, undefined)).toBeNull()
  })
  it("returns null for non-numeric free text", () => {
    expect(scoreOfAnswer(scalar, "great")).toBeNull()
  })
})
