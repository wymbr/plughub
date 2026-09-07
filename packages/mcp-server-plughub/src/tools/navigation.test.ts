/**
 * O casamento `caminho → pool` é por PREFIXO DE SEGMENTO, e o que ele recusa
 * importa mais do que o que ele aceita.
 *
 * Dois modos de falha, ambos silenciosos:
 *   · `startsWith` cru faria `sac_premium` casar com a chave `sac` e mandar o
 *     contato para o pool errado com o log dizendo que casou;
 *   · devolver algo para um caminho não mapeado converteria config ausente em
 *     despacho — o `queue_pool_id or pool_id` do CLAUDE.md.
 */
import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import { resolve } from "node:path"
import { matchNavigationRoute, decideVerb } from "./navigation"

const MAPA = {
  "sac":               "sac_ia",
  "sac.especialista":  "retencao_humano",
  "portabilidade":     "portabilidade_ia",
}

describe("matchNavigationRoute", () => {
  it("casa a PASTA e cobre a subárvore — folha nova não exige entrada nova", () => {
    expect(matchNavigationRoute(MAPA, "sac.info_plano")).toEqual({ pool: "sac_ia", matched_key: "sac" })
    expect(matchNavigationRoute(MAPA, "sac.status_servico")).toEqual({ pool: "sac_ia", matched_key: "sac" })
  })

  it("a entrada MAIS ESPECÍFICA vence a genérica", () => {
    expect(matchNavigationRoute(MAPA, "sac.especialista"))
      .toEqual({ pool: "retencao_humano", matched_key: "sac.especialista" })
  })

  it("casa a folha de primeiro nível", () => {
    expect(matchNavigationRoute(MAPA, "portabilidade"))
      .toEqual({ pool: "portabilidade_ia", matched_key: "portabilidade" })
  })

  it("prefixo é por SEGMENTO: `sac_premium` NÃO casa com `sac`", () => {
    // `startsWith("sac")` diria que sim, e o contato iria para `sac_ia`.
    expect(matchNavigationRoute(MAPA, "sac_premium")).toBeNull()
    expect(matchNavigationRoute(MAPA, "sac_premium.info")).toBeNull()
  })

  it("caminho não mapeado devolve null — nunca um pool plausível", () => {
    expect(matchNavigationRoute(MAPA, "reembolso")).toBeNull()
    expect(matchNavigationRoute(MAPA, "nao_se_aplica")).toBeNull()
  })

  it("mapa vazio devolve null", () => {
    expect(matchNavigationRoute({}, "sac.info_plano")).toBeNull()
  })

  it("valor vazio na chave NÃO conta como rota — e a busca CONTINUA subindo", () => {
    // Uma entrada apagada pela metade na UI não pode virar um pool "" bem-formado,
    // nem pode encobrir a pasta que ainda tem destino válido.
    expect(matchNavigationRoute({ "sac": "sac_ia", "sac.info_plano": "   " }, "sac.info_plano"))
      .toEqual({ pool: "sac_ia", matched_key: "sac" })
  })

  it("caminho vazio devolve null — não existe rota para 'nenhum caminho'", () => {
    expect(matchNavigationRoute(MAPA, "")).toBeNull()
  })
})

// ── decideVerb — quem pode receber `delegate` (RET-02) ──────────────────────
//
// ⚠️ O caso que carrega peso e a RECUSA POR DEFAULT: delegar e suspender o
// chamador, entao qualquer duvida tem de resolver para `escalate`. Um teste que
// so provasse o caminho feliz deixaria passar exatamente o defeito que importa —
// um `delegate` para quem nao devolve pendura o contato ate o `timeout_hours`,
// com o cliente vendo o especialista atender e nada ficando vermelho.

describe("decideVerb — o verbo e DERIVADO do que esta promovido", () => {
  const passo = (o: Record<string, unknown>) => o

  it("snapshot que devolve o controle ⇒ delegate", () => {
    const v = decideVerb({ steps: [
      passo({ id: "a", type: "menu" }),
      passo({ id: "b", type: "invoke", tool: "workflow_resume" }),
    ] })
    expect(v).toEqual({ verb: "delegate", reason: "devolve_o_controle" })
  })

  it("snapshot que NAO invoca workflow_resume ⇒ escalate/nao_retorna", () => {
    const v = decideVerb({ steps: [passo({ id: "a", type: "menu" }), passo({ id: "b", type: "complete" })] })
    expect(v).toEqual({ verb: "escalate", reason: "nao_retorna" })
  })

  it("snapshot com `delegate` proprio ⇒ escalate/cadeia_delegate, mesmo devolvendo", () => {
    // O token e tag UNICA da sessao: a delegacao de dentro sobrescreve a de fora.
    // Devolver NAO salva este caso, e por isso a checagem da cadeia vem ANTES.
    const v = decideVerb({ steps: [
      passo({ id: "a", type: "delegate", pool: "x" }),
      passo({ id: "b", type: "invoke", tool: "workflow_resume" }),
    ] })
    expect(v).toEqual({ verb: "escalate", reason: "cadeia_delegate" })
  })

  it("sem deploy ⇒ escalate/sem_deploy (e o caso dos pools humanos)", () => {
    expect(decideVerb(null).reason).toBe("sem_deploy")
    expect(decideVerb(undefined).reason).toBe("sem_deploy")
    expect(decideVerb({}).reason).toBe("sem_deploy")
    expect(decideVerb({ steps: [] }).reason).toBe("sem_deploy")
  })

  it("entrada de formato inesperado NUNCA vira delegate", () => {
    for (const lixo of ["", 0, false, [], { steps: "nao-e-array" }, { steps: [null, 7, "x"] }]) {
      expect(decideVerb(lixo).verb).toBe("escalate")
    }
  })
})

// ── PARIDADE cross-language: os MESMOS vetores que o probe Python le ────────
//
// O criterio do verbo esta escrito duas vezes -- aqui em TypeScript e em
// `infra/test/_ret02_verb_probe.py`. Paridade PRESUMIDA entre linguagens e o
// defeito que o gate do channel-gateway existe para pegar, entao os dois leem o
// MESMO arquivo de vetores. Um caso novo obriga as duas casas a concordarem.

describe("decideVerb — vetores compartilhados com o probe Python", () => {
  const vetores = JSON.parse(
    readFileSync(
      resolve(__dirname, "../../../../infra/test/fixtures/verb_vectors.json"),
      "utf8",
    ),
  ).vetores as Array<{ nome: string; snapshot: unknown; verb: string; reason: string }>

  it("o arquivo de vetores existe e nao esta vazio", () => {
    expect(vetores.length).toBeGreaterThan(5)
  })

  for (const v of vetores) {
    it(`${v.nome} ⇒ ${v.verb}/${v.reason}`, () => {
      expect(decideVerb(v.snapshot)).toEqual({ verb: v.verb, reason: v.reason })
    })
  }
})
