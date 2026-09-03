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
  id: string; label: string; type: string; required: boolean; masked: boolean | string
  // Approval (ADR adr-human-approval-workflow-step): pre-filled editable value +
  // per-field options (select). Absent for plain capture-only survey fields.
  value?:   string | number | boolean
  options?: RenderOption[]
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
  // NIV-04 fatia A — projeção POR NÓ: `node_id → texto resolvido`. Statements dão
  // o próprio texto; questions dão o `prompt`.
  //
  // POR QUE ELA EXISTE. O `render` acima é single-turn: statements só existem como
  // satélites de uma pergunta (`menu_prompt` os junta com `\n\n`). Isso serve o
  // dialog-runner, e **não serve** o roteiro de um fluxo de agente — cujos avisos
  // estão espalhados por ramos diferentes (saudação, transferência, encerramento).
  // Sem endereçamento por nó, migrar roteiro para `DialogForm` exigiria uma forma
  // por aviso: medido em 2026-09-03, **79 pontos estáticos em 24 skills** virariam
  // ~79 formas e ~79 `invoke` novos, quase dobrando a contagem de steps.
  //
  // Com `by_node`, o fluxo carrega **uma** forma (o seu roteiro) num `invoke` só e
  // cada `notify` referencia o seu nó.
  //
  // ⚠️ **O texto NÃO é re-interpolado.** O `interpolate` do engine é de PASSE ÚNICO:
  // ele coleta os `{{…}}` do template ORIGINAL, resolve e substitui — um valor
  // inserido que contenha `{{…}}` chega ao cliente com as chaves literais. Logo nó
  // com texto dinâmico ainda NÃO migra; são 20 pontos, contados, e a decisão sobre
  // uma segunda passada tem vetor próprio (quem edita conteúdo passaria a poder
  // injetar referências ao `pipeline_state`).
  by_node:         Record<string, string>
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
  const byNode: Record<string, string> = {}
  let seenQuestion = false
  let firstQuestion: QuestionNode | null = null

  for (const node of form.nodes) {
    if (node.kind === "statement") {
      const txt = resolveLocalizedText(node.text, locale, dl)
      byNode[node.id] = txt
      if (txt) (seenQuestion ? after : before).push(txt)
    } else {
      byNode[node.id] = resolveLocalizedText(node.prompt, locale, dl)
      seenQuestion = true
      if (!firstQuestion) firstQuestion = node
      // Multi-field form (interaction: "form", approval "form padrão"): emit each
      // declared field with its own type/value/options. Otherwise the question is a
      // single scalar answer → one field keyed by output_key (survey/OTP behavior).
      if (node.fields && node.fields.length) {
        for (const f of node.fields) {
          const rf: RenderField = {
            id:       f.id,
            label:    resolveLocalizedText(f.label, locale, dl),
            type:     f.type,
            required: f.required ?? false,
            // Verbatim: com a união (T2), `=== true` faria `masked: "cpf"` virar
            // `false` e o campo sair DESMASCARADO — fail-open silencioso.
            masked:   f.masked ?? false,
          }
          if (f.value !== undefined) rf.value = f.value
          if (f.options && f.options.length) {
            rf.options = f.options.map(o => ({
              id:    o.value ?? o.id,
              label: resolveLocalizedText(o.label, locale, dl),
            }))
          }
          fields.push(rf)
        }
      } else {
        fields.push({
          id:       node.output_key,
          label:    resolveLocalizedText(node.prompt, locale, dl),
          type:     node.interaction === "text" ? "text" : "choice",
          required: true,
          masked:   node.masked ?? false,   // verbatim — ver acima
        })
      }
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
    by_node: byNode,
  }
}

/**
 * Ids de nó repetidos dentro de uma forma.
 *
 * Um mapa `node_id → texto` é AMBÍGUO quando dois nós compartilham o id: o segundo
 * sobrescreve o primeiro e tudo que só existia no primeiro **deixa de existir, sem
 * erro**. É o mesmo defeito que a chave duplicada num arquivo de locale produziu
 * (o parser fica com a última, e a tela mostra a chave crua) — e aqui seria pior,
 * porque a referência não resolvida vira string VAZIA e o cliente recebe um aviso
 * em branco, que é um valor plausível.
 *
 * Medido em 2026-09-03: **zero** duplicatas nas 10 formas semeadas. Fechar a classe
 * agora custa nada e remove a possibilidade, em vez de exigir vigilância.
 */
function duplicateNodeIds(form: DialogForm): string[] {
  const vistos = new Set<string>()
  const dup    = new Set<string>()
  for (const n of form.nodes) {
    if (vistos.has(n.id)) dup.add(n.id)
    vistos.add(n.id)
  }
  return [...dup].sort()
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
    "(one per question), statement_after, and a domain-blind capture echo. Content only; no control flow. " +
    "`render.by_node` maps node_id -> resolved text (statements: their text; questions: their prompt) so a " +
    "flow can address ONE line of the script instead of the whole turn — the text is NOT re-interpolated, " +
    "so nodes carrying {{...}} refs are not migratable yet (NIV-04 fatia C).",
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
        // Ambiguidade no mapa `by_node` NUNCA sai calada: o segundo nó sobrescreveria
        // o primeiro, e uma referência ao id perdido resolveria para string VAZIA —
        // o cliente receberia um aviso em branco, que é um valor plausível. Recusa
        // alto, aqui, onde a causa está visível.
        const dup = duplicateNodeIds(form)
        if (dup.length) {
          return mcpError(
            "duplicate_node_id",
            `A forma '${form.form_id}' repete o(s) id(s) de nó [${dup.join(", ")}]. ` +
            `O mapa render.by_node ficaria ambíguo e a referência ao nó perdido viraria ` +
            `texto vazio, sem erro. Renomeie os nós.`,
          )
        }
        const render = buildRender(form, input.locale)
        return ok({ form_id: form.form_id, version: form.version, status: form.status, render, form })
      } catch (err) {
        return mcpError("network_error", err instanceof Error ? err.message : String(err))
      }
    },
  )
}
