/**
 * dialog-return.test.ts — RET-01: a folha DEVOLVE
 * (`docs/adr/adr-tree-return-continuation.md`, D2/D3/D4).
 *
 * ⚠️ O caso que carrega peso NÃO é o do ponteiro funcionando — é a
 * COMPATIBILIDADE: `buildRender` sem `fromQuestionId` tem de produzir
 * exatamente o que produzia, inclusive nas formas com VÁRIAS questions. Medido
 * em 2026-09-06: 5 das 14 formas publicadas têm mais de uma question (survey e
 * wrap-up), e aplicar a janela do bloco a todas mudaria o `statement_after`
 * delas em silêncio. Se alguém tornar a janela incondicional, o teste
 * `mantém o comportamento de sempre` fica vermelho e nomeia o motivo.
 */

import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import { resolve } from "node:path"
import { buildRender, categoryPathFor, entryQuestionId, returnRefErrors, validateDialogForm } from "./dialog-render"
import type { DialogForm } from "./dialog"

const base = {
  form_id: "f", tenant_id: "t", name: "F",
  status: "published" as const, version: 1,
  default_locale: "pt-BR", locales: ["pt-BR"],
  dimensions: [],
}

/** s1 · Q(main) · s2 · Q(pos) · s3 — duas questions, statements entre elas. */
const FORMA = {
  ...base,
  nodes: [
    { id: "s1", kind: "statement", text: { "pt-BR": "Bem-vindo" } },
    {
      id: "main", kind: "question", prompt: { "pt-BR": "O que deseja?" },
      interaction: "list", output_key: "escolha", capture: {},
      options: [
        { id: "sac", label: { "pt-BR": "SAC" }, on_return: "pos" },
        { id: "sair", label: { "pt-BR": "Sair" } },
      ],
    },
    { id: "s2", kind: "statement", text: { "pt-BR": "Pronto." } },
    {
      id: "pos", kind: "question", prompt: { "pt-BR": "Mais alguma coisa?" },
      interaction: "button", output_key: "cont", capture: {},
      options: [{ id: "nao", label: { "pt-BR": "Não" } }],
    },
    { id: "s3", kind: "statement", text: { "pt-BR": "Até logo" } },
  ],
} as unknown as DialogForm

describe("entryQuestionId — a convenção `main` (D3)", () => {
  it("prefere a question `main`", () => {
    expect(entryQuestionId(FORMA)).toBe("main")
  })
  it("sem `main`, cai na PRIMEIRA question — o comportamento de sempre", () => {
    const semMain = {
      ...base,
      nodes: [
        { id: "q1", kind: "question", prompt: { "pt-BR": "a" }, interaction: "text", output_key: "a", capture: {} },
        { id: "q2", kind: "question", prompt: { "pt-BR": "b" }, interaction: "text", output_key: "b", capture: {} },
      ],
    } as unknown as DialogForm
    expect(entryQuestionId(semMain)).toBe("q1")
  })
})

describe("buildRender — compatibilidade (o caso que carrega peso)", () => {
  it("mantém o comportamento de sempre: sem `fromQuestionId`, `after` é TUDO depois da primeira", () => {
    const r = buildRender(FORMA)
    expect(r.output_key).toBe("escolha")
    // `s2` E `s3` — se a janela do bloco fosse incondicional, `s3` sumiria e as
    // 5 formas multi-question do parque mudariam de texto sem ninguém notar.
    expect(r.statement_after).toBe("Pronto.\n\nAté logo")
  })
  it("e o array `questions` descreve a FORMA inteira, com ou sem janela", () => {
    expect(buildRender(FORMA).questions).toHaveLength(2)
    expect(buildRender(FORMA, undefined, "pos").questions).toHaveLength(2)
  })
})

describe("buildRender — a janela do bloco (D4)", () => {
  const r = buildRender(FORMA, undefined, "pos")

  it("parte da question pedida", () => {
    expect(r.output_key).toBe("cont")
    expect(r.interaction).toBe("button")
  })
  it("traz o statement que PRECEDE a question — é isso que faz do alvo um BLOCO", () => {
    expect(r.menu_prompt).toBe("Pronto.\n\nMais alguma coisa?")
  })
  it("e não vaza o statement de abertura da entrada", () => {
    expect(r.menu_prompt).not.toContain("Bem-vindo")
    expect(r.statement_after).toBe("Até logo")
  })
  it("os `fields` são do TURNO, então seguem a janela", () => {
    expect(r.fields.map(f => f.id)).toEqual(["cont"])
  })
  // Ponteiro quebrado é recusado na VALIDAÇÃO, onde há quem leia o erro. Aqui
  // ele cai no padrão — render vazio seria um aviso em branco para o cliente.
  it("`fromQuestionId` inexistente cai no padrão, nunca em render vazio", () => {
    const r2 = buildRender(FORMA, undefined, "nao_existe")
    expect(r2.output_key).toBe("escolha")
    expect(r2.prompt).toContain("O que deseja?")
  })
})

describe("on_return — o ponteiro viaja, e é CONFERIDO (D2)", () => {
  it("chega ao render, porque quem decide o retorno lê a árvore pelo render", () => {
    const opt = buildRender(FORMA).options.find(o => o.id === "sac")
    expect(opt?.on_return).toBe("pos")
    expect(buildRender(FORMA).options.find(o => o.id === "sair")?.on_return).toBeUndefined()
  })
  it("forma conforme não acusa nada", () => {
    expect(returnRefErrors(FORMA)).toEqual([])
    expect(validateDialogForm(FORMA).valid).toBe(true)
  })
  it("ponteiro para question inexistente é RECUSADO, nomeando opção e alvo", () => {
    const quebrada = JSON.parse(JSON.stringify(FORMA)) as DialogForm
    ;(quebrada.nodes[1] as { options: { on_return?: string }[] }).options[0]!.on_return = "fantasma"
    const errs = returnRefErrors(quebrada)
    expect(errs).toHaveLength(1)
    expect(errs[0]!.option_id).toBe("sac")
    expect(errs[0]!.target).toBe("fantasma")

    const v = validateDialogForm(quebrada)
    expect(v.valid).toBe(false)
    expect(v.errors.map(e => e.code)).toContain("return_ref_unknown")
  })
  it("apontar para um STATEMENT também é recusado — statement não escuta", () => {
    const paraStatement = JSON.parse(JSON.stringify(FORMA)) as DialogForm
    ;(paraStatement.nodes[1] as { options: { on_return?: string }[] }).options[0]!.on_return = "s2"
    expect(returnRefErrors(paraStatement).map(e => e.target)).toEqual(["s2"])
  })
  it("o ponteiro é achado em qualquer PROFUNDIDADE da árvore", () => {
    const aninhada = {
      ...base,
      nodes: [{
        id: "main", kind: "question", prompt: { "pt-BR": "?" },
        interaction: "list", output_key: "v", capture: {},
        options: [{
          id: "pasta", label: { "pt-BR": "P" },
          options: [{ id: "folha", label: { "pt-BR": "F" }, on_return: "fantasma" }],
        }],
      }],
    } as unknown as DialogForm
    expect(returnRefErrors(aninhada).map(e => e.option_id)).toEqual(["folha"])
  })
})

/**
 * `categoryPathFor` — ENDERECO x MEDICAO (D5), e a discordancia que custou um
 * contato real em 2026-09-07.
 *
 * A tool compunha `path.join(".")` para TODA question, entao a continuacao media
 * `outra_coisa` enquanto o fluxo e o `navigation_pools` comparavam
 * `pos_atendimento.outra_coisa`. Nenhum dos dois estava errado sozinho: eles
 * compunham o mesmo caminho em DUAS casas, e so uma era o runtime. O comando nao
 * casava, o caminho seguia como demanda, e o contato caia na fila humana.
 *
 * ⚠️ Os vetores sao COMPARTILHADOS com o gemeo Python
 * (`infra/test/_ret05_continuation_probe.py`, modo `composicao`) — paridade
 * presumida entre linguagens ja custou caro neste repositorio.
 */
describe("categoryPathFor — vetores compartilhados com o probe Python", () => {
  const vetores = JSON.parse(
    readFileSync(resolve(__dirname, "../../../infra/test/fixtures/category_path_vectors.json"), "utf8"),
  ) as { vetores: Array<{ nome: string; entry: string | null; question: string | null; path: string[]; category_path: string }> }

  it.each(vetores.vetores.map(v => [v.nome, v] as const))("%s", (_nome, v) => {
    expect(categoryPathFor(v.entry ?? undefined, v.question ?? undefined, v.path)).toBe(v.category_path)
  })

  // ── O que NAO pode mudar junto ──────────────────────────────────────────────
  it("o ENDERECO nao leva prefixo — prefixa-lo reiniciaria a navegacao em silencio", () => {
    // `path` volta para dentro da tool como `input.path`, e `optionsAtPath` caminha
    // o `options` da question corrente. Um `pos_atendimento` na frente faria a
    // projecao procurar uma opcao com esse id na raiz, nao achar, e devolver
    // `found: false` — a navegacao reiniciaria e a tela pareceria certa.
    const trilha = ["outra_coisa"]
    expect(categoryPathFor("destino", "pos_atendimento", trilha)).toBe("pos_atendimento.outra_coisa")
    expect(trilha).toEqual(["outra_coisa"])
  })
})
