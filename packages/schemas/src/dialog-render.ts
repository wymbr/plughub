/**
 * dialog-render.ts
 * Normalização de uma `DialogForm` no bloco `render` que o runner consome —
 * e o VEREDICTO estrutural que o editor pergunta antes de salvar.
 *
 * ── Por que mora aqui, e não no mcp-server ──────────────────────────────────
 *
 * Até 2026-09-04 esta função vivia dentro de `tools/dialog.ts` (mcp-server), a
 * casa do `form_get` — o único consumidor que existia. Com o editor JSON do
 * platform-ui aparece um SEGUNDO consumidor que precisa da MESMA normalização
 * (o preview mostra o que o runner receberia), e a alternativa seria uma cópia
 * no browser. Duas implementações da mesma regra divergem, e a divergência
 * apareceria como *"o preview mostrou uma coisa e o cliente viu outra"*.
 *
 * O próprio `resolveLocalizedText` já antecipava este consumidor: *"kept here so
 * the runner, the editor preview and any renderer resolve identically"*.
 *
 * ⚠️ O platform-ui **não importa `@plughub/schemas`** (sem workspaces, o
 * Dockerfile copia só o pacote, e há risco de dual-instance de Zod — ver
 * `SkillFlowsPage.tsx` § dry-run e `adr-skill-flow-editor-validation`). Logo o
 * editor não chama estas funções direto: ele PERGUNTA ao servidor
 * (`POST /api/dialog/preview`), que roda exatamente o que o `form_get` roda.
 * A regra do ADR vale igual aqui: AFORDÂNCIA no cliente, VEREDICTO no servidor.
 *
 * Conteúdo apenas — nenhum control flow. Branching é do skill chamador.
 */

import type {
  DialogForm,
  QuestionNode,
} from "./dialog"
import {
  DialogFormSchema,
  askWhenForwardRefErrors,
  optionTreeIssues,
  resolveLocalizedText,
} from "./dialog"

// ─── Render normalization (§18.4) ─────────────────────────────────────────────
// Flatten a DialogForm into a single-turn render block the dialog-runner menu
// consumes directly: leading statements → menu prompt, questions → form fields,
// trailing statements → statement_after, plus a domain-blind capture echo.
// Content-shaping only (no control flow).

export interface RenderField {
  id: string; label: string; type: string; required: boolean; masked: boolean | string
  // Approval (ADR adr-human-approval-workflow-step): pre-filled editable value +
  // per-field options (select). Absent for plain capture-only survey fields.
  value?:   string | number | boolean
  options?: RenderOption[]
  /**
   * D6 do ADR do catálogo de formatos. Até 2026-09-04 este campo NÃO existia e a
   * construção abaixo era uma ALLOWLIST — logo `fields[].validation` era
   * descartado aqui, e todo `interaction: "form"` (aprovação, solicitação de
   * limite, promoção de deploy) era estruturalmente incapaz de validar, sem
   * nada ficar vermelho. Segunda ocorrência da família DTO-01, no mesmo desenho
   * de tipo espelhado.
   */
  validation?: unknown
}
/**
 * RenderOption — uma opção e, quando há, a SUBÁRVORE dela (F3 do
 * `adr-dialog-tree-options`).
 *
 * Até a F3 o mapeamento era `{id, label}` e **descartava os filhos**. Uma forma
 * com taxonomia chegava à superfície como uma lista PLANA de raízes, e escolher
 * "Financeiro" gravava a PASTA como resposta — dado errado, sem nada vermelho.
 * Perder subárvore em silêncio é o modo de falha que a D4 já recusava no schema;
 * aqui ele reaparecia um andar abaixo.
 *
 * `active: false` (D6) é filtrado AQUI: a folha aposentada sai da OFERTA e
 * permanece no form, para o histórico continuar explicável.
 */
export interface RenderOption { id: string; label: string; options?: RenderOption[] }
// Retry affordance flattened for the menu step: reprompt localized, counter fixed.
export interface RenderRetry { reprompt: string; max_attempts: number }
export interface RenderQuestion {
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
export interface DialogRender {
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
  /**
   * F3/D11 — a forma EXIGE uma superficie que desenhe arvore. DERIVADO da
   * presenca de subarvore, nunca declarado pelo autor: campo pode ser esquecido,
   * a estrutura nao.
   *
   * Quem consome decide o que fazer com um `true` que nao sabe desenhar — e a
   * unica resposta aceitavel e RECUSAR ALTO. Achatar `Financeiro > Cobranca
   * indevida` numa lista de 40 botoes e emulacao muda: entrega uma tela que
   * parece certa e perde a hierarquia que a serie do Arc 12 vai contar.
   */
  options_tree:    boolean
}

// Flatten a question's retry (LocalizedText reprompt → string) for the menu step.
/** Mapeia opções resolvendo i18n e PRESERVANDO a subárvore; descarta aposentadas. */
function mapOptions(
  opts: ReadonlyArray<{ id: string; value?: string; label: unknown; options?: unknown; active?: boolean }> | undefined,
  locale: string | undefined,
  dl: string,
): RenderOption[] {
  return (opts ?? [])
    .filter(o => o.active !== false)
    .map(o => {
      const filhos = Array.isArray(o.options)
        ? mapOptions(o.options as Parameters<typeof mapOptions>[0], locale, dl)
        : []
      const ro: RenderOption = {
        id:    o.value ?? o.id,
        label: resolveLocalizedText(o.label as never, locale, dl),
      }
      // Pasta que ficou VAZIA por aposentadoria vira folha selecionável — e o
      // rótulo viraria resposta. Emitir `options: []` seria pior (a superfície
      // abriria uma coluna vazia), então a chave só existe quando há filho.
      if (filhos.length) ro.options = filhos
      return ro
    })
}

/** True quando ALGUMA opção tem subárvore — derivado, nunca declarado pelo autor. */
function temArvore(opts: ReadonlyArray<RenderOption>): boolean {
  return opts.some(o => (o.options?.length ?? 0) > 0)
}

function flattenRetry(q: QuestionNode, locale: string | undefined, dl: string): RenderRetry | undefined {
  if (!q.retry) return undefined
  return {
    reprompt:     resolveLocalizedText(q.retry.reprompt, locale, dl),
    max_attempts: q.retry.max_attempts,
  }
}

export function buildRender(form: DialogForm, locale?: string): DialogRender {
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
          // DENYLIST, nunca allowlist. Quatro coisas SAEM, cada uma com motivo:
          // `label` e `options` são reescritos aqui (i18n resolvida); `capture`
          // pertence ao mapa `captures`, não ao campo; e `value` é atribuído
          // logo abaixo, condicionalmente, porque `exactOptionalPropertyTypes`
          // recusa um `value: undefined` explícito.
          //
          // Todo o resto — `validation` inclusive, e o próximo campo que o
          // schema ganhar — atravessa. Enumerar o que FICA é o que fez
          // `validation` sumir daqui sem ninguém notar.
          const { label: _lbl, options: _opts, capture: _cap, value: _val, ...restoDoCampo } = f
          const rf: RenderField = {
            ...restoDoCampo,
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
          // A pergunta escalar aparece nas DUAS vistas (`render.validation` no
          // topo e um campo aqui). Carregar nas duas é de propósito: quem
          // renderiza a vista de formulário não deveria precisar saber que
          // existe uma vista single-turn ao lado para descobrir a regra.
          ...(node.validation ? { validation: node.validation } : {}),
          masked:   node.masked ?? false,   // verbatim — ver acima
        })
      }
      questions.push({
        prompt:      resolveLocalizedText(node.prompt, locale, dl),
        interaction: node.interaction,
        options:     mapOptions(node.options, locale, dl),
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
  const options: RenderOption[] = mapOptions(q?.options, locale, dl)

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
    // O `||` de antes CURTO-CIRCUITAVA: havendo statement de abertura, o prompt da
    // pergunta era descartado — e as TRÊS formas `form` do repositório têm as duas
    // coisas, logo os três prompts ("Preencha os dados da solicitação:", "Dados da
    // aprovação", "Análise de crédito") nunca chegavam a ninguém. Ninguém notou
    // porque o statement sozinho faz sentido: valor plausível, de novo.
    //
    // ⚠️ Isto MUDA o texto de três formas publicadas — mudança decidida pelo dono
    // em 2026-09-04, ao construir o editor JSON (que torna a omissão visível: o
    // autor digita o prompt e espera vê-lo).
    menu_prompt: [...before, qPrompt].filter(Boolean).join("\n\n"),
    fields,
    statement_after: after.join("\n\n"),
    captures,
    by_node: byNode,
    options_tree: temArvore(options) || questions.some(x => temArvore(x.options)),
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
export function duplicateNodeIds(form: DialogForm): string[] {
  const vistos = new Set<string>()
  const dup    = new Set<string>()
  for (const n of form.nodes) {
    if (vistos.has(n.id)) dup.add(n.id)
    vistos.add(n.id)
  }
  return [...dup].sort()
}

// ─── Veredicto estrutural (dry-run do editor) ─────────────────────────────────

/**
 * A forma como o AUTOR a escreve: sem os três campos que o STORE é dono
 * (`tenant_id` vem do header, `created_at`/`updated_at` do banco). Validar um
 * rascunho contra o schema completo reprovaria toda forma nova por falta de
 * carimbo que o autor não tem como pôr — um validador que reprova o caso normal
 * é pior que validador nenhum, porque ensina a ignorá-lo.
 */
export const DialogFormDraftSchema = DialogFormSchema.omit({
  tenant_id:  true,
  created_at: true,
  updated_at: true,
})

export interface DialogFormIssue {
  /** Endereço no documento, no formato do zod: `nodes.1.fields.0.type`. */
  path:    string
  message: string
  code:
    | "schema" | "duplicate_node_id" | "ask_when_forward_ref"
    | "option_duplicate_sibling_id" | "option_nesting_not_allowed"
    | "option_depth" | "option_empty_folder"
}

export interface DialogFormVerdict {
  valid:  boolean
  errors: DialogFormIssue[]
  /** O bloco que o `form_get` entregaria — só quando válido. */
  render: DialogRender | null
}

/**
 * O veredicto que o editor pergunta antes de salvar. Roda as MESMAS três guardas
 * que já existiam espalhadas — schema (zod), id de nó repetido (`form_get` recusa
 * alto por causa do `by_node`) e forward-ref de `ask_when` (a tela já checava à
 * mão no `save()`).
 *
 * ⚠️ **NÃO cobre o conflito `format` × `masked`** (§D8), e a razão é mecânica, não
 * de gosto: aquela regra precisa do CATÁLOGO de formatos (config-api) para
 * resolver `from_masked_type`, que uma função pura não tem. Ela continua no
 * publish da dialog-api, e a tela DIZ que o dry-run não a cobre — validador que
 * insinua completude é como se compra o "editor disse que estava bom e o save
 * recusou".
 */
export function validateDialogForm(doc: unknown, locale?: string): DialogFormVerdict {
  const errors: DialogFormIssue[] = []

  const parsed = DialogFormDraftSchema.safeParse(doc)
  if (!parsed.success) {
    for (const issue of parsed.error.errors) {
      errors.push({ path: issue.path.join("."), message: issue.message, code: "schema" })
    }
    return { valid: false, errors, render: null }
  }

  const form = parsed.data as unknown as DialogForm

  for (const id of duplicateNodeIds(form)) {
    errors.push({
      path:    "nodes",
      message: `id de nó repetido: '${id}' — render.by_node ficaria ambíguo e a referência ao nó perdido viraria texto vazio`,
      code:    "duplicate_node_id",
    })
  }

  // Árvore de opções (F1 do `adr-dialog-tree-options`). O aninhamento é
  // permitido pela INTERAÇÃO da pergunta, e recusado no campo: `DialogField` não
  // tem interação, logo não há superfície que saiba desenhar sua subárvore.
  form.nodes.forEach((node, i) => {
    if (node.kind !== "question") return
    const permite = node.interaction === "list" || node.interaction === "checklist"
    for (const issue of optionTreeIssues(node.options, {
      allowNesting: permite,
      base: `nodes.${i}.options`,
    })) {
      errors.push({ path: issue.path, message: issue.message, code: issue.code })
    }
    ;(node.fields ?? []).forEach((f, j) => {
      for (const issue of optionTreeIssues(f.options, {
        allowNesting: false,
        base: `nodes.${i}.fields.${j}.options`,
      })) {
        errors.push({ path: issue.path, message: issue.message, code: issue.code })
      }
    })
  })

  for (const { node_id, field } of askWhenForwardRefErrors(form)) {
    errors.push({
      path:    `nodes.${form.nodes.findIndex(n => n.id === node_id)}.ask_when.field`,
      message: `'${field}' não é resposta de uma pergunta ANTERIOR (guarda referencia para a frente ou para chave inexistente)`,
      code:    "ask_when_forward_ref",
    })
  }

  if (errors.length) return { valid: false, errors, render: null }
  return { valid: true, errors: [], render: buildRender(form, locale) }
}
