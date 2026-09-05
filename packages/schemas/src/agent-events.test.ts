/**
 * agent-events.test.ts — o TETO de segmentos da `category` e o que
 * `decomposeCategoryLevels` promete (F4 do `adr-dialog-tree-options`).
 *
 * Estes casos existem porque o teto anterior (5 segmentos) era um **bloqueio**
 * disfarçado de limite: uma taxonomia de 3 níveis já dá 6 segmentos com o prefixo
 * `pool.skill.metric`, e o evento seria rejeitado pelo schema — a árvore ficaria
 * autorável e incontável ao mesmo tempo.
 */
import { describe, it, expect } from "vitest"
import {
  AGENT_EVENT_CATEGORY_REGEX,
  AGENT_EVENT_CATEGORY_MAX_SEGMENTS,
  decomposeCategoryLevels,
} from "./agent-events"
import { DIALOG_OPTION_MAX_DEPTH } from "./dialog"

const seg = (n: number) => Array.from({ length: n }, (_, i) => `s${i}`).join(".")

describe("AGENT_EVENT_CATEGORY — teto derivado, não escolhido", () => {
  it("o teto É o prefixo do emissor mais a profundidade máxima da árvore", () => {
    // Este é o MECANISMO que liga as duas constantes. Sem ele elas são duas casas
    // afirmando o mesmo número por prosa — e é assim que ele diverge: alguém sobe
    // a profundidade da taxonomia (D3) e a categoria passa a ser rejeitada em
    // runtime, longe daqui, sem nada apontar para cá.
    const PREFIXO_DO_EMISSOR = 3 // pool.skill.metric
    expect(AGENT_EVENT_CATEGORY_MAX_SEGMENTS).toBe(PREFIXO_DO_EMISSOR + DIALOG_OPTION_MAX_DEPTH)
  })

  it("aceita o caminho de taxonomia que a F1 tornou autorável", () => {
    // 3 níveis de taxonomia = 6 segmentos: REJEITADO pelo teto anterior.
    expect(AGENT_EVENT_CATEGORY_REGEX.test(
      "sac_humano.wrapup.motivo.financeiro.cobranca.indevida",
    )).toBe(true)
    // profundidade máxima = 8 segmentos, o limite exato
    expect(AGENT_EVENT_CATEGORY_REGEX.test(seg(AGENT_EVENT_CATEGORY_MAX_SEGMENTS))).toBe(true)
  })

  it("continua recusando o que sempre recusou (controle positivo)", () => {
    expect(AGENT_EVENT_CATEGORY_REGEX.test(seg(AGENT_EVENT_CATEGORY_MAX_SEGMENTS + 1))).toBe(false)
    expect(AGENT_EVENT_CATEGORY_REGEX.test("sozinho")).toBe(false)       // < 2 segmentos
    expect(AGENT_EVENT_CATEGORY_REGEX.test("Pool.Skill.Key")).toBe(false) // maiúsculas
    expect(AGENT_EVENT_CATEGORY_REGEX.test("pool..key")).toBe(false)      // segmento vazio
  })
})

describe("decomposeCategoryLevels — QUATRO níveis, declaradamente", () => {
  const funda = "sac_humano.wrapup.motivo.financeiro.cobranca.indevida"

  it("o 5º segmento em diante existe SÓ na `category` — e isso é o contrato", () => {
    const { l1, l2, l3, l4 } = decomposeCategoryLevels(funda)
    expect([l1, l2, l3, l4]).toEqual(["sac_humano", "wrapup", "motivo", "financeiro"])
    // O ponto do teste: `l4` NÃO é a folha, e ninguém deve "consertá-lo" para ser.
    // Com profundidade variável, `l4` de um ramo curto é folha e de um ramo longo é
    // intermediário — agregar por nível fixo somaria granularidades diferentes.
    expect(l4).not.toBe("indevida")
  })

  it("o recorte hierárquico é por PREFIXO da category completa (D10)", () => {
    expect(funda.startsWith("sac_humano.wrapup.motivo.financeiro.")).toBe(true)
    // e o prefixo respeita o SEGMENTO: um irmão de nome parecido não entra
    expect("sac_humano.wrapup.motivo.financeiro_avulso.x"
      .startsWith("sac_humano.wrapup.motivo.financeiro.")).toBe(false)
  })

  it("ramo curto continua decompondo como sempre (controle positivo)", () => {
    expect(decomposeCategoryLevels("pool.skill.fcr")).toEqual({
      l1: "pool", l2: "skill", l3: "fcr", l4: "",
    })
  })
})
