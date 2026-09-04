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
import { buildRender, duplicateNodeIds } from "@plughub/schemas"
import type { DialogForm }               from "@plughub/schemas"

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
//
// MUDOU DE CASA em 2026-09-04: `buildRender`/`duplicateNodeIds` vivem em
// `@plughub/schemas` (`dialog-render.ts`). Motivo: o editor JSON do platform-ui
// precisa da MESMA normalização para o preview, e uma cópia no browser seria a
// segunda implementação da mesma regra. Aqui ficou só o consumo.

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
