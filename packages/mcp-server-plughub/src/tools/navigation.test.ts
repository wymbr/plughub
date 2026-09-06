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
import { matchNavigationRoute } from "./navigation"

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
