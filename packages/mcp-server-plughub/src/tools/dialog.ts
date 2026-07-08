/**
 * tools/dialog.ts
 * Generic dialog-form MCP tool — thin wrapper over dialog-api so that the
 * Tier-3 dialog-runner (and any scripted-dialog consumer) can load a versioned
 * DialogForm via an `invoke` step. Replaces the survey-branded `survey_form_get`.
 *
 * Tools:
 *   form_get — GET /v1/dialog/forms/:id?status=published (default) → DialogForm JSON
 */

import { z }                     from "zod"
import type { McpServer }        from "@modelcontextprotocol/sdk/server/mcp.js"
import { resolveLocalizedText }        from "@plughub/schemas"
import type { DialogForm, QuestionNode } from "@plughub/schemas"

// ─── Dependências injetadas ───────────────────────────────────────────────────

export interface DialogDeps {
  dialogApiUrl: string   // e.g. http://localhost:3760
  tenantId:     string   // default tenant (overridden by input when provided)
}

// ─── Schema ───────────────────────────────────────────────────────────────────

const FormGetInputSchema = z.object({
  form_id: z.string().min(1).describe("Dialog form id to resolve"),
  status:  z.enum(["draft", "published"]).default("published")
             .describe("Which lifecycle status to resolve; defaults to the published (current) version"),
  version: z.number().int().positive().optional()
             .describe("Pin an exact version; overrides status when set"),
  locale:  z.string().optional().describe("Locale to resolve i18n text to; defaults to the form's default_locale"),
  tenant_id: z.string().optional().describe("Tenant ID; defaults to server-configured tenant"),
})

// ─── Render normalization (§18.4) ─────────────────────────────────────────────
// Flatten a DialogForm into a single-turn render block the dialog-runner menu
// consumes directly: leading statements → menu prompt, questions → form fields,
// trailing statements → statement_after, plus a domain-blind capture echo.
// Content-shaping only (no control flow).

interface RenderField {
  id: string; label: string; type: string; required: boolean; masked: boolean
}
interface RenderOption { id: string; label: string }
// Retry affordance flattened for the menu step: reprompt localized, counter fixed.
interface RenderRetry { reprompt: string; max_attempts: number }
interface RenderQuestion {
  prompt:      string
  interaction: string
  options:     RenderOption[]
  output_key:  string
  capture:     unknown
  visibility:  unknown
  validation:  unknown               // format-only validation (numeric/pattern/…) or undefined
  retry:       RenderRetry | undefined  // reprompt (localized) + max_attempts, or undefined
  ask_when:    unknown               // declarative skip-logic guard { field, op, value } or undefined
}
interface DialogRender {
  // §17.4 — single-question NATIVE view (the v1 render the runner uses):
  interaction: string                 // the question's native interaction (text|button|list|...)
  prompt:      string                 // leading statements + the question prompt, localized
  options:     RenderOption[]         // the question's options (localized labels), for button/list
  output_key:  string                 // where the raw answer keys (domain reads payload.value)
  visibility:  unknown                // the question's visibility (enum|array with @ctx refs) or "all"
  validation:  unknown                // the question's format validation, or undefined
  retry:       RenderRetry | undefined // the question's retry (reprompt localized + max_attempts)
  timeout_s:   number                 // §21 — the question's timeout (s); menu step reads via ref
  // Fatia 2 loop view: one entry per question (walked sequentially by a `loop` step).
  questions:       RenderQuestion[]
  // Legacy/multi-field view (interaction=form): one field per question.
  menu_prompt:     string
  fields:          RenderField[]
  statement_after: string
  captures:        Record<string, unknown>
}

// Flatten a question's retry (LocalizedText reprompt → string) for the menu step.
function flattenRetry(q: QuestionNode, locale: string | undefined, dl: string): RenderRetry | undefined {
  if (!q.retry) return undefined
  return {
    reprompt:     resolveLocalizedText(q.retry.reprompt, locale, dl),
    max_attempts: q.retry.max_attempts,
  }
}

function buildRender(form: DialogForm, locale?: string): DialogRender {
  const dl = form.default_locale
  const before: string[] = []
  const after:  string[] = []
  const fields: RenderField[] = []
  const questions: RenderQuestion[] = []
  const captures: Record<string, unknown> = {}
  let seenQuestion = false
  let firstQuestion: QuestionNode | null = null

  for (const node of form.nodes) {
    if (node.kind === "statement") {
      const txt = resolveLocalizedText(node.text, locale, dl)
      if (txt) (seenQuestion ? after : before).push(txt)
    } else {
      seenQuestion = true
      if (!firstQuestion) firstQuestion = node
      fields.push({
        id:       node.output_key,
        label:    resolveLocalizedText(node.prompt, locale, dl),
        type:     node.interaction === "text" ? "text" : "choice",
        required: true,
        masked:   node.masked === true,
      })
      questions.push({
        prompt:      resolveLocalizedText(node.prompt, locale, dl),
        interaction: node.interaction,
        options:     (node.options ?? []).map(o => ({
          id:    o.value ?? o.id,
          label: resolveLocalizedText(o.label, locale, dl),
        })),
        output_key:  node.output_key,
        capture:     node.capture ?? {},
        visibility:  node.visibility ?? "all",
        validation:  node.validation,
        retry:       flattenRetry(node, locale, dl),
        ask_when:    node.ask_when,
      })
      captures[node.output_key] = node.capture ?? {}
    }
  }

  const q       = firstQuestion
  const qPrompt = q ? resolveLocalizedText(q.prompt, locale, dl) : ""
  // Fold leading statements into the single-question prompt (§17.4 native render).
  const prompt  = before.length ? `${before.join("\n\n")}\n\n${qPrompt}` : qPrompt
  const options: RenderOption[] = (q?.options ?? []).map(o => ({
    id:    o.value ?? o.id,
    label: resolveLocalizedText(o.label, locale, dl),
  }))

  return {
    interaction: q?.interaction ?? "text",
    prompt,
    options,
    output_key:  q?.output_key ?? "value",
    visibility:  q?.visibility ?? "all",
    validation:  q?.validation,
    retry:       q ? flattenRetry(q, locale, dl) : undefined,
    // §21 — the question's timeout (form JSON is raw-cast, not Zod-parsed, so the
    // schema default isn't applied here → fall back to 300, matching the schema).
    timeout_s:   (q && typeof q.timeout_s === "number") ? q.timeout_s : 300,
    questions,
    menu_prompt: before.join("\n\n") || qPrompt,
    fields,
    statement_after: after.join("\n\n"),
    captures,
  }
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

type ToolResult = {
  isError?: true
  content: Array<{ type: "text"; text: string }>
}

function ok(data: unknown): ToolResult {
  return { content: [{ type: "text" as const, text: JSON.stringify(data) }] }
}

function mcpError(code: string, message: string): ToolResult {
  return {
    isError: true,
    content: [{ type: "text" as const, text: JSON.stringify({ error: code, message }) }],
  }
}

// ─── Registration ─────────────────────────────────────────────────────────────

export function registerDialogTools(server: McpServer, deps: DialogDeps): void {
  const { dialogApiUrl, tenantId: defaultTenantId } = deps

  server.tool(
    "form_get",
    "Load a versioned DialogForm (scripted-dialog content: statements + questions, i18n) from the " +
    "dialog-api. Defaults to the published (current) version. Returns { form, render } where `render` " +
    "is a single-turn normalization for the dialog-runner: menu_prompt (leading statements), fields " +
    "(one per question), statement_after, and a domain-blind capture echo. Content only; no control flow.",
    FormGetInputSchema.shape as any,
    async (rawInput: Record<string, unknown>) => {
      let input: z.infer<typeof FormGetInputSchema>
      try {
        input = FormGetInputSchema.parse(rawInput)
      } catch (e) {
        if (e instanceof z.ZodError) {
          return mcpError("validation_error", e.errors.map(x => `${x.path.join(".")}: ${x.message}`).join("; "))
        }
        throw e
      }

      const tenantId = input.tenant_id ?? defaultTenantId
      const params = new URLSearchParams({ status: input.status })
      if (input.version !== undefined) params.set("version", String(input.version))

      try {
        const resp = await fetch(
          `${dialogApiUrl}/v1/dialog/forms/${encodeURIComponent(input.form_id)}?${params}`,
          { headers: { "X-Tenant-ID": tenantId } },
        )
        if (!resp.ok) {
          const body = await resp.text().catch(() => "")
          return mcpError("dialog_api_error", `Dialog API returned ${resp.status}: ${body}`)
        }
        const form = (await resp.json()) as DialogForm
        const render = buildRender(form, input.locale)
        return ok({ form_id: form.form_id, version: form.version, status: form.status, render, form })
      } catch (err) {
        return mcpError("network_error", err instanceof Error ? err.message : String(err))
      }
    },
  )
}
