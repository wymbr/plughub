/**
 * dialog.ts
 * Generic scripted-dialog form schema — the shared "content" layer for the
 * dialog primitive (survey + OTP). See docs/product/dialog-primitive-and-runner-design.md
 * and docs/adr/adr-otp-workflow-and-dialog-primitive.md.
 *
 * A DialogForm is DATA owned by the tenant: a versioned (draft/published),
 * i18n, LINEAR script of nodes that a Tier-3 dialog-runner presents to the
 * customer. It carries NO control flow (branching lives in the calling skill —
 * the four-seam invariant) and NO channel-specific rendering (the Channel
 * Gateway adapter owns that).
 *
 * Two node kinds:
 *   statement — no response; rendered as `notify`.
 *   question  — captures customer input; rendered as `menu` (dynamic options/fields).
 *
 * Retry lives on the SAME surface but only for FORMAT validation (required /
 * numeric / pattern). Semantic retry (wrong OTP code, "is this a detractor")
 * is CONTROL — the calling workflow decides and re-delegates.
 */

import { z } from "zod"
import { ScoreScaleSchema, ScoreAggregationSchema } from "./scoring"

// ─────────────────────────────────────────────
// i18n — embedded locale map (D-I18N)
// ─────────────────────────────────────────────

/** BCP-47-ish locale code, e.g. "pt-BR", "en". */
export const LocaleCodeSchema = z.string().min(2)
export type LocaleCode = z.infer<typeof LocaleCodeSchema>

/**
 * Localized text: either a bare string (single-locale) or a { locale: text }
 * map. Resolution order: map[session_locale] ?? map[default_locale] ?? first.
 * The form is tenant data, so translations travel inside the JSON — never in
 * platform-ui locale files (which are code).
 */
export const LocalizedTextSchema = z.union([
  z.string(),
  z.record(z.string(), z.string()),
])
export type LocalizedText = z.infer<typeof LocalizedTextSchema>

// ─────────────────────────────────────────────
// Format-level validation (NOT semantic)
// ─────────────────────────────────────────────

/**
 * Format-only validation on a scalar answer/field. Enables the retry-on-same-
 * surface affordance. Semantic checks (correct OTP code, business rules) are
 * never expressed here — those are control, owned by the calling skill.
 */
export const DialogValidationSchema = z
  .object({
    numeric:    z.boolean().optional(),
    pattern:    z.string().optional(), // regex, format only
    min_length: z.number().int().nonnegative().optional(),
    max_length: z.number().int().nonnegative().optional(),
    min:        z.number().optional(),
    max:        z.number().optional(),
  })
  .optional()
export type DialogValidation = z.infer<typeof DialogValidationSchema>

/** Channel-agnostic response format for a question. Adapter maps to the UI. */
export const DialogInteractionSchema = z.enum([
  "text",
  "button",
  "list",
  "checklist",
  "form",
])
export type DialogInteraction = z.infer<typeof DialogInteractionSchema>

// ─────────────────────────────────────────────
// Conditional skip-logic — declarative guard (ADR adr-dialog-conditional-skip-logic)
// ─────────────────────────────────────────────

/** Comparison operators for an `ask_when` guard. */
export const AskWhenOpSchema = z.enum(["lt", "lte", "gt", "gte", "eq", "ne", "in"])
export type AskWhenOp = z.infer<typeof AskWhenOpSchema>

/**
 * AskWhen — a DECLARATIVE skip-logic guard on a node (not control flow). The node
 * is presented only when the guard passes. `field` references a PRIOR question's
 * `output_key` (never a mutable "response"); `value` is a scalar (or a list for
 * `in`). Bounded and side-effect-free (sandboxed-expression principle) — the
 * runner stays linear and just SKIPS a node whose guard is false. Absent guard =
 * always present. Real control (delegate/escalate/tool) stays in the skill.
 */
export const AskWhenSchema = z.object({
  field: z.string().min(1),
  op:    AskWhenOpSchema,
  value: z.union([
    z.number(),
    z.string(),
    z.boolean(),
    z.array(z.union([z.number(), z.string()])),
  ]),
})
export type AskWhen = z.infer<typeof AskWhenSchema>

// ─────────────────────────────────────────────
// Declarative capture binding (survey domain)
// ─────────────────────────────────────────────

/**
 * Declarative binding echoed back to the domain (e.g. survey) so it can build
 * `survey_record` signals from the raw answers WITHOUT re-fetching the form.
 * This is DATA, not logic — it never drives branching. Absent capture on a
 * free-text question ⇒ verbatim/open_text (the domain routes it to its sink).
 *
 * Two ways a question contributes to a signal (ADR §D1/§D7):
 *   - `metric` (legacy) — a STANDALONE single-question metric = its own 1-item
 *     dimension. Kept for backward-compat with existing forms.
 *   - `dimension_id` (composition) — the question contributes to a declared
 *     `DialogForm.dimensions[]` entry; `weight` is its weight WITHIN that
 *     dimension (default 1). The dimension owns the scale + aggregation; the
 *     domain (`survey_record`) composes the per-respondent value via
 *     `composeScore`. Use one OR the other, not both.
 */
export const DialogCaptureSchema = z
  .object({
    /** Signal metric key, snake_case (e.g. "csat", "nps"). Legacy standalone. */
    metric: z
      .string()
      .regex(/^[a-z0-9_]+$/, { message: "metric must be snake_case (a-z0-9_)" })
      .optional(),
    /** Dimension this question feeds (must match a `DialogForm.dimensions[].dimension_id`). */
    dimension_id: z
      .string()
      .regex(/^[a-z0-9_]+$/, { message: "dimension_id must be snake_case (a-z0-9_)" })
      .optional(),
    /** Weight of this question WITHIN its dimension (relative; normalized at compose). Default 1. */
    weight: z.number().min(0).optional(),
    /** Fixed machine value for this option/field (e.g. button "4" → score 4). */
    value: z.union([z.number(), z.string()]).optional(),
  })
  .optional()
export type DialogCapture = z.infer<typeof DialogCaptureSchema>

// ─────────────────────────────────────────────
// Dimension — composed instrument (survey_definition layer)
// ─────────────────────────────────────────────

/**
 * DialogDimension — a survey INSTRUMENT (csat, nps, ces, …) that groups
 * questions and composes ONE per-respondent value. The promotion of the legacy
 * per-question `capture.metric` (ADR §D1). Homogeneous by design: the `scale` is
 * declared once here and INHERITED by member questions (options only carry the
 * value mapping, validated against this range). Dimensions are PARALLEL — each
 * emits its own signal (`metric = dimension_id`); they do NOT roll up into a
 * single form composite (unlike a Quality form). See ADR §D2/§D4/§D5.
 */
export const DialogDimensionSchema = z.object({
  /** Instrument id = the emitted signal metric key, snake_case (e.g. "csat"). */
  dimension_id: z
    .string()
    .regex(/^[a-z0-9_]+$/, { message: "dimension_id must be snake_case (a-z0-9_)" }),
  /** Optional human label (editor/reporting). */
  label: LocalizedTextSchema.optional(),
  /** Numeric scale of the instrument, inherited by member questions (e.g. 1–5). */
  scale: ScoreScaleSchema,
  /** How member questions compose the per-respondent value. Default weighted mean. */
  aggregation: ScoreAggregationSchema.default("weighted_mean"),
  /**
   * Instrument-level render, inherited by member questions (a scored instrument
   * is homogeneous). The editor MATERIALIZES this into each scored question's
   * `interaction`+`options` on save, so the runtime keeps reading them per-node.
   * Optional — absent means the questions carry their own render (legacy forms).
   */
  interaction: DialogInteractionSchema.optional(),
  /**
   * Optional anchor label per scale point (length = scale.max − scale.min + 1),
   * e.g. ["péssimo", …, "ótimo"]. Materialized as the option labels; absent =
   * the numeric value is the label.
   */
  anchors: z.array(LocalizedTextSchema).optional(),
  /**
   * Reserved for an OPTIONAL future form-level composite (health score) — a
   * roll-up over the parallel dimensions. Unused while dimensions stay parallel
   * (the default). See ADR § Decisões em aberto #1.
   */
  weight: z.number().min(0).optional(),
})
export type DialogDimension = z.infer<typeof DialogDimensionSchema>

// ─────────────────────────────────────────────
// Options / fields
// ─────────────────────────────────────────────

export const DialogOptionSchema = z.object({
  id:      z.string().min(1),
  label:   LocalizedTextSchema,
  /** Machine value returned to the runner; defaults to `id` when absent. */
  value:   z.string().optional(),
  capture: DialogCaptureSchema,
})
export type DialogOption = z.infer<typeof DialogOptionSchema>

export const DialogFieldSchema = z.object({
  id:         z.string().min(1),
  label:      LocalizedTextSchema,
  /** Channel-agnostic field type, e.g. "text" | "number". Adapter maps to UI. */
  type:       z.string().min(1),
  required:   z.boolean().default(false),
  /**
   * When true this field is masked: its value never appears in the runner's raw
   * return (masked-input invariant) — it stays in the in-memory masked scope.
   */
  masked:     z.boolean().optional(),
  validation: DialogValidationSchema,
  capture:    DialogCaptureSchema,
})
export type DialogField = z.infer<typeof DialogFieldSchema>

// ─────────────────────────────────────────────
// Visibility (mirrors MenuStep.visibility)
// ─────────────────────────────────────────────

/**
 * Prompt/statement visibility. "all" (default) / "agents_only", or an explicit
 * participant_id array (may contain @ctx.* refs resolved by the runner).
 */
export const DialogVisibilitySchema = z.union([
  z.enum(["all", "agents_only"]),
  z.array(z.string().min(1)).min(1),
])
export type DialogVisibility = z.infer<typeof DialogVisibilitySchema>

// ─────────────────────────────────────────────
// Nodes — discriminated union on `kind`
// ─────────────────────────────────────────────

/** Statement node — no response; rendered as `notify`. */
export const StatementNodeSchema = z.object({
  id:         z.string().min(1),
  kind:       z.literal("statement"),
  text:       LocalizedTextSchema,
  visibility: DialogVisibilitySchema.optional(),
  /** Declarative skip-logic guard (references a prior question's output_key). */
  ask_when:   AskWhenSchema.optional(),
})
export type StatementNode = z.infer<typeof StatementNodeSchema>

/**
 * Retry affordance on the same surface — FORMAT failures only. `max_attempts`
 * is honored fully in slice 2 (needs the engine counter); slice-1 runners treat
 * it as "one reprompt".
 */
export const DialogRetrySchema = z
  .object({
    reprompt:     LocalizedTextSchema,
    max_attempts: z.number().int().min(1).default(2),
  })
  .optional()
export type DialogRetry = z.infer<typeof DialogRetrySchema>

/** Question node — captures customer input; rendered as `menu`. */
export const QuestionNodeSchema = z.object({
  id:          z.string().min(1),
  kind:        z.literal("question"),
  prompt:      LocalizedTextSchema,
  interaction: DialogInteractionSchema.default("text"),
  options:     z.array(DialogOptionSchema).optional(),
  fields:      z.array(DialogFieldSchema).optional(),
  masked:      z.boolean().optional(),
  /** Key under which the raw answer lands in the runner's return `answers`. */
  output_key:  z.string().min(1),
  /** Question-level capture (single-answer questions). */
  capture:     DialogCaptureSchema,
  /** Format validation (scalar questions). */
  validation:  DialogValidationSchema,
  retry:       DialogRetrySchema,
  visibility:  DialogVisibilitySchema.optional(),
  timeout_s:   z.number().int().min(-1).default(300),
  /** Declarative skip-logic guard (references a prior question's output_key). */
  ask_when:    AskWhenSchema.optional(),
})
export type QuestionNode = z.infer<typeof QuestionNodeSchema>

export const DialogNodeSchema = z.discriminatedUnion("kind", [
  StatementNodeSchema,
  QuestionNodeSchema,
])
export type DialogNode = z.infer<typeof DialogNodeSchema>

// ─────────────────────────────────────────────
// DialogForm — versioned, i18n, linear script
// ─────────────────────────────────────────────

export const DialogFormStatusSchema = z.enum(["draft", "published"])
export type DialogFormStatus = z.infer<typeof DialogFormStatusSchema>

/**
 * DialogForm — the reusable, versioned dialog script. Stored canonically in
 * dialog-api; resolved at runtime via the generic `form_get` MCP tool.
 *
 * `nodes` order IS the flow — there is deliberately no conditional `next`
 * (branching = control, owned by the calling skill). `tags` is non-semantic,
 * used only to group forms into editor views (e.g. "survey", "otp").
 */
export const DialogFormSchema = z.object({
  form_id:        z.string().min(1),
  tenant_id:      z.string().min(1),
  name:           z.string().min(1),
  description:    z.string().optional(),
  status:         DialogFormStatusSchema.default("draft"),
  /** Monotonic version counter — incremented on each structural change. */
  version:        z.number().int().positive().default(1),
  default_locale: LocaleCodeSchema,
  locales:        z.array(LocaleCodeSchema).min(1),
  nodes:          z.array(DialogNodeSchema).min(1),
  /**
   * Composed instruments (survey_definition layer). Empty for a plain dialog
   * (OTP) or a legacy per-question-`metric` survey. When present, questions bind
   * via `capture.dimension_id` and the domain composes one signal per dimension.
   */
  dimensions:     z.array(DialogDimensionSchema).default([]),
  /**
   * OPTIONAL form-level composite (health score) — a weighted roll-up OVER the
   * parallel dimensions into ONE extra signal. Each dimension contributes its
   * per-respondent value (normalized by its scale) weighted by
   * `DialogDimension.weight`; the composite is emitted on a 0–100 scale under
   * `metric`. Absent = dimensions stay purely parallel (the default).
   */
  composite:      z.object({ metric: z.string().regex(/^[a-z0-9_]+$/) }).optional(),
  tags:           z.array(z.string()).default([]),
  created_at:     z.string().datetime(),
  updated_at:     z.string().datetime(),
})
export type DialogForm = z.infer<typeof DialogFormSchema>

// ─────────────────────────────────────────────
// Runtime helpers (single source of truth)
// ─────────────────────────────────────────────

/**
 * Resolve a LocalizedText to a plain string. Order: map[locale] ??
 * map[defaultLocale] ?? first available value. A bare string resolves to itself.
 * Kept here so the runner, the editor preview and any renderer resolve identically.
 */
export function resolveLocalizedText(
  text: LocalizedText,
  locale?: string,
  defaultLocale?: string,
): string {
  if (typeof text === "string") return text
  if (locale && text[locale] !== undefined) return text[locale]
  if (defaultLocale && text[defaultLocale] !== undefined) return text[defaultLocale]
  const first = Object.values(text)[0]
  return first ?? ""
}

// ─────────────────────────────────────────────
// ask_when — pure evaluator + forward-reference validation
// ─────────────────────────────────────────────

function _num(x: unknown): number {
  return typeof x === "number" ? x : Number(x)
}

/** eq/ne semantics: numeric compare when both coerce to a finite number, else string. */
function _eq(a: unknown, b: unknown): boolean {
  const na = _num(a), nb = _num(b)
  if (Number.isFinite(na) && Number.isFinite(nb)) return na === nb
  return String(a) === String(b)
}

/**
 * evaluateAskWhen — should the node be PRESENTED? Pure, deterministic, no I/O.
 * No guard ⇒ true (present). Referenced answer absent/empty ⇒ false (skip —
 * ADR § Decisões em aberto #1). Single source of truth for the engine; the web
 * vehicle mirrors these semantics in JS.
 */
export function evaluateAskWhen(
  guard: AskWhen | undefined,
  answers: Record<string, unknown>,
): boolean {
  if (!guard) return true
  const a = answers[guard.field]
  if (a === undefined || a === null || a === "") return false
  const { op, value } = guard
  switch (op) {
    case "lt":  return _num(a) <  _num(value)
    case "lte": return _num(a) <= _num(value)
    case "gt":  return _num(a) >  _num(value)
    case "gte": return _num(a) >= _num(value)
    case "eq":  return _eq(a, value)
    case "ne":  return !_eq(a, value)
    case "in":  return Array.isArray(value) && value.some(v => _eq(a, v))
    default:    return false
  }
}

/**
 * askWhenForwardRefErrors — an `ask_when.field` MUST reference a question that
 * appears EARLIER in the linear node order (a prior answer). Returns the
 * offending { node_id, field } pairs (forward or unknown reference). Used by the
 * editor (client-side) and deploy/sync validation.
 */
export function askWhenForwardRefErrors(
  form: Pick<DialogForm, "nodes">,
): Array<{ node_id: string; field: string }> {
  const errors: Array<{ node_id: string; field: string }> = []
  const seen = new Set<string>()
  for (const node of form.nodes ?? []) {
    if (node.ask_when && !seen.has(node.ask_when.field)) {
      errors.push({ node_id: node.id, field: node.ask_when.field })
    }
    if (node.kind === "question") seen.add(node.output_key)
  }
  return errors
}
