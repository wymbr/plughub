/**
 * ORQ-12 — o que `dialog_tree_level` entrega ao orquestrador com LLM.
 *
 * O contrato se mede no LEITOR: quem lê é o step `classificar` do
 * `skill_navegacao_llm_v1`, que manda `leaves` (conferido depois) e
 * `vocabulary` (significado) ao prompt. Daí as proposições:
 *
 *   - `vocabulary` acompanha `leaves` item a item, na MESMA ordem — é a árvore
 *     que a conferência (D6) vai aceitar, nunca outra;
 *   - `examples` chega ao classificador e **não** ao `options`, que é o bloco
 *     que os canais desenham;
 *   - quando as duas leituras da árvore divergem, a CHAVE some (e o motivo vai
 *     ao log): o prompt degrada para "só os caminhos", que é o comportamento de
 *     antes desta ficha, em vez de ficar sem destino nenhum.
 */
import { describe, it, expect, beforeEach, vi, afterEach } from "vitest"
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js"
import { registerDialogTools } from "../tools/dialog"

/**
 * A divergência entre as duas caminhadas NÃO é produzível por dado: `leafMeanings`
 * espelha as regras do `mapOptions` (aposentada fora, `value ?? id`, pasta vazia
 * vira folha), então toda forma legítima resolve igual nas duas. A guarda existe
 * contra DRIFT de código — uma das duas mudando sozinha —, e é isso que este mock
 * planta. Sem ele, o ramo `vocabulary: undefined` seria código que nenhum teste
 * pode reprovar.
 */
let driftar = false
vi.mock("@plughub/schemas", async (importOriginal) => {
  const real = await importOriginal<typeof import("@plughub/schemas")>()
  return {
    ...real,
    leafMeanings: (...args: Parameters<typeof real.leafMeanings>) => {
      const r = real.leafMeanings(...args)
      return driftar ? r.slice(1) : r
    },
  }
})

type ToolResponse = { isError?: boolean; content: Array<{ type: string; text: string }> }

const FORM = {
  form_id: "f_nav", version: 6, status: "published", default_locale: "pt-BR",
  name: "nav", locales: ["pt-BR"],
  nodes: [{
    id: "main", kind: "question", output_key: "destino", interaction: "list",
    prompt: { "pt-BR": "Como posso ajudar?" },
    options: [
      {
        id: "sac", label: { "pt-BR": "SAC" }, description: { "pt-BR": "Serviço já contratado" },
        options: [
          {
            id: "info_plano", label: { "pt-BR": "Plano" },
            description: { "pt-BR": "O que o plano inclui" },
            examples: { "pt-BR": ["quanto de internet eu tenho"] },
          },
          { id: "velho", label: { "pt-BR": "Aposentada" }, active: false },
        ],
      },
      {
        id: "aumento_limite", label: { "pt-BR": "Limite" },
        description: { "pt-BR": "Aumentar o limite do cartão" },
        examples: { "pt-BR": ["quero mais limite"] },
      },
      { id: "nao_se_aplica", label: { "pt-BR": "Nenhuma dessas" } },
    ],
  }],
}

function chamar(form: unknown): (i: unknown) => Promise<ToolResponse> {
  vi.stubGlobal("fetch", vi.fn(async () => ({
    ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(form)),
  })))
  const mcp = new McpServer({ name: "t", version: "0" })
  registerDialogTools(mcp, { dialogApiUrl: "http://dialog-api:3760", tenantId: "tenant_test" })
  const reg = (mcp as unknown as Record<string, Record<string, { handler: (i: unknown) => Promise<ToolResponse> }>>)
    ._registeredTools!["dialog_tree_level"]!
  return (i) => reg.handler(i)
}

const corpo = (r: ToolResponse) => JSON.parse(r.content[0]!.text)

describe("ORQ-12 — vocabulary do dialog_tree_level", () => {
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

  let call: (i: unknown) => Promise<ToolResponse>
  beforeEach(() => { call = chamar(FORM) })

  it("acompanha `leaves` item a item, com rótulo, descrição e exemplos", async () => {
    const r = corpo(await call({ form_id: "f_nav", output_key: "destino", path: [] }))
    expect(r.leaves).toEqual(["sac.info_plano", "aumento_limite", "nao_se_aplica"])
    expect(r.vocabulary.map((v: { path: string }) => v.path)).toEqual(r.leaves)
    expect(r.vocabulary[0]).toEqual({
      path: "sac.info_plano", label: "Plano",
      description: "O que o plano inclui", examples: ["quanto de internet eu tenho"],
    })
    // Folha sem significado declarado não ganha campo vazio — ausência é ausência.
    expect(r.vocabulary[2]).toEqual({ path: "nao_se_aplica", label: "Nenhuma dessas" })
  })

  it("sob um cursor, o vocabulário é o do NÍVEL, com o caminho inteiro", async () => {
    const r = corpo(await call({ form_id: "f_nav", output_key: "destino", path: ["sac"] }))
    expect(r.vocabulary.map((v: { path: string }) => v.path)).toEqual(["sac.info_plano"])
  })

  it("`examples` NÃO viaja no `options` — aquele bloco é o que os canais desenham", async () => {
    const r = corpo(await call({ form_id: "f_nav", output_key: "destino", path: [] }))
    expect(JSON.stringify(r.options)).not.toContain("examples")
    expect(JSON.stringify(r.options)).not.toContain("quero mais limite")
  })

  it("drift entre as duas leituras OMITE o vocabulário e diz por quê", async () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {})
    driftar = true
    try {
      const r = corpo(await call({ form_id: "f_nav", output_key: "destino", path: [] }))
      // O que se CONFERE continua inteiro; o que some é a ajuda de classificação.
      expect(r.leaves).toEqual(["sac.info_plano", "aumento_limite", "nao_se_aplica"])
      expect("vocabulary" in r).toBe(false)
      expect(warn).toHaveBeenCalledTimes(1)
      expect(String(warn.mock.calls[0]![0])).toContain("vocabulario OMITIDO")
    } finally {
      driftar = false
    }
  })
})
