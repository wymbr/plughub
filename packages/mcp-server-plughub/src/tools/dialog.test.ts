/**
 * ORQ-13 — a pergunta de esclarecimento oferece o que a ÁRVORE declara, não o que
 * o modelo disse.
 *
 * A proposição: *`clarifyOptions` devolve uma opção por caminho CONFERIDO, com o
 * rótulo da árvore, e nunca transforma um caminho inventado em botão.*
 *
 * O que faria isto ficar vermelho — e são os modos de falha que a ficha existe
 * para impedir:
 *
 *   · aceitar um caminho que não é folha declarada (o cliente escolheria um
 *     destino que a conferência da D6 depois recusaria, e o contato acabaria no
 *     escape DEPOIS de uma pergunta inútil);
 *   · fabricar rótulo a partir do caminho quando o vocabulário não veio (o
 *     cliente leria `sac.info_plano` como se fosse uma frase);
 *   · contar repetido como segundo candidato (uma opção não é escolha, e duas
 *     iguais são uma);
 *   · truncar em silêncio acima do teto de botões do canal.
 */
import { describe, it, expect } from "vitest"
import { clarifyOptions, CLARIFY_MAX_OPTIONS } from "./dialog"

const VOC = [
  { path: "sac.info_plano",       label: "Informações do plano" },
  { path: "sac.problema_tecnico", label: "Problema técnico" },
  { path: "aumento_limite",       label: "Aumento de limite" },
  { path: "portabilidade",        label: "Portabilidade" },
  { path: "nao_se_aplica",        label: "Nenhuma dessas" },
]

describe("clarifyOptions", () => {
  it("resolve os caminhos propostos com o RÓTULO da árvore, na ordem pedida", () => {
    const r = clarifyOptions(VOC, ["aumento_limite", "sac.info_plano"])
    expect(r.options).toEqual([
      { id: "aumento_limite", label: "Aumento de limite" },
      { id: "sac.info_plano", label: "Informações do plano" },
    ])
    expect(r.candidate_count).toBe(2)
    expect(r.candidates_dropped).toEqual([])
  })

  it("o `id` é o CAMINHO PONTUADO — é ele que o `chosen_id` sabe dividir", () => {
    // Fosse o id da folha (`info_plano`), a projeção o procuraria na RAIZ, não
    // acharia, e a navegação reiniciaria parecendo certa.
    expect(clarifyOptions(VOC, ["sac.info_plano"]).options[0].id).toBe("sac.info_plano")
  })

  it("caminho INVENTADO não vira botão — e é reportado, não engolido", () => {
    const r = clarifyOptions(VOC, ["sac.info_plano", "sac.cancelamento"])
    expect(r.options.map(o => o.id)).toEqual(["sac.info_plano"])
    expect(r.candidates_dropped).toEqual(["sac.cancelamento"])
    // e com UM só candidato o fluxo não pergunta: `gte 2` no `avaliar_esclarecimento`
    expect(r.candidate_count).toBe(1)
  })

  it("vocabulário AUSENTE devolve zero, nunca rótulo fabricado do caminho", () => {
    const r = clarifyOptions(undefined, ["sac.info_plano", "aumento_limite"])
    expect(r.options).toEqual([])
    expect(r.candidate_count).toBe(0)
    expect(r.candidates_dropped).toEqual(["sac.info_plano", "aumento_limite"])
  })

  it("repetido não é segundo candidato", () => {
    const r = clarifyOptions(VOC, ["aumento_limite", "aumento_limite"])
    expect(r.candidate_count).toBe(1)
    expect(r.candidates_dropped).toEqual([])
  })

  it("acima do teto do canal, trunca e DIZ o que ficou de fora", () => {
    const pedidos = ["sac.info_plano", "sac.problema_tecnico", "aumento_limite", "portabilidade"]
    const r = clarifyOptions(VOC, pedidos)
    expect(r.options).toHaveLength(CLARIFY_MAX_OPTIONS)
    expect(r.truncated).toEqual(["portabilidade"])
  })

  it("lista vazia é zero candidatos — o `exists` do choice não decide sozinho", () => {
    // O `triagem` testa `exists`, e `[]` existe. Quem recusa é este contador.
    const r = clarifyOptions(VOC, [])
    expect(r.candidate_count).toBe(0)
    expect(r.truncated).toEqual([])
  })
})
