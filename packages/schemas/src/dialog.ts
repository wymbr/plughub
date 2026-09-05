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
import { MaskedDeclarationSchema } from "./audit"
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
    /**
     * Nome de uma entrada do catálogo de formatos (`dialog-format.ts` /
     * config-api `dialog.formats`) — a declaração PRIMÁRIA desde o ADR
     * `adr-dialog-input-format-catalog`.
     *
     * O nome decide AFORDÂNCIA (máscara de digitação, `inputmode`, `maxlength`)
     * e VEREDICTO em dois níveis (`shape` ancorada + `semantic`). Os campos
     * abaixo continuam válidos e são aplicados POR CIMA — um `max_length` na
     * pergunta aperta o do formato, nunca o afrouxa.
     *
     * ⚠️ **`pattern` NÃO existe mais** (F3, 2026-09-04). Regex crua não guia
     * digitação, não produz mensagem localizada e não alcança validade
     * semântica — `31/02/2026` casa qualquer regex de data. Removida com censo
     * ZERO (0 de 12 formas publicadas, 0 semeadas, 0 YAMLs) e apagando por
     * construção um fail-open: regex inválida caía num `catch` e liberava tudo.
     * A regex não saiu do sistema, mudou de AUTOR — hoje é a implementação de
     * uma entrada de catálogo, revisada e testada uma vez.
     */
    format:     z.string().optional(),
    numeric:    z.boolean().optional(),
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

/**
 * Comparison operators for an `ask_when` guard.
 *
 * `prefix` (D12 do `adr-dialog-tree-options`) existe porque a skip-logic natural
 * sobre TAXONOMIA é *"se o motivo está em qualquer lugar sob Financeiro"* — sem
 * ele seria preciso listar todas as folhas do ramo num `in`, e a lista
 * envelheceria a cada folha nova, em silêncio.
 */
export const AskWhenOpSchema = z.enum(["lt", "lte", "gt", "gte", "eq", "ne", "in", "prefix"])
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
    /**
     * Wrap-up / Arc 12 (fatia 3) — como a resposta entra em `agent_business_events`.
     * Só é lido quando quem responde é o AGENTE (`segment_outcome_record`); no
     * domínio de survey, onde quem responde é o CLIENTE, este campo é ignorado.
     *
     * NÃO escolhe o sink. O sink é dado por QUEM RESPONDE (ADR §D1), e isso já está
     * determinado pela TOOL que compõe: `segment_outcome_record` ⇒ Arc 12,
     * `survey_record` ⇒ `session_signal`. Um campo aqui para escolher sink permitiria
     * declarar, num form de wrap-up, que a resposta do atendente é voz do cliente —
     * e contaminaria a série histórica de forma irreversível.
     *
     * O que ele escolhe é a FORMA do evento, porque `agent_business_events` tem
     * `value Float64` e o relatório **não agrupa por tag** (só por
     * category/skill_id/pool_id/agent_type_id) — logo o que não for numérico precisa
     * virar CATEGORIA para ser contável (§D2):
     *
     *   scored  — categoria fixa `{pool}.{skill}.{metric}`, `value` = a resposta
     *             numérica. `avg_value` do summary É a taxa (ex.: FCR).
     *   nominal — a resposta VIRA a folha: `{pool}.{skill}.{metric}.{option.value}`,
     *             `value: 1`. `count` por categoria. Multi-select ⇒ N eventos.
     *             A folha sai de `options[].value` (lista controlada, versionada e
     *             UI-editável) e nunca de texto livre — §D3: sem isso
     *             `troca_titularidade` × `troca_de_titularidade` viram duas séries
     *             que jamais reconciliam.
     *
     * Ausente num form de wrap-up = a resposta NÃO vai para o Arc 12; texto livre
     * sem capture vira prosa nas colunas do segmento (`wrapup_summary` /
     * `wrapup_next_steps`). É assim que a §D6 (os dois sinks coexistem) fica
     * decidida por construção, e não por convenção que alguém precise lembrar.
     */
    kind: z.enum(["scored", "nominal"]).optional(),
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

/**
 * DialogOption — uma opção, e possivelmente uma SUBÁRVORE de opções.
 *
 * A recursão entra AQUI, nunca em `DialogNode` (D1 do `adr-dialog-tree-options`):
 * uma taxonomia (`Financeiro > Cobrança > indevida`) é **domínio de valor**, não
 * control-flow — é UMA resposta cujo valor é hierárquico, e não decide o que vem
 * depois. Com isso `nodes` continua plano e as seis superfícies que o percorrem
 * mantêm o laço linear.
 *
 * **Pasta × folha é DERIVADO**, nunca declarado: selecionável ⟺ sem `options`.
 * Não há flag a marcar nem a esquecer. `value` é ignorado numa pasta.
 *
 * ⚠️ `id` é IMUTÁVEL (D6) — ele compõe a categoria do Arc 12, e a série histórica
 * é append-only. `label` pode ser reescrito e traduzido à vontade; mudou de
 * conceito, é folha NOVA, e a antiga sai da oferta com `active: false`.
 */
export interface DialogOption {
  id:       string
  label:    LocalizedText
  /** Machine value returned to the runner; defaults to `id` when absent. */
  value?:   string
  capture?: DialogCapture
  /** Presente ⇒ é PASTA (não selecionável). Ausente/vazio ⇒ folha. */
  options?: DialogOption[]
  /** `false` = APOSENTADA: sai da oferta e permanece no form (D6) — o dado
   *  histórico continua explicável. Ausente = ativa. */
  active?:  boolean
}

/**
 * Recursão em Zod exige `z.lazy` COM anotação explícita — `z.infer` não deduz o
 * tipo sozinho, e por isso `DialogOption` acima é declarado à mão.
 *
 * ⚠️ As REGRAS da árvore (profundidade, `id` único entre irmãos, aninhamento só
 * sob `list`/`checklist`) **não** moram num `superRefine` daqui: `superRefine`
 * devolve `ZodEffects`, que não pode entrar num `discriminatedUnion` (o
 * `DialogNodeSchema`) nem aceitar `.omit()` (o `DialogFormDraftSchema`). Elas
 * vivem em `optionTreeIssues`, chamada pelo validador canônico — a mesma casa de
 * `duplicateNodeIds`.
 */
export const DialogOptionSchema: z.ZodType<DialogOption> = z.lazy(() =>
  z.object({
    id:      z.string().min(1),
    label:   LocalizedTextSchema,
    value:   z.string().optional(),
    capture: DialogCaptureSchema,
    options: z.array(DialogOptionSchema).optional(),
    active:  z.boolean().optional(),
  }),
)

export const DialogFieldSchema = z.object({
  id:         z.string().min(1),
  label:      LocalizedTextSchema,
  /**
   * Channel-agnostic field type. Deliberately an OPEN string ("adapter maps to
   * UI") — recognized by the renderers: "text" | "number" | "money" | "date" |
   * "bool" | "select". Approval (ADR adr-human-approval-workflow-step) uses the
   * richer types; the survey/OTP runners degrade unknown types to text.
   */
  type:       z.string().min(1),
  required:   z.boolean().default(false),
  /**
   * Pre-filled value the human may EDIT (approval form_ext). A bare scalar as a
   * string (the adapter parses per `type` — e.g. "1240.00" for money, "true" for
   * bool). Absent = empty/capture-only field (survey questions). The edited value
   * travels back in the delegate return `payload.edits` (audited, ADR §D7).
   */
  value:      z.union([z.string(), z.number(), z.boolean()]).optional(),
  /** Option list for a "select"/"checklist" FIELD (per-field, distinct from the
   *  question-level `options`). Absent for scalar field types. */
  options:    z.array(DialogOptionSchema).optional(),
  /**
   * When true this field is masked: its value never appears in the runner's raw
   * return (masked-input invariant) — it stays in the in-memory masked scope.
   */
  masked:     MaskedDeclarationSchema.optional(),
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
  masked:      MaskedDeclarationSchema.optional(),
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
  // Ausência é "não respondeu" ⇒ não apresenta. Lista VAZIA entra aqui pela
  // mesma razão que `""`: um `checklist` sem marcação nenhuma não é resposta.
  if (a === undefined || a === null || a === "") return false
  if (Array.isArray(a) && a.length === 0) return false

  const { op, value } = guard
  const multi = Array.isArray(a)
  const vals: unknown[] = multi ? (a as unknown[]) : [a]

  switch (op) {
    // ORDENAÇÃO sobre multi-resposta é INDEFINIDA (D12) ⇒ guarda falsa. Escolher
    // "o menor" ou "o maior" seria inventar uma ordem que o autor não declarou.
    case "lt":  return multi ? false : _num(a) <  _num(value)
    case "lte": return multi ? false : _num(a) <= _num(value)
    case "gt":  return multi ? false : _num(a) >  _num(value)
    case "gte": return multi ? false : _num(a) >= _num(value)
    // Igualdade sobre multi = "ALGUM casa"; e `ne` é a NEGAÇÃO de `eq`, nunca
    // "algum difere" — senão uma marcação com X e Y satisfaria `eq X` e `ne X`
    // ao mesmo tempo, e "pergunte a menos que tenham escolhido X" mentiria.
    case "eq":  return vals.some(v => _eq(v, value))
    case "ne":  return !vals.some(v => _eq(v, value))
    case "in":  return Array.isArray(value) && vals.some(v => value.some(x => _eq(v, x)))
    // `prefix` casa por SEGMENTO, não por string: `startsWith` cru faria a guarda
    // de `financeiro` casar `financeiro_avulso`, que não é filho do ramo — a
    // pergunta errada apareceria sem nada ficar vermelho.
    case "prefix": return vals.some(v => _sobPrefixo(String(v), String(value)))
    default:    return false
  }
}

/** `caminho` é o próprio prefixo ou desce dele por um separador de segmento. */
function _sobPrefixo(caminho: string, prefixo: string): boolean {
  return caminho === prefixo || caminho.startsWith(prefixo + ".")
}

/**
 * askWhenForwardRefErrors — an `ask_when.field` MUST reference a question that
 * appears EARLIER in the linear node order (a prior answer). Returns the
 * offending { node_id, field } pairs (forward or unknown reference). Used by the
 * editor (client-side) and deploy/sync validation.
 */
/** Profundidade máxima da árvore de opções (D3). Acima disso é erro de AUTORIA,
 *  nunca truncamento — truncar perderia subárvore inteira em silêncio. */
export const DIALOG_OPTION_MAX_DEPTH = 5

export interface OptionTreeIssue {
  path:    string
  code:
    | "option_duplicate_sibling_id"
    | "option_nesting_not_allowed"
    | "option_depth"
    | "option_empty_folder"
  message: string
}

/**
 * optionTreeIssues — as regras da árvore de opções, puras e sem I/O.
 *
 * Mora aqui, e não num `superRefine`, porque `superRefine` devolve `ZodEffects`:
 * `DialogNodeSchema` é `discriminatedUnion` (não aceita efeitos) e
 * `DialogFormDraftSchema` faz `.omit()` (idem). Chamada pelo validador canônico
 * (`validateDialogForm`) e disponível ao editor, que não importa Zod.
 *
 * `allowNesting` é do CHAMADOR porque a permissão é da PERGUNTA, não da opção:
 * só `list`/`checklist` renderizam árvore (D4). Sob `button`/`form` o aninhamento
 * é erro de schema, nunca render parcial — um renderizador que desenhasse só o
 * primeiro nível perderia subárvores sem reclamar.
 */
export function optionTreeIssues(
  options: DialogOption[] | undefined,
  opts: { allowNesting: boolean; base?: string },
): OptionTreeIssue[] {
  const issues: OptionTreeIssue[] = []
  const walk = (list: DialogOption[], path: string, depth: number): void => {
    const vistos = new Set<string>()
    list.forEach((opt, i) => {
      const p = `${path}.${i}`
      if (vistos.has(opt.id)) {
        issues.push({
          path: `${p}.id`,
          code: "option_duplicate_sibling_id",
          message: `'${opt.id}' repetido entre irmãos — o caminho deixa de identificar a folha, e duas séries do Arc 12 se fundem`,
        })
      }
      vistos.add(opt.id)

      const filhos = opt.options
      if (filhos === undefined) return
      if (filhos.length === 0) {
        issues.push({
          path: `${p}.options`,
          code: "option_empty_folder",
          message: `'${opt.id}' declara 'options' vazio — pasta sem filho lê-se de dois jeitos; remova a chave para ser folha, ou dê filhos a ela`,
        })
        return
      }
      if (!opts.allowNesting) {
        issues.push({
          path: `${p}.options`,
          code: "option_nesting_not_allowed",
          message: `'${opt.id}' tem subopções, e aninhamento só existe sob 'list'/'checklist' — sob os demais o render perderia a subárvore inteira, sem erro`,
        })
        return
      }
      if (depth + 1 > DIALOG_OPTION_MAX_DEPTH) {
        issues.push({
          path: `${p}.options`,
          code: "option_depth",
          message: `profundidade acima de ${DIALOG_OPTION_MAX_DEPTH} sob '${opt.id}' — é erro de autoria, e truncar perderia a subárvore em silêncio`,
        })
        return
      }
      walk(filhos, `${p}.options`, depth + 1)
    })
  }
  walk(options ?? [], opts.base ?? "options", 1)
  return issues
}

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
