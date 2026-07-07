/**
 * scoring.ts
 * Shared score-composition primitive — the common substructure between a survey
 * `DialogForm` dimension (customer-facing, dialog-api) and a Quality
 * `EvaluationForm` dimension (evaluator rubric, evaluation-api).
 *
 * See docs/adr/adr-survey-form-scoring-composition.md (D6/D8/D9).
 *
 * This module owns ONLY the composition math (scale, aggregation method, NA
 * re-normalization). The value mapping (option→score, choice_scores…) is
 * DOMAIN-specific and stays in each envelope's schema. `composeScore` is the
 * single source of truth for the deterministic per-respondent value — imported
 * by mcp-server (`survey_record`) and (later) evaluation-api, so both aggregate
 * identically.
 */

import { z } from "zod"

// ─────────────────────────────────────────────
// Scale
// ─────────────────────────────────────────────

/**
 * ScoreScale — numeric bounds of a scored item or group. Optional at either
 * level: a survey dimension carries ONE scale inherited by its questions
 * (homogeneous instrument); a Quality criterion carries its own (heterogeneous
 * dimension). The shared kernel therefore treats the group scale as optional.
 */
export const ScoreScaleSchema = z
  .object({
    min: z.number().default(0),
    max: z.number(),
  })
  .refine((s) => s.max > s.min, {
    message: "scale.max must be greater than scale.min",
  })
export type ScoreScale = z.infer<typeof ScoreScaleSchema>

// ─────────────────────────────────────────────
// Aggregation
// ─────────────────────────────────────────────

/**
 * How the items within a group (dimension) are composed into one value.
 *   weighted_mean — weighted average; uniform weights ⇒ arithmetic mean.
 *                   Re-normalizes over non-NA items.
 *   min           — the minimum normalized item score (worst-of).
 *
 * Population-level normalization (NPS %, CSAT top-2-box, cross-respondent
 * average) is NOT here — it stays read-time in analytics. This composes only the
 * PER-RESPONDENT value. Quality's legacy vocabulary maps onto this:
 * weighted_average→weighted_mean, min_score→min.
 */
export const ScoreAggregationSchema = z.enum(["weighted_mean", "min"])
export type ScoreAggregation = z.infer<typeof ScoreAggregationSchema>

// ─────────────────────────────────────────────
// Deterministic composition
// ─────────────────────────────────────────────

/**
 * One item's contribution at compose time. `score` is the raw domain value
 * already mapped to a number (option value, raw numeric answer, criterion score)
 * or `null` for NA/skipped (which re-normalizes the weights). `scale` overrides
 * the group scale for this item (Quality per-criterion scales); absent ⇒ the
 * group scale applies.
 */
export interface ScoredItem {
  score: number | null
  weight?: number
  scale?: ScoreScale
}

/**
 * composeScore — deterministic per-respondent composition. Normalizes each live
 * item to 0..1 against its effective scale (item scale ?? group scale), applies
 * the aggregation method over the non-NA items (weights re-normalized), then maps
 * the unit result back ONTO the group scale when one is given (e.g. CSAT 1–5 →
 * 4.3), or returns the raw 0..1 unit when there is no group scale.
 *
 * Returns `null` when every item is NA (the caller drops the signal / marks na).
 *
 * Invariants: pure, deterministic, no I/O. Single source of truth so that
 * `survey_record` (mcp-server) and evaluation-api produce identical numbers.
 */
export function composeScore(
  items: ScoredItem[],
  method: ScoreAggregation = "weighted_mean",
  groupScale?: ScoreScale,
): number | null {
  const live = items.filter((i) => i.score != null)
  if (live.length === 0) return null

  const unitOf = (i: ScoredItem): number => {
    const s = i.scale ?? groupScale
    if (!s) return i.score as number // no scale → treat raw value as the unit
    const span = s.max - s.min
    return span === 0 ? 0 : ((i.score as number) - s.min) / span
  }

  let unit: number
  if (method === "min") {
    unit = Math.min(...live.map(unitOf))
  } else {
    const wsum = live.reduce((acc, i) => acc + (i.weight ?? 1), 0)
    unit =
      wsum === 0
        ? 0
        : live.reduce((acc, i) => acc + unitOf(i) * (i.weight ?? 1), 0) / wsum
  }

  if (groupScale) return groupScale.min + unit * (groupScale.max - groupScale.min)
  return unit
}
