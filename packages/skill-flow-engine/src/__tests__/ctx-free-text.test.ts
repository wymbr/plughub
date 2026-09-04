/**
 * ctx-free-text.test.ts — F5 (§D12): a REDE sobre `$.pipeline_state.*`.
 *
 * ⚠️ **A rede é MITIGAÇÃO, e metade destes casos existe para provar isso.** A garantia
 * vem de declarar o campo num `DialogForm`; um teste que só mostrasse a rede pegando
 * PII deixaria a impressão de cobertura, que é o anestésico que a §D12 nomeia. Por isso
 * há caso para o que ela NÃO pega, e ele é tão importante quanto o resto.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import { filtrarTextoLivre } from "../ctx-audit"

const CLIENTE  = { stepType: "notify", visibility: "all",         stepId: "avisar" }
const OPERADOR = { stepType: "notify", visibility: "agents_only", stepId: "nota" }
const SISTEMA  = { stepType: "invoke",                            stepId: "crm" }

const info = () => vi.spyOn(console, "info").mockImplementation(() => {})

describe("a rede PEGA o que é reconhecível por FORMA", () => {
  let i: ReturnType<typeof info>
  beforeEach(() => { i = info() })
  afterEach(() => { i.mockRestore() })

  it.each([
    ["cartão",   "Seu cartão 1111 2222 3333 4444 foi aprovado"],
    ["CPF",      "CPF 529.883.653-09 confirmado"],
    ["telefone", "Ligamos para (11) 98765-4321"],
    ["e-mail",   "Enviado para maria@exemplo.com"],
  ])("%s em texto livre não sai inteiro ao cliente", (_rot, texto) => {
    const fora = String(filtrarTextoLivre(texto, CLIENTE, "$.pipeline_state.x"))
    expect(fora).not.toBe(texto)
    expect(i.mock.calls.flat().join(" ")).toContain("REDE")
  })

  it("anda dentro de objeto e de array, não só na raiz", () => {
    const fora = filtrarTextoLivre(
      { itens: [{ nota: "cpf 529.883.653-09" }] }, CLIENTE, "$.pipeline_state.x")
    expect(JSON.stringify(fora)).not.toContain("529.883.653-09")
  })
})

describe("a rede NÃO é cobertura — os limites são o produto (§D12)", () => {
  it("tipo sem `detect_pattern` passa INTEIRO: 11 dos 15 não são detectáveis", () => {
    // `card_expiry` não tem padrão POR DECISÃO — `\d{2}/\d{2}` casaria qualquer data.
    // Este caso existe para que ninguém leia a rede como garantia.
    const texto = "Validade 04/29"
    expect(filtrarTextoLivre(texto, CLIENTE, "$.pipeline_state.x")).toBe(texto)
  })

  it("sensível por CONTEXTO, não por forma, passa inteiro", () => {
    // Nenhuma regex reconhece um endereço ou um diagnóstico. É o argumento de produto
    // do dono: não capturar em texto livre — a rede não substitui isso.
    const texto = "Mora na Rua das Flores 123 e trata hipertensão"
    expect(filtrarTextoLivre(texto, CLIENTE, "$.pipeline_state.x")).toBe(texto)
  })
})

describe("o que a rede não pode ESTRAGAR", () => {
  it("valor JÁ mascarado atravessa intacto — idempotência (§D12)", () => {
    // É o que dispensou o carimbo de proveniência. `pendencia.context.*` nasce
    // mascarado; sem esta propriedade a F5 produziria `*****4444**`.
    for (const m of ["***4444", "**** **** **** ****", "***.***.***.--",
                     "(##) ****-4321", "m***@exemplo.com"]) {
      expect(filtrarTextoLivre(`Cartão: ${m}`, CLIENTE, "$.x")).toBe(`Cartão: ${m}`)
    }
  })

  it("ao SISTEMA o valor sai INTEIRO — controle positivo", () => {
    // Sem este caso, uma rede que mascarasse tudo passaria em todos os outros. E o
    // `invoke` quebraria: o CRM precisa do número.
    const texto = "1111 2222 3333 4444"
    expect(filtrarTextoLivre(texto, SISTEMA, "$.x")).toBe(texto)
  })

  it("ao OPERADOR mascara — ele é plateia de gente", () => {
    expect(filtrarTextoLivre("cpf 529.883.653-09", OPERADOR, "$.x"))
      .not.toContain("529.883.653-09")
  })

  it("texto sem PII atravessa byte a byte", () => {
    const texto = "📋 Recebido! Vou registrar seu pedido. Um instante..."
    expect(filtrarTextoLivre(texto, CLIENTE, "$.x")).toBe(texto)
  })

  it("ausente e não-string atravessam sem tocar na rede", () => {
    expect(filtrarTextoLivre(undefined, CLIENTE, "$.x")).toBeUndefined()
    expect(filtrarTextoLivre("", CLIENTE, "$.x")).toBe("")
    expect(filtrarTextoLivre(42, CLIENTE, "$.x")).toBe(42)
  })

  it("sem sítio não há plateia, e sem plateia a rede não decide", () => {
    // O `interpolate` já guarda isto; o teste fixa o contrato da função.
    const texto = "1111 2222 3333 4444"
    expect(filtrarTextoLivre(texto, { stepType: "choice" }, "$.x")).toBe(texto)
  })
})
