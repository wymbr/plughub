/**
 * ORQ-12 — o SIGNIFICADO da folha (`description` + `examples`) e o vocabulário
 * que o orquestrador com LLM lê.
 *
 * Os casos que sustentam isto não são os felizes:
 *   · `leafMeanings` tem de produzir EXATAMENTE os caminhos de `leafPaths` sobre o
 *     render — é a conferência que o `dialog_tree_level` faz, e se as duas
 *     caminhadas divergirem (aposentada, `value`, pasta esvaziada) o classificador
 *     veria uma árvore que a D6 não aceita;
 *   · exemplo numa PASTA é recusado — ensinaria o LLM a aterrissar no que não é
 *     resposta;
 *   · `examples` NUNCA entra no `render`, que vai aos canais.
 */

import { describe, expect, it } from "vitest"

import { buildRender, leafMeanings, leafPaths } from "./dialog-render"
import {
  DIALOG_OPTION_DESCRIPTION_MAX,
  DIALOG_OPTION_EXAMPLES_MAX,
  DialogOptionSchema,
  optionTreeIssues,
  resolveLocalizedList,
} from "./dialog"
import type { DialogForm, DialogOption } from "./dialog"

const OPCOES: DialogOption[] = [
  {
    id: "sac", label: { "pt-BR": "SAC", en: "Support" },
    description: "Dúvidas e problemas com o seu plano",
    options: [
      {
        id: "info_plano", label: "Informações do plano",
        description: { "pt-BR": "Consultar o que o seu plano inclui", en: "What your plan includes" },
        examples: { "pt-BR": ["quanto de internet eu tenho?"], en: ["how much data do I have?"] },
      },
      { id: "velho", label: "Aposentada", active: false, examples: ["não deve aparecer"] },
      { id: "especialista", value: "humano", label: "Falar com uma pessoa" },
    ],
  },
  // Pasta cujos filhos foram TODOS aposentados: vira folha no render.
  { id: "pasta_morta", label: "Pasta morta", options: [{ id: "x", label: "x", active: false }] },
  { id: "aumento_limite", label: "Aumento de limite", examples: ["quero mais limite no cartão", "  "] },
]

function form(options: DialogOption[]): DialogForm {
  return {
    form_id: "f", version: 1, status: "published", default_locale: "pt-BR", title: "t",
    nodes: [{ kind: "question", id: "main", output_key: "destino", prompt: "?", interaction: "list", options }],
  } as unknown as DialogForm
}

describe("leafMeanings — os caminhos batem com leafPaths", () => {
  it("raiz: mesma lista, mesma ordem, com aposentada fora, value no lugar do id e pasta esvaziada como folha", () => {
    const render = buildRender(form(OPCOES))
    const q = render.questions[0]!
    const m = leafMeanings(OPCOES, [], "pt-BR")
    expect(m.map(x => x.path)).toEqual(leafPaths(q.options))
    expect(m.map(x => x.path)).toEqual(["sac.info_plano", "sac.humano", "pasta_morta", "aumento_limite"])
  })

  it("sob um cursor: prefixa a trilha, como o `leaves` da tool", () => {
    expect(leafMeanings(OPCOES, ["sac"], "pt-BR").map(x => x.path)).toEqual(["sac.info_plano", "sac.humano"])
  })

  it("cursor que não resolve devolve VAZIO — nunca a raiz", () => {
    expect(leafMeanings(OPCOES, ["nao_existe"], "pt-BR")).toEqual([])
    // Aposentada não é caminho navegável, igual ao `optionsAtPath`.
    expect(leafMeanings(OPCOES, ["sac", "velho"], "pt-BR")).toEqual([])
  })
})

describe("leafMeanings — o significado", () => {
  it("resolve rótulo, descrição e exemplos na língua pedida", () => {
    const [info] = leafMeanings(OPCOES, ["sac"], "pt-BR", "en")
    expect(info).toEqual({
      path: "sac.info_plano", label: "Informações do plano",
      description: "What your plan includes", examples: ["how much data do I have?"],
    })
  })

  it("sem descrição nem exemplo, as CHAVES não existem (nunca string vazia ou lista vazia)", () => {
    const humano = leafMeanings(OPCOES, [], "pt-BR").find(x => x.path === "sac.humano")!
    expect(humano).toEqual({ path: "sac.humano", label: "Falar com uma pessoa" })
    expect("description" in humano).toBe(false)
    expect("examples" in humano).toBe(false)
  })

  it("exemplo em branco é descartado", () => {
    const lim = leafMeanings(OPCOES, [], "pt-BR").find(x => x.path === "aumento_limite")!
    expect(lim.examples).toEqual(["quero mais limite no cartão"])
  })
})

describe("examples NUNCA vai ao render", () => {
  it("o bloco que os canais recebem não carrega `examples` em nível nenhum", () => {
    const render = buildRender(form(OPCOES))
    expect(JSON.stringify(render)).not.toContain("examples")
    expect(JSON.stringify(render)).not.toContain("quero mais limite")
  })
})

describe("optionTreeIssues — tetos do significado", () => {
  const codes = (opts: DialogOption[]) => optionTreeIssues(opts, { allowNesting: true }).map(i => i.code)

  it("controle positivo: a árvore de exemplo, sem o exemplo em branco, não tem problema nenhum", () => {
    // O `"  "` do `aumento_limite` é DELIBERADO e está isolado no caso dos tetos:
    // `leafMeanings` o descarta na leitura, e aqui o validador o recusa na autoria.
    // Se ele entrasse no controle positivo, o positivo não seria controle de nada.
    const limpa = OPCOES.map(o =>
      o.id === "aumento_limite" ? { ...o, examples: ["quero mais limite no cartão"] } : o)
    expect(codes(limpa)).toEqual([])
    expect(codes(OPCOES)).toEqual(["option_examples_limit"])
  })

  it("descrição acima do teto é recusada, por língua", () => {
    const longa = "x".repeat(DIALOG_OPTION_DESCRIPTION_MAX + 1)
    expect(codes([{ id: "a", label: "a", description: longa }])).toEqual(["option_description_too_long"])
    const issues = optionTreeIssues(
      [{ id: "a", label: "a", description: { "pt-BR": "curta", en: longa } }], { allowNesting: true },
    )
    expect(issues.map(i => i.path)).toEqual(["options.0.description.en"])
    // No teto exato passa.
    expect(codes([{ id: "a", label: "a", description: "x".repeat(DIALOG_OPTION_DESCRIPTION_MAX) }])).toEqual([])
  })

  it("exemplo numa PASTA é recusado; descrição numa pasta é aceita", () => {
    expect(codes([{
      id: "p", label: "p", description: "ok", examples: ["x"], options: [{ id: "f", label: "f" }],
    }])).toEqual(["option_examples_on_folder"])
  })

  it("exemplos demais, longos demais ou vazios são recusados", () => {
    const muitos = Array.from({ length: DIALOG_OPTION_EXAMPLES_MAX + 1 }, (_, i) => `frase ${i}`)
    expect(codes([{ id: "a", label: "a", examples: muitos }])).toEqual(["option_examples_limit"])
    expect(codes([{ id: "a", label: "a", examples: ["x".repeat(121)] }])).toEqual(["option_examples_limit"])
    expect(codes([{ id: "a", label: "a", examples: ["   "] }])).toEqual(["option_examples_limit"])
  })

  it("a regra desce na árvore", () => {
    expect(optionTreeIssues([{
      id: "p", label: "p", options: [{ id: "f", label: "f", description: "x".repeat(80) }],
    }], { allowNesting: true }).map(i => i.path)).toEqual(["options.0.options.0.description"])
  })
})

describe("schema", () => {
  it("aceita os dois formatos de cada campo, e recusa lista com não-string", () => {
    expect(DialogOptionSchema.safeParse({ id: "a", label: "a", description: "d", examples: ["e"] }).success).toBe(true)
    expect(DialogOptionSchema.safeParse({
      id: "a", label: "a", description: { en: "d" }, examples: { en: ["e"] },
    }).success).toBe(true)
    expect(DialogOptionSchema.safeParse({ id: "a", label: "a", examples: [1] }).success).toBe(false)
    expect(DialogOptionSchema.safeParse({ id: "a", label: "a", examples: "e" }).success).toBe(false)
  })

  it("resolveLocalizedList segue a mesma ordem do texto e nunca mistura línguas", () => {
    const l = { "pt-BR": ["a"], en: ["b"] }
    expect(resolveLocalizedList(l, "en", "pt-BR")).toEqual(["b"])
    expect(resolveLocalizedList(l, "es", "pt-BR")).toEqual(["a"])
    expect(resolveLocalizedList(["x"], "en", "pt-BR")).toEqual(["x"])
    expect(resolveLocalizedList(undefined, "en", "pt-BR")).toEqual([])
  })
})
