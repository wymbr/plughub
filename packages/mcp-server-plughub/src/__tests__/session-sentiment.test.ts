/**
 * session-sentiment.test.ts
 * Unit tests for lib/session-sentiment.ts.
 *
 * Regressão de 2026-08-25: a Console exibia "Neutral" para um cliente medido em
 * -0.50. O cálculo lia uma fonte aposentada e fazia `?? 0`, o que converte
 * NÃO-MEDIDO num ponto legítimo da escala.
 *
 * O teste que carrega o peso é `mede 0.0 como MEDIDO` — é ele que separa as duas
 * coisas que o defeito confundia. Sem ele, um "conserto" por `|| null` passaria
 * verde e voltaria a apagar o cliente neutro de verdade.
 */

import { describe, it, expect, vi, afterEach } from "vitest"
import {
  SENTIMENT_CTX_TAG,
  parseCtxSentiment,
  computeTrend,
  sentimentFromCtxHash,
} from "../lib/session-sentiment"

/** ContextEntry como o ai-gateway o escreve. */
function entry(value: unknown): Record<string, string> {
  return {
    [SENTIMENT_CTX_TAG]: JSON.stringify({
      value,
      confidence: 0.8,
      source:     "ai_inferred:sentiment_emitter",
      visibility: "agents_only",
      updated_at: "2026-08-25T00:00:00Z",
    }),
  }
}

/** Uma tag qualquer que NÃO é a de sentimento — a testemunha. */
const OTHER_TAG = {
  "caller.customer_id": JSON.stringify({
    value: "cus_1", confidence: 1, source: "test",
    visibility: "agents_only", updated_at: "2026-08-25T00:00:00Z",
  }),
}

afterEach(() => vi.restoreAllMocks())

describe("parseCtxSentiment", () => {
  it("lê o value do ContextEntry", () => {
    expect(parseCtxSentiment(entry(-0.5))).toBe(-0.5)
  })

  it("mede 0.0 como MEDIDO — cliente neutro não é ausência", () => {
    // Este é o discriminador do arco inteiro. `0` é um ponto da escala; a
    // ausência é `null`. Confundir os dois nas duas direções foi o defeito.
    expect(parseCtxSentiment(entry(0))).toBe(0)
  })

  it("devolve null quando o ctx EXISTE mas a tag de sentimento não", () => {
    // Testemunha: é o estado de toda sessão que não passou pela fila.
    expect(parseCtxSentiment(OTHER_TAG)).toBeNull()
  })

  it("devolve null para hash vazio, nulo ou indefinido", () => {
    expect(parseCtxSentiment({})).toBeNull()
    expect(parseCtxSentiment(null)).toBeNull()
    expect(parseCtxSentiment(undefined)).toBeNull()
  })

  it("devolve null e LOGA quando o valor não é JSON", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {})
    expect(parseCtxSentiment({ [SENTIMENT_CTX_TAG]: "não é json" })).toBeNull()
    expect(warn).toHaveBeenCalled()   // degradação nunca é silenciosa
  })

  it("devolve null quando `value` não é número finito", () => {
    vi.spyOn(console, "warn").mockImplementation(() => {})
    expect(parseCtxSentiment(entry("-0.5"))).toBeNull()   // string, não número
    expect(parseCtxSentiment(entry(null))).toBeNull()
    expect(parseCtxSentiment(entry(NaN))).toBeNull()      // JSON.stringify(NaN) = "null"
  })

  it("aproveita escalar cru fora do contrato, mas denuncia", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {})
    expect(parseCtxSentiment({ [SENTIMENT_CTX_TAG]: "-0.42" })).toBe(-0.42)
    expect(warn).toHaveBeenCalled()
  })
})

describe("computeTrend", () => {
  it("é null com menos de 3 pontos — 'stable' seria invenção", () => {
    expect(computeTrend([])).toBeNull()
    expect(computeTrend([0.1])).toBeNull()
    expect(computeTrend([0.1, 0.2])).toBeNull()
  })

  it("classifica subida, descida e platô", () => {
    expect(computeTrend([-0.5, 0.0, 0.5])).toBe("improving")
    expect(computeTrend([0.5, 0.0, -0.5])).toBe("declining")
    expect(computeTrend([0.1, 0.1, 0.1])).toBe("stable")
  })
})

describe("sentimentFromCtxHash", () => {
  it("monta a view medida, com trajetória vazia (não há produtor de histórico)", () => {
    expect(sentimentFromCtxHash(entry(-0.5))).toEqual({
      current:    -0.5,
      trajectory: [],
      trend:      null,
      // -0.5 NÃO é < -0.5 — o limiar é estrito; ver o teste dedicado abaixo.
      alert:      false,
    })
  })

  it("sem medição: current null, alert false", () => {
    expect(sentimentFromCtxHash(OTHER_TAG)).toEqual({
      current:    null,
      trajectory: [],
      trend:      null,
      alert:      false,
    })
  })

  it("alert é ESTRITO — o limiar não dispara nele mesmo", () => {
    // Comportamento PRESERVADO do código original (`current < -0.5`), não uma
    // escolha nova: o conserto trocou a fonte, não a régua.
    expect(sentimentFromCtxHash(entry(-0.4)).alert).toBe(false)
    expect(sentimentFromCtxHash(entry(-0.5)).alert).toBe(false)
    expect(sentimentFromCtxHash(entry(-0.51)).alert).toBe(true)
  })

  it("nunca alerta sobre ausência", () => {
    expect(sentimentFromCtxHash({}).alert).toBe(false)
  })

  it("devolve trajetória própria, não a constante compartilhada", () => {
    const view = sentimentFromCtxHash(entry(0.2))
    view.trajectory.push(1)   // não pode contaminar a próxima chamada
    expect(sentimentFromCtxHash(entry(0.2)).trajectory).toEqual([])
  })

  it("aceita trajetória injetada e deriva o trend dela", () => {
    // O parâmetro existe para que o dia do produtor de histórico mude 1 lugar.
    const view = sentimentFromCtxHash(entry(0.5), [-0.5, 0.0, 0.5])
    expect(view.trajectory).toEqual([-0.5, 0.0, 0.5])
    expect(view.trend).toBe("improving")
  })
})
