/**
 * tools/dialog.ts
 * Generic dialog-form MCP tool — thin wrapper over dialog-api so that the
 * Tier-3 dialog-runner (and any scripted-dialog consumer) can load a versioned
 * DialogForm via an `invoke` step. Replaces the survey-branded `survey_form_get`.
 *
 * Tools:
 *   form_get — GET /v1/dialog/forms/:id?status=published (default) → DialogForm JSON
 *   dialog_tree_level — projeta UM nível da árvore de opções de uma pergunta,
 *     para navegação turno-a-turno com o cliente (ADR adr-orchestrator-tree-navigation)
 */

import { z }                     from "zod"
import type { McpServer }        from "@modelcontextprotocol/sdk/server/mcp.js"
import { buildRender, duplicateNodeIds, optionsAtPath, leafPaths,
         entryQuestionId, categoryPathFor } from "@plughub/schemas"
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

const TreeLevelInputSchema = z.object({
  form_id:    z.string().min(1).describe("Dialog form id to resolve"),
  output_key: z.string().min(1).optional()
                .describe("Which question of the form owns the options tree (its output_key)"),
  // RET-02: `on_return` (D2 do ADR do retorno) aponta para o `id` do NODE, nao
  // para o `output_key` — que nomeia a RESPOSTA, nao a pergunta. Sem endereçar
  // por id, o ponteiro que `returnRefErrors` valida seria inenderecavel pela
  // propria tool que o consome: a continuacao existiria e ninguem a renderizaria.
  question_id: z.string().min(1).optional()
                .describe(
                  "Which question to project, by NODE id. Alternative to `output_key` — this " +
                  "is what `on_return` names. Exactly one of the two must be given.",
                ),
  // O CURSOR e do chamador, nunca desta tool: ela e pura sobre (forma, caminho).
  // Guardar cursor aqui criaria estado de navegacao fora do `pipeline_state`, que e
  // quem sobrevive a queda (ver D11 do ADR).
  path:       z.array(z.string()).default([])
                .describe("Cursor: ids already chosen, outermost first. Empty = root level"),
  // O APPEND mora aqui porque o fluxo nao tem primitiva para isso: `choice` compara,
  // `menu` coleta, e nenhum step concatena array. Sem este campo cada skill
  // reinventaria a concatenacao — ou pior, guardaria o caminho como string e
  // fabricaria o separador, que e o mesmo ponto onde a categoria do Arc 12 nasce.
  chosen_id:  z.string().optional()
                .describe(
                  "What the customer just picked, appended to `path` before projecting. " +
                  "May be a DOTTED PATH (`sac.info_plano`) when the channel drew the whole " +
                  "tree and the customer reached a leaf in one turn — each segment is " +
                  "appended in order, so the flow is turn-agnostic.",
                ),
  status:     z.enum(["draft", "published"]).default("published"),
  version:    z.number().int().positive().optional(),
  locale:     z.string().optional(),
  tenant_id:  z.string().optional(),
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

  // ── dialog_tree_level ──────────────────────────────────────────────────────
  //
  // Existe porque navegar arvore COM O CLIENTE e turno-a-turno: o `menu` step mostra
  // UMA lista plana por vez, e o `render` traz a arvore inteira aninhada. Sem esta
  // projecao, apontar a ref do menu para o render entregaria so o nivel de cima e os
  // filhos ficariam invisiveis — o mesmo achatamento mudo do adapter de WhatsApp.
  //
  // ⚠️ Tool PROPRIA, e nao um parametro do `form_get`: aquele responde *qual e o
  // conteudo publicado* e tem tres consumidores (runner, survey web, preview do
  // editor). Alargar a forma do `render.options` para servir um deles seria containers
  // largos para fato estreito — a mesma regra que o ADR aplica noutro eixo.
  server.tool(
    "dialog_tree_level",
    "Project ONE level of a question's option tree, for turn-by-turn navigation with the customer. " +
    "Takes the cursor (`path`: ids already chosen) and returns the options to offer NEXT, plus " +
    "`is_leaf` telling whether the cursor already names a selectable leaf (derived from the absence " +
    "of children, never declared). Pure over (form, path): the cursor belongs to the caller's " +
    "pipeline_state, which is what survives a crash. `found: false` means the path no longer " +
    "resolves — a legitimate outcome when the form changed mid-contact — and is DATA, not an error, " +
    "so the flow can restart navigation instead of failing.",
    TreeLevelInputSchema.shape as any,
    async (rawInput: Record<string, unknown>) => {
      let input: z.infer<typeof TreeLevelInputSchema>
      try {
        input = TreeLevelInputSchema.parse(rawInput)
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

        // Pergunta INEXISTENTE recusa alto. Cair na primeira pergunta da forma seria
        // navegar uma arvore que o chamador nao pediu — e a tela pareceria certa.
        // Exatamente UM dos dois enderecos. Aceitar nenhum cairia na primeira
        // pergunta (navegar arvore que o chamador nao pediu); aceitar os dois
        // exigiria uma precedencia, e precedencia entre dois enderecos e o
        // comeco da pergunta *"qual deles respondeu?"*.
        if ((input.output_key ? 1 : 0) + (input.question_id ? 1 : 0) !== 1) {
          return mcpError(
            "address_ambiguous",
            "informe EXATAMENTE um de `output_key` ou `question_id` — " +
            `recebi output_key=${JSON.stringify(input.output_key)} question_id=${JSON.stringify(input.question_id)}`,
          )
        }
        const q = input.question_id
          ? render.questions.find(x => x.id === input.question_id)
          : render.questions.find(x => x.output_key === input.output_key)
        if (!q) {
          return mcpError(
            "question_not_found",
            input.question_id
              ? `A forma '${form.form_id}' nao tem pergunta com id '${input.question_id}'. ` +
                `Disponiveis: [${render.questions.map(x => x.id).join(", ")}]`
              : `A forma '${form.form_id}' nao tem pergunta com output_key '${input.output_key}'. ` +
                `Disponiveis: [${render.questions.map(x => x.output_key).join(", ")}]`,
          )
        }

        // ⚠️ SPLIT, nao append cru (F2). Um canal que desenhou a arvore agrupada
        // responde com o CAMINHO (`sac.info_plano`), nao com um id de um nivel; sem
        // dividir, a projecao procuraria uma opcao chamada literalmente
        // "sac.info_plano", nao acharia, e devolveria `found: false` — a navegacao
        // reiniciaria e a tela pareceria certa. O ponto e separador seguro porque
        // `DialogOptionSchema` proibe ponto no id (medido: 0 de 87).
        const escolhidos = input.chosen_id
          ? input.chosen_id.split(".").filter(x => x !== "")
          : []
        const trilha = [...input.path, ...escolhidos]
        const nivel = optionsAtPath(q.options, trilha)
        // Rotulo do no do cursor — para a superficie compor o prompt do nivel de baixo
        // sem repetir a pergunta da raiz. Usa a MESMA projecao, um passo acima.
        const pai   = optionsAtPath(q.options, trilha.slice(0, -1))
        const ultimo = trilha[trilha.length - 1]
        const nodeLabel = ultimo ? pai.options.find(o => o.id === ultimo)?.label : undefined

        return ok({
          form_id:       form.form_id,
          version:       form.version,
          output_key:    q.output_key,
          prompt:        q.prompt,
          node_label:    nodeLabel,
          found:         nivel.found,
          is_leaf:       nivel.is_leaf,
          question_id:   q.id,
          // RET-02 — ponteiro de continuacao do NO DO CURSOR. Ausente ⇒ a folha
          // encerra o fluxo do chamador (o comportamento de sempre). Viaja aqui
          // e nao no `options` porque quem continua e a folha ESCOLHIDA, e o
          // chamador precisa dele DEPOIS da escolha, ja com o cursor sobre ela.
          //
          // ⚠️ A CHAVE so existe quando ha ponteiro, e isso nao e estilo: o
          // operador `exists` do `choice` olha a CHAVE, nao o valor, entao um
          // `on_return: null` explicito faria TODO caminho parecer ter
          // continuacao — o valor plausivel mais barato de produzir aqui.
          ...(nivel.on_return ? { on_return: nivel.on_return } : {}),
          // Vocabulario de desfechos SOB o cursor (D6). Em `path: []` e a arvore
          // inteira — o que o orquestrador com LLM manda ao prompt E confere na
          // volta. As duas metades: mandar sem conferir e promessa sem mecanismo.
          leaves:        leafPaths(nivel.options).map(c => [...trilha, c].join(".")),
          options:       nivel.options,
          path:          nivel.path,
          // Cauda da `category` do Arc 12 — o chamador nao precisa juntar, e assim
          // ha UMA forma de compor o caminho, nao uma por skill.
          //
          // ⚠️ **MEDICAO, nao endereco.** `path` e `leaves` acima voltam para dentro
          // desta tool (`input.path` / `chosen_id`) e por isso NAO levam o prefixo;
          // `category_path` e `root_id` levam, porque a D5 faz da continuacao uma
          // RAIZ PROPRIA (`pos_atendimento.especialista`). Ate 2026-09-07 esta linha
          // era `nivel.path.join(".")` para toda question, e a continuacao media
          // `outra_coisa` enquanto o fluxo comparava `pos_atendimento.outra_coisa` —
          // duas casas compondo o mesmo caminho, e so uma era o runtime.
          category_path: categoryPathFor(entryQuestionId(form), q.id, nivel.path),
          // Primeiro segmento em campo PROPRIO: e por ele que o roteamento decide, e
          // depender de indice em JSONPath no `choice` poria a composicao do caminho
          // em duas casas.
          root_id:       categoryPathFor(entryQuestionId(form), q.id, nivel.path).split(".")[0] || null,
        })
      } catch (err) {
        return mcpError("network_error", err instanceof Error ? err.message : String(err))
      }
    },
  )
}
