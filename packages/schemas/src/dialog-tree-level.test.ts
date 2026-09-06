/**
 * `optionsAtPath` — a projecao de UM nivel da arvore de opcoes.
 *
 * ⚠️ O caso que sustenta esta funcao NAO e o feliz: e o **controle negativo** do
 * segmento desconhecido. Devolver a raiz ali seria o valor plausivel mais barato de
 * produzir — a tela voltaria ao menu principal, pareceria certa, e o cliente perderia
 * a navegacao sem nada ficar vermelho. Por isso ha um teste que so passa se a funcao
 * devolver VAZIO, e ele reprova se alguem trocar o `return` por um fallback.
 */

import { describe, expect, it } from "vitest"

import { optionsAtPath } from "./dialog-render"
import { DialogOptionSchema } from "./dialog"
import type { RenderOption } from "./dialog-render"

// Espelha a forma real publicada em `dialog_wrapup_arvore_v1`/navegacao: pasta tem
// `options`, folha NAO tem a chave (o `mapOptions` a omite quando vazia).
const ARVORE: RenderOption[] = [
  {
    id: "sac", label: "SAC",
    options: [
      { id: "info_plano",      label: "Informacoes do plano" },
      { id: "problema_tecnico", label: "Problema tecnico" },
    ],
  },
  { id: "portabilidade", label: "Portabilidade" },
  { id: "nao_se_aplica", label: "Nenhuma dessas" },
]

describe("optionsAtPath", () => {
  it("caminho VAZIO devolve a raiz, e a raiz nao e folha", () => {
    const r = optionsAtPath(ARVORE, [])
    expect(r.found).toBe(true)
    expect(r.is_leaf).toBe(false)
    expect(r.options.map(o => o.id)).toEqual(["sac", "portabilidade", "nao_se_aplica"])
  })

  it("pasta devolve os FILHOS, um nivel, sem aninhamento", () => {
    const r = optionsAtPath(ARVORE, ["sac"])
    expect(r.found).toBe(true)
    expect(r.is_leaf).toBe(false)
    expect(r.options.map(o => o.id)).toEqual(["info_plano", "problema_tecnico"])
    expect(r.options.every(o => o.options === undefined)).toBe(true)
  })

  it("folha na RAIZ e folha", () => {
    const r = optionsAtPath(ARVORE, ["portabilidade"])
    expect(r.found).toBe(true)
    expect(r.is_leaf).toBe(true)
    expect(r.options).toEqual([])
  })

  it("folha ANINHADA e folha, e o caminho volta ecoado", () => {
    const r = optionsAtPath(ARVORE, ["sac", "info_plano"])
    expect(r.found).toBe(true)
    expect(r.is_leaf).toBe(true)
    expect(r.path).toEqual(["sac", "info_plano"])
  })

  // ── O controle negativo ────────────────────────────────────────────────────
  it("segmento DESCONHECIDO nao encontra, e NAO degrada para a raiz", () => {
    const r = optionsAtPath(ARVORE, ["financeiro"])
    expect(r.found).toBe(false)
    expect(r.options).toEqual([])            // <- o que reprova o fallback para a raiz
    expect(r.is_leaf).toBe(false)            // nao encontrado != folha
  })

  it("segmento desconhecido DEPOIS de um valido tambem nao degrada", () => {
    const r = optionsAtPath(ARVORE, ["sac", "inexistente"])
    expect(r.found).toBe(false)
    expect(r.options).toEqual([])
  })

  it("descer ALEM de uma folha nao encontra — folha nao tem nivel", () => {
    const r = optionsAtPath(ARVORE, ["portabilidade", "qualquer"])
    expect(r.found).toBe(false)
    expect(r.options).toEqual([])
  })

  it("nao muta a arvore de entrada", () => {
    const antes = JSON.stringify(ARVORE)
    optionsAtPath(ARVORE, ["sac"])
    expect(JSON.stringify(ARVORE)).toBe(antes)
  })
})

// ── F2: o ponto e separador de CAMINHO, logo id nao pode conte-lo ────────────
//
// Tres mecanismos ja assumiam esta regra sem ninguem impo-la: `category_path` do
// Arc 12, o casamento por prefixo de `navigation_pools`, e o `chosen_id` pontuado
// que deixa um canal responder a arvore inteira num turno. Um id com ponto
// acrescentaria um segmento a serie SEM erro em lugar nenhum.
describe("id de opcao × separador de caminho", () => {
  it("RECUSA id com ponto — o mecanismo que os tres pressupunham", () => {
    const r = DialogOptionSchema.safeParse({
      id: "sac.info_plano", label: { "pt-BR": "x" },
    })
    expect(r.success).toBe(false)
  })

  it("aceita id normal — controle POSITIVO (senao o teste acima passa pelo motivo errado)", () => {
    const r = DialogOptionSchema.safeParse({ id: "info_plano", label: { "pt-BR": "x" } })
    expect(r.success).toBe(true)
  })

  it("um caminho pontuado projeta o MESMO nivel que os segmentos um a um", () => {
    // E a proposicao que o `chosen_id` pontuado precisa: turno unico e turno a
    // turno tem de aterrissar no mesmo lugar, senao o canal muda o resultado.
    const raizes = ARVORE
    const passo = optionsAtPath(raizes, ["sac", "info_plano"])
    const unico = optionsAtPath(raizes, "sac.info_plano".split("."))
    expect(unico).toEqual(passo)
    expect(unico.is_leaf).toBe(true)
  })
})
