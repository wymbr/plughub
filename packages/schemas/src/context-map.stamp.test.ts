/**
 * context-map.stamp.test.ts — ALW-02 passo 1: o CARIMBO (D9.6).
 *
 * ── Que proposição cada caso mede ────────────────────────────────────────────
 *
 * A D9.6 decide que **o escritor não declara nada**: o caminho de escrita carimba o
 * `atributo` a partir do cadastro. Três propriedades vêm junto — o autor não tem o que
 * errar, o dado guardado fica autodescritivo (F5 e export LGPD), e o tipo tem uma casa só.
 *
 * `stampContextEntry` é a metade PURA desse carimbo, e está aqui — e não no funil de
 * escrita — por um motivo de arco: é ela que o gêmeo Python vai espelhar no passo 2, e é
 * contra ela que o gate comparativo roda. Função pura é a única espécie que se compara
 * barato entre duas linguagens.
 *
 * Dois casos carregam o peso, e nenhum dos dois é o caminho feliz:
 *
 *   · **a ordem canônica-antes-de-alias** — é o único ramo em que uma reimplementação
 *     razoável diverge sem parecer errada. `resolveContextTag` documenta a ordem como
 *     deliberada; um gêmeo que consulte o alias primeiro deixa uma canônica ser sombreada
 *     por uma grafia legada, e nada fica vermelho até alguém contar a população.
 *   · **a ausência distinguível** — `atributo` ausente significa *não passou pelo funil*,
 *     e é assim que se mede se o choke point tem furo. Se `dynamic`/`unknown` não
 *     carimbassem nada, a entrada ficaria idêntica à de um `HSET` direto, e o furo que a
 *     D9.6 chama de silencioso continuaria silencioso — agora com um carimbo por perto
 *     dando a impressão de cobertura.
 */

import { describe, it, expect } from "vitest"
import {
  buildContextTagIndex,
  stampContextEntry,
  type ContextMap,
} from "./context-map"

/** Mapa mínimo — não usa o DEFAULT para que o teste não mude quando o mapa crescer. */
const MAPA: ContextMap = {
  mode: "audit",
  dynamic_prefixes: ["segment."],
  contexto: {
    session: {
      cliente: {
        cpf:      { tipo: "cpf_br", legado: ["caller.cpf"] },
        telefone: { tipo: "telefone_br" },
      },
      // `session.cliente.telefone` é canônica E aparece como `legado` do nó abaixo.
      // Colisão DELIBERADA: é o caso da ordem.
      contato: {
        fone_antigo: { tipo: "opaque", legado: ["session.cliente.telefone"] },
      },
    },
  },
} as ContextMap

const INDEX = buildContextTagIndex(MAPA)

/** ContextEntry como qualquer escritor a monta, ANTES do carimbo. */
function entrada(): Record<string, unknown> {
  return {
    value:      "123.456.789-00",
    confidence: 1.0,
    source:     "test",
    visibility: "agents_only",
    updated_at: "2026-09-02T00:00:00.000Z",
  }
}

describe("stampContextEntry — o que carimba", () => {
  it("canônica: carimba o tipo do cadastro", () => {
    const out = stampContextEntry(entrada(), "session.cliente.cpf", INDEX, false)
    expect(out["atributo"]).toEqual({ tipo: "cpf_br", origem: "canonical" })
  })

  it("alias: carimba o tipo da CANÔNICA e diz qual é", () => {
    // O valor fica gravado sob a grafia legada (renomear quebraria os leitores);
    // o carimbo é o que torna a entrada autodescritiva apesar disso.
    const out = stampContextEntry(entrada(), "caller.cpf", INDEX, false)
    expect(out["atributo"]).toEqual({
      tipo:     "cpf_br",
      origem:   "alias",
      canonica: "session.cliente.cpf",
    })
  })

  it("dinâmica: carimba a ORIGEM sem tipo — o prefixo não declara folha", () => {
    const out = stampContextEntry(entrada(), "segment.seg_1.served_human", INDEX, false)
    expect(out["atributo"]).toEqual({ origem: "dynamic" })
  })

  it("desconhecida: carimba a ORIGEM sem tipo — não recusa, registra", () => {
    // D9/D3: runtime nunca rejeita. Rejeitar aqui trocaria vazamento por quebra muda.
    const out = stampContextEntry(entrada(), "session.nao.cadastrado", INDEX, false)
    expect(out["atributo"]).toEqual({ origem: "unknown" })
  })
})

describe("stampContextEntry — a ORDEM, que é onde um gêmeo diverge", () => {
  it("tag que é canônica E alias de outro nó resolve como CANÔNICA", () => {
    // `session.cliente.telefone` é canônica (tipo telefone_br) e está declarada como
    // `legado` de `session.contato.fone_antigo` (tipo opaque). Consultar o alias
    // primeiro daria `opaque` — máscara diferente, sem nada ficar vermelho.
    const out = stampContextEntry(entrada(), "session.cliente.telefone", INDEX, false)
    expect(out["atributo"]).toEqual({ tipo: "telefone_br", origem: "canonical" })
  })
})

describe("stampContextEntry — a ausência é o instrumento", () => {
  it("carimba SEMPRE que roda: `atributo` ausente ⇒ não passou pelo funil", () => {
    // É a única forma de medir se o choke point tem furo. Sem carimbo em
    // dynamic/unknown, a entrada fica idêntica à de um HSET direto.
    for (const tag of [
      "session.cliente.cpf", "caller.cpf",
      "segment.seg_1.x", "session.nao.cadastrado",
    ]) {
      expect(stampContextEntry(entrada(), tag, INDEX, false)).toHaveProperty("atributo")
    }
  })

  it("mapa EMBUTIDO marca `fallback: true` — o tipo é o que o código trouxe", () => {
    // Com o config-api fora, o carimbo continua acontecendo (recusar deixaria a
    // escrita refém da config), mas ele deixa de afirmar o que o TENANT declarou.
    // Para um export LGPD essa é exatamente a distinção que precisa sobreviver.
    const out = stampContextEntry(entrada(), "session.cliente.cpf", INDEX, true)
    expect(out["atributo"]).toEqual({
      tipo: "cpf_br", origem: "canonical", fallback: true,
    })
  })

  it("sem fallback a chave é AUSENTE, nunca `false`", () => {
    // Ausência é a codificação honesta e mantém a entrada pequena — ela vai para
    // toda folha de todo hash de ctx.
    const out = stampContextEntry(entrada(), "session.cliente.cpf", INDEX, false)
    expect(Object.keys(out["atributo"] as object)).not.toContain("fallback")
  })
})

describe("stampContextEntry — testemunha negativa", () => {
  it("não toca em nenhum campo do escritor", () => {
    const antes = entrada()
    const out   = stampContextEntry(entrada(), "session.cliente.cpf", INDEX, false)
    for (const k of ["value", "confidence", "source", "visibility", "updated_at"]) {
      expect(out[k]).toEqual(antes[k])
    }
  })

  it("não muta a entrada recebida", () => {
    // O escritor pode reusar o objeto num loop (`mapping=` com N tags) — mutar
    // faria a segunda tag herdar o carimbo da primeira.
    const orig = entrada()
    stampContextEntry(orig, "session.cliente.cpf", INDEX, false)
    expect(orig).not.toHaveProperty("atributo")
  })
})
