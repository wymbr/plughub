/**
 * scoring.test.ts — composeScore (shared per-respondent composition).
 * Locks the NA re-normalization + scale-normalization + weighting behavior that
 * survey_record (and later evaluation-api) rely on.
 */
import { describe, it, expect } from "vitest"
import { composeScore, type ScoredItem } from "./scoring"

const CSAT = { min: 1, max: 5 } // typical instrument scale

describe("composeScore", () => {
  it("uniform weights = arithmetic mean, remapped onto the group scale", () => {
    // items already on the CSAT scale → mean of 5,4,3 = 4
    const items: ScoredItem[] = [{ score: 5 }, { score: 4 }, { score: 3 }]
    expect(composeScore(items, "weighted_mean", CSAT)).toBeCloseTo(4, 6)
  })

  it("weights produce a weighted mean, not arithmetic", () => {
    // On CSAT 1–5: 5 (w=3) and 1 (w=1) → weighted mean = (5*3 + 1*1)/4 = 4.0
    // (normalize-then-remap is equivalent to a direct weighted mean on the scale).
    const items: ScoredItem[] = [
      { score: 5, weight: 3 },
      { score: 1, weight: 1 },
    ]
    expect(composeScore(items, "weighted_mean", CSAT)).toBeCloseTo(4.0, 6)
    // arithmetic mean would be 3.0 — confirm the weighting changed the result
    expect(composeScore(items, "weighted_mean", CSAT)).not.toBeCloseTo(3.0, 6)
  })

  it("NA items are dropped and weights re-normalize", () => {
    // 4, 4, NA → mean of the two live items = 4 (NA must not pull it down)
    const items: ScoredItem[] = [{ score: 4 }, { score: 4 }, { score: null }]
    expect(composeScore(items, "weighted_mean", CSAT)).toBeCloseTo(4, 6)
  })

  it("all-NA returns null", () => {
    const items: ScoredItem[] = [{ score: null }, { score: null }]
    expect(composeScore(items, "weighted_mean", CSAT)).toBeNull()
  })

  it("normalizes mixed item scales before weighting (Quality-style)", () => {
    // item A 0..5 → 5 (unit 1.0); item B 0..10 → 5 (unit 0.5); equal weights →
    // unit 0.75. No group scale → returns the raw 0..1 unit.
    const items: ScoredItem[] = [
      { score: 5, scale: { min: 0, max: 5 } },
      { score: 5, scale: { min: 0, max: 10 } },
    ]
    expect(composeScore(items, "weighted_mean")).toBeCloseTo(0.75, 6)
  })

  it("min takes the worst normalized item, remapped onto the group scale", () => {
    // units: 5→1.0, 2→0.25 → min 0.25 → 1 + 0.25*4 = 2
    const items: ScoredItem[] = [{ score: 5 }, { score: 2 }]
    expect(composeScore(items, "min", CSAT)).toBeCloseTo(2, 6)
  })

  it("without a scale, treats the raw value as the unit (identity)", () => {
    const items: ScoredItem[] = [{ score: 7 }, { score: 9 }]
    expect(composeScore(items, "weighted_mean")).toBeCloseTo(8, 6)
  })
})
