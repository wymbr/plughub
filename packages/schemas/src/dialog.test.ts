/**
 * dialog.test.ts — ask_when evaluator + forward-reference validation
 * (conditional skip-logic, adr-dialog-conditional-skip-logic).
 */
import { describe, it, expect } from "vitest"
import {
  evaluateAskWhen, askWhenForwardRefErrors, optionTreeIssues, DialogOptionSchema,
  type AskWhen, type DialogForm, type DialogOption,
} from "./dialog"

describe("evaluateAskWhen", () => {
  const answers = { csat: "2", nps: 9, operadora: "vivo" }

  it("no guard → always present", () => {
    expect(evaluateAskWhen(undefined, answers)).toBe(true)
  })

  it("numeric comparisons (string answers coerced)", () => {
    expect(evaluateAskWhen({ field: "csat", op: "lt", value: 3 }, answers)).toBe(true)
    expect(evaluateAskWhen({ field: "csat", op: "gte", value: 3 }, answers)).toBe(false)
    expect(evaluateAskWhen({ field: "nps", op: "gt", value: 8 }, answers)).toBe(true)
    expect(evaluateAskWhen({ field: "nps", op: "lte", value: 6 }, answers)).toBe(false)
  })

  it("eq / ne (numeric then string)", () => {
    expect(evaluateAskWhen({ field: "csat", op: "eq", value: 2 }, answers)).toBe(true)
    expect(evaluateAskWhen({ field: "operadora", op: "eq", value: "vivo" }, answers)).toBe(true)
    expect(evaluateAskWhen({ field: "operadora", op: "ne", value: "claro" }, answers)).toBe(true)
  })

  it("in (membership)", () => {
    expect(evaluateAskWhen({ field: "operadora", op: "in", value: ["vivo", "tim"] }, answers)).toBe(true)
    expect(evaluateAskWhen({ field: "operadora", op: "in", value: ["claro", "oi"] }, answers)).toBe(false)
    expect(evaluateAskWhen({ field: "csat", op: "in", value: [1, 2, 3] }, answers)).toBe(true)
  })

  it("absent/empty answer → skip (guard false)", () => {
    expect(evaluateAskWhen({ field: "missing", op: "lt", value: 3 }, answers)).toBe(false)
    expect(evaluateAskWhen({ field: "blank", op: "eq", value: "" }, { blank: "" })).toBe(false)
  })

  it("non-numeric answer with numeric op → false (NaN compare)", () => {
    expect(evaluateAskWhen({ field: "operadora", op: "lt", value: 3 }, answers)).toBe(false)
  })
})

describe("askWhenForwardRefErrors", () => {
  const q = (output_key: string, ask_when?: AskWhen) =>
    ({ id: output_key, kind: "question", prompt: "", interaction: "text", output_key, ask_when })

  it("accepts a backward reference", () => {
    const form = { nodes: [q("csat"), q("motivo", { field: "csat", op: "lt", value: 3 })] } as unknown as DialogForm
    expect(askWhenForwardRefErrors(form)).toEqual([])
  })

  it("flags a forward reference", () => {
    // 'motivo' guards on 'csat' which appears AFTER it → error
    const form = { nodes: [q("motivo", { field: "csat", op: "lt", value: 3 }), q("csat")] } as unknown as DialogForm
    expect(askWhenForwardRefErrors(form)).toEqual([{ node_id: "motivo", field: "csat" }])
  })

  it("flags an unknown reference", () => {
    const form = { nodes: [q("csat"), q("motivo", { field: "nope", op: "eq", value: 1 })] } as unknown as DialogForm
    expect(askWhenForwardRefErrors(form)).toEqual([{ node_id: "motivo", field: "nope" }])
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// F1 do `adr-dialog-tree-options` — opções em ÁRVORE (D1/D3/D4) e a D12 no
// avaliador. O que estes testes julgam é a CORREÇÃO da canônica; a PARIDADE com
// as duas cópias é outro fato, e tem gate próprio (`probe_ask_when_parity.sh`).
// ─────────────────────────────────────────────────────────────────────────────

describe("evaluateAskWhen — prefix e multi-resposta (D12)", () => {
  it("prefix desce o ramo, e o próprio nó casa", () => {
    const a = { motivo: "financeiro.cobranca.indevida" }
    expect(evaluateAskWhen({ field: "motivo", op: "prefix", value: "financeiro" }, a)).toBe(true)
    expect(evaluateAskWhen({ field: "motivo", op: "prefix", value: "financeiro.cobranca" }, a)).toBe(true)
    expect(evaluateAskWhen({ field: "motivo", op: "prefix", value: "financeiro.cobranca.indevida" }, a)).toBe(true)
  })

  it("prefix casa por SEGMENTO, nunca por substring", () => {
    // `startsWith` cru faria 'financeiro' casar 'financeiro_avulso', que não é
    // filho do ramo — a pergunta errada apareceria sem nada ficar vermelho.
    expect(evaluateAskWhen({ field: "m", op: "prefix", value: "financeiro" }, { m: "financeiro_avulso" })).toBe(false)
    expect(evaluateAskWhen({ field: "m", op: "prefix", value: "financeiro" }, { m: "tecnico.sinal" })).toBe(false)
  })

  it("multi-resposta: igualdade é 'algum casa'", () => {
    const a = { m: ["financeiro.cobranca.indevida", "tecnico.sinal"] }
    expect(evaluateAskWhen({ field: "m", op: "eq", value: "tecnico.sinal" }, a)).toBe(true)
    expect(evaluateAskWhen({ field: "m", op: "in", value: ["x", "tecnico.sinal"] }, a)).toBe(true)
    expect(evaluateAskWhen({ field: "m", op: "prefix", value: "financeiro" }, a)).toBe(true)
  })

  it("`ne` é a NEGAÇÃO de `eq`, nunca 'algum difere'", () => {
    // Se `ne` fosse "algum difere", uma marcação com X e Y satisfaria `eq X` E
    // `ne X` ao mesmo tempo, e "pergunte a menos que tenham escolhido X" mentiria.
    const a = { m: ["x", "y"] }
    expect(evaluateAskWhen({ field: "m", op: "eq", value: "x" }, a)).toBe(true)
    expect(evaluateAskWhen({ field: "m", op: "ne", value: "x" }, a)).toBe(false)
    expect(evaluateAskWhen({ field: "m", op: "ne", value: "z" }, a)).toBe(true)
  })

  it("ordenação sobre multi-resposta é indefinida ⇒ guarda falsa", () => {
    expect(evaluateAskWhen({ field: "m", op: "lt",  value: 9 }, { m: [1, 2] })).toBe(false)
    expect(evaluateAskWhen({ field: "m", op: "gte", value: 0 }, { m: [1, 2] })).toBe(false)
    // controle positivo: escalar continua comparando
    expect(evaluateAskWhen({ field: "m", op: "lt", value: 9 }, { m: 1 })).toBe(true)
  })

  it("lista vazia é 'não respondeu' — como `\"\"`", () => {
    expect(evaluateAskWhen({ field: "m", op: "ne", value: "x" }, { m: [] })).toBe(false)
    expect(evaluateAskWhen({ field: "m", op: "eq", value: "x" }, { m: [] })).toBe(false)
  })
})

describe("optionTreeIssues — as regras da árvore (D2/D3/D4/D6)", () => {
  const folha = (id: string) => ({ id, label: id })
  const pasta = (id: string, filhos: DialogOption[]) => ({ id, label: id, options: filhos })

  it("árvore válida sob list não produz issue (controle positivo)", () => {
    const t = [pasta("financeiro", [pasta("cobranca", [folha("indevida")]), folha("outro")]), folha("na")]
    expect(optionTreeIssues(t, { allowNesting: true })).toEqual([])
  })

  it("aninhamento sob interação que não é list/checklist é erro, não render parcial", () => {
    const t = [pasta("financeiro", [folha("cobranca")])]
    const issues = optionTreeIssues(t, { allowNesting: false })
    expect(issues.map(i => i.code)).toEqual(["option_nesting_not_allowed"])
    expect(issues[0].path).toBe("options.0.options")
  })

  it("id repetido ENTRE IRMÃOS é erro; repetido em ramos diferentes não é", () => {
    const irmaos = [folha("x"), folha("x")]
    expect(optionTreeIssues(irmaos, { allowNesting: true }).map(i => i.code))
      .toEqual(["option_duplicate_sibling_id"])
    // o caminho é que identifica: `a.x` e `b.x` são folhas distintas
    const ramos = [pasta("a", [folha("x")]), pasta("b", [folha("x")])]
    expect(optionTreeIssues(ramos, { allowNesting: true })).toEqual([])
  })

  it("profundidade acima de 5 é erro de autoria (D3)", () => {
    const cinco = pasta("n1", [pasta("n2", [pasta("n3", [pasta("n4", [folha("n5")])])])])
    expect(optionTreeIssues([cinco], { allowNesting: true })).toEqual([])
    const seis = pasta("n1", [pasta("n2", [pasta("n3", [pasta("n4", [pasta("n5", [folha("n6")])])])])])
    expect(optionTreeIssues([seis], { allowNesting: true }).map(i => i.code)).toEqual(["option_depth"])
  })

  it("`options: []` é recusado — pasta sem filho lê-se de dois jeitos", () => {
    const t = [{ id: "vazia", label: "vazia", options: [] }]
    expect(optionTreeIssues(t, { allowNesting: true }).map(i => i.code)).toEqual(["option_empty_folder"])
  })

  it("a subárvore sobrevive ao parse do schema (z.lazy)", () => {
    const t = pasta("financeiro", [pasta("cobranca", [folha("indevida")])])
    const out = DialogOptionSchema.parse(t) as DialogOption
    expect(out.options?.[0].options?.[0].id).toBe("indevida")
  })

  it("`active: false` sobrevive — aposentar não é apagar (D6)", () => {
    const out = DialogOptionSchema.parse({ id: "x", label: "X", active: false }) as DialogOption
    expect(out.active).toBe(false)
  })
})
