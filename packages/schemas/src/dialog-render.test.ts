/**
 * dialog-render.test.ts
 * Cobre a normalização que o `form_get` entrega e o veredicto que o editor
 * pergunta. Escrito junto com o editor JSON (2026-09-04) porque a função mudou
 * de casa e ganhou um segundo consumidor — e porque o defeito do `menu_prompt`
 * viveu meses sem nada vermelho.
 */
import { describe, it, expect } from "vitest"
import { buildRender, validateDialogForm } from "./dialog-render"
import type { DialogForm } from "./dialog"

const base = {
  form_id: "dialog_t", tenant_id: "t", name: "T", status: "published" as const,
  version: 1, default_locale: "pt-BR", locales: ["pt-BR"], dimensions: [], tags: [],
  created_at: "2026-09-04T00:00:00.000Z", updated_at: "2026-09-04T00:00:00.000Z",
}

const q = (over: Record<string, unknown> = {}) => ({
  id: "q1", kind: "question" as const, prompt: "Preencha os dados:",
  interaction: "form" as const, output_key: "dados", timeout_s: 300, ...over,
})

describe("buildRender — menu_prompt", () => {
  it("mantém o prompt da pergunta QUANDO há statement de abertura", () => {
    // A regressão que existia: `before.join() || qPrompt` curto-circuitava e o
    // prompt sumia. Sem esta asserção o defeito é invisível — o statement
    // sozinho parece uma tela correta.
    const form = { ...base, nodes: [
      { id: "s1", kind: "statement" as const, text: "Vou registrar seu pedido." },
      q(),
    ] } as unknown as DialogForm
    expect(buildRender(form).menu_prompt).toBe("Vou registrar seu pedido.\n\nPreencha os dados:")
  })

  it("controle positivo: sem statement, o menu_prompt é só o prompt", () => {
    const form = { ...base, nodes: [q()] } as unknown as DialogForm
    expect(buildRender(form).menu_prompt).toBe("Preencha os dados:")
  })
})

describe("buildRender — fields", () => {
  it("emite um RenderField por campo declarado, com masked e validation VERBATIM", () => {
    const form = { ...base, nodes: [q({ fields: [
      { id: "numero", label: "Número", type: "text", required: true, validation: { format: "credit_card" } },
      { id: "cvv",    label: "CVV",    type: "text", required: true, masked: "card_cvv" },
    ] })] } as unknown as DialogForm
    const r = buildRender(form)
    expect(r.fields.map(f => f.id)).toEqual(["numero", "cvv"])
    // `=== true` faria "card_cvv" virar false e o campo sair DESMASCARADO.
    expect(r.fields[1]!.masked).toBe("card_cvv")
    expect(r.fields[0]!.validation).toEqual({ format: "credit_card" })
    expect(r.fields[0]!.masked).toBe(false)
  })

  it("pergunta `form` SEM fields degenera num campo sintético `choice` sem opções", () => {
    // É exatamente o que o editor de widgets produzia ao escolher `form`: nó
    // morto. Fica testado para que a forma do defeito seja legível, não para
    // abençoá-la — a tela agora impede o estado.
    const form = { ...base, nodes: [q()] } as unknown as DialogForm
    const r = buildRender(form)
    expect(r.fields).toHaveLength(1)
    expect(r.fields[0]).toMatchObject({ id: "dados", type: "choice", required: true })
    expect(r.fields[0]!.options).toBeUndefined()
  })
})

describe("validateDialogForm", () => {
  const draft = {
    form_id: "dialog_t", name: "T", default_locale: "pt-BR", locales: ["pt-BR"],
    dimensions: [], tags: [],
    nodes: [{ id: "q1", kind: "question", prompt: "P", interaction: "text", output_key: "a" }],
  }

  it("ACEITA rascunho sem tenant_id/created_at/updated_at (o store é dono deles)", () => {
    // Sem este corte o validador reprovaria toda forma nova — validador que
    // reprova o caso normal ensina a ignorá-lo.
    const v = validateDialogForm(draft)
    expect(v.valid).toBe(true)
    expect(v.render).not.toBeNull()
  })

  it("recusa id de nó repetido, nomeando o id", () => {
    const v = validateDialogForm({ ...draft, nodes: [...draft.nodes, { ...draft.nodes[0], output_key: "b" }] })
    expect(v.valid).toBe(false)
    expect(v.errors.some(e => e.code === "duplicate_node_id" && e.message.includes("q1"))).toBe(true)
  })

  it("recusa ask_when que referencia pergunta POSTERIOR", () => {
    const v = validateDialogForm({ ...draft, nodes: [
      { id: "q0", kind: "question", prompt: "P0", interaction: "text", output_key: "x",
        ask_when: { field: "a", op: "eq", value: 1 } },
      ...draft.nodes,
    ] })
    expect(v.valid).toBe(false)
    expect(v.errors[0]!.code).toBe("ask_when_forward_ref")
    expect(v.errors[0]!.path).toBe("nodes.0.ask_when.field")
  })

  it("erro de schema vem com o CAMINHO no documento", () => {
    const v = validateDialogForm({ ...draft, nodes: [{ ...draft.nodes[0], interaction: "carrossel" }] })
    expect(v.valid).toBe(false)
    expect(v.errors[0]!.code).toBe("schema")
    expect(v.errors[0]!.path).toContain("nodes.0")
    expect(v.render).toBeNull()
  })
})

describe("validateDialogForm — árvore de opções (F1, adr-dialog-tree-options)", () => {
  // Estes casos existem porque a REGRA e a LIGAÇÃO dela são dois fatos: as
  // unidades de `optionTreeIssues` vivem no `dialog.test.ts` e continuariam
  // verdes com a chamada desligada aqui — que é o veredicto que o `form_get` e
  // o dry-run do editor realmente rodam.
  const arvore = [{
    id: "financeiro", label: "Financeiro",
    options: [{ id: "cobranca", label: "Cobrança", options: [{ id: "indevida", label: "Indevida" }] }],
  }]

  it("recusa aninhamento sob interação que não desenha árvore, nomeando o nó", () => {
    const form = { ...base, nodes: [
      { id: "q1", kind: "question", prompt: "Motivo?", interaction: "button",
        output_key: "motivo", timeout_s: 300, options: arvore },
    ] }
    const v = validateDialogForm(form)
    expect(v.valid).toBe(false)
    expect(v.errors.map(e => e.code)).toContain("option_nesting_not_allowed")
    expect(v.errors.find(e => e.code === "option_nesting_not_allowed")?.path)
      .toBe("nodes.0.options.0.options")
  })

  it("controle positivo: a MESMA árvore sob `list` passa e chega ao render", () => {
    const form = { ...base, nodes: [
      { id: "q1", kind: "question", prompt: "Motivo?", interaction: "list",
        output_key: "motivo", timeout_s: 300, options: arvore },
    ] }
    const v = validateDialogForm(form)
    expect(v.valid).toBe(true)
    expect(v.render?.options.map(o => o.id)).toEqual(["financeiro"])
  })

  it("recusa subárvore em CAMPO — `DialogField` não tem interação que a desenhe", () => {
    const form = { ...base, nodes: [
      { id: "q1", kind: "question", prompt: "Dados:", interaction: "form",
        output_key: "dados", timeout_s: 300,
        fields: [{ id: "motivo", label: "Motivo", type: "select", options: arvore }] },
    ] }
    const v = validateDialogForm(form)
    expect(v.valid).toBe(false)
    expect(v.errors.find(e => e.code === "option_nesting_not_allowed")?.path)
      .toBe("nodes.0.fields.0.options.0.options")
  })
})
