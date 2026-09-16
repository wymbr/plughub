/**
 * menu-collect.test.ts — VOZ-05 fatia 5a: contrato da coleta por voz e teclado no step `menu`.
 *
 * O que se decide aqui é o que o DEPLOY recusa: coleta em tempo real que nunca termina (inválido
 * ignorado sem timeout finito), parâmetro de dígito sem teclado, e dado protegido FALADO (NIV-08).
 * Cada recusa tem o controle positivo ao lado — um validador que recusa tudo passaria sozinho.
 */
import { describe, it, expect } from "vitest"
import { SkillFlowSchema, MenuStepSchema, menuCollectViolations } from "./skill"

const menu = (over: Record<string, unknown> = {}) => ({
  id: "m1", type: "menu", prompt: "Escolha uma opção", interaction: "button",
  options: [{ id: "a", label: "Fatura" }, { id: "b", label: "Outros" }],
  timeout_s: 60, on_success: "fim", on_failure: "fim", ...over,
})
const flow = (m: Record<string, unknown>) => ({
  entry: "m1", steps: [m, { id: "fim", type: "complete", outcome: "resolved" }],
})
const erros = (m: Record<string, unknown>) => {
  const r = SkillFlowSchema.safeParse(flow(m))
  return r.success ? [] : r.error.issues.map(i => i.message)
}

const DTMF = { input: ["dtmf", "voice"], first_input_timeout_s: 8, echo: "plain", max_invalid: 3 }

describe("MenuCollectSchema — o que o deploy aceita", () => {
  it("CONTROLE: menu de voz completo passa, com os defaults preenchidos", () => {
    expect(erros(menu({ collect: DTMF, on_invalid: "fim", on_timeout: "fim" }))).toEqual([])
    const parsed = MenuStepSchema.parse(menu({ collect: DTMF }))
    expect(parsed.collect?.barge_in).toBe(true)
  })

  it("CONTROLE: menu sem collect continua como sempre (inclusive espera infinita)", () => {
    expect(erros(menu({ timeout_s: -1 }))).toEqual([])
  })

  it("dtmf/voice sem first_input_timeout_s é recusado", () => {
    expect(erros(menu({ collect: { input: ["dtmf"] } })).join()).toMatch(/first_input_timeout_s/)
  })

  it.each([0, -1])("dtmf/voice com timeout_s %s (espera infinita) é recusado", (t) => {
    expect(erros(menu({ timeout_s: t, collect: DTMF })).join()).toMatch(/espera infinita/)
  })

  it("guarda do fluxo menor que o timeout do canal é recusada", () => {
    expect(erros(menu({ timeout_s: 5, collect: DTMF })).join()).toMatch(/guarda do fluxo/)
  })

  it("CONTROLE: coleta só por texto não exige timeout de canal", () => {
    expect(erros(menu({ timeout_s: -1, collect: { input: ["text"] } }))).toEqual([])
  })

  it("parâmetro de dígito sem dtmf é recusado, cada um nomeado", () => {
    const e = erros(menu({ collect: { input: ["voice"], first_input_timeout_s: 8, max_digits: 4, terminator: "#" } })).join()
    expect(e).toMatch(/max_digits só vale com input dtmf/)
    expect(e).toMatch(/terminator só vale com input dtmf/)
  })

  it("min_digits > max_digits é recusado; igual passa", () => {
    const base = { input: ["dtmf"], first_input_timeout_s: 8 }
    expect(erros(menu({ collect: { ...base, min_digits: 5, max_digits: 4 } })).join()).toMatch(/min_digits maior/)
    expect(erros(menu({ collect: { ...base, min_digits: 4, max_digits: 4 } }))).toEqual([])
  })

  it("domain text só por teclado é recusado; com voz passa", () => {
    expect(erros(menu({ collect: { input: ["dtmf"], first_input_timeout_s: 8, domain: "text" } })).join())
      .toMatch(/domain text/)
    expect(erros(menu({ collect: { input: ["voice"], first_input_timeout_s: 8, domain: "text" } }))).toEqual([])
  })

  it("domínio alfanumérico por teclado não existe no enum", () => {
    expect(erros(menu({ collect: { input: ["dtmf"], first_input_timeout_s: 8, domain: "alphanumeric" } })).length)
      .toBeGreaterThan(0)
  })

  it("on_invalid sem max_invalid é recusado (inválido ignorado nunca esgota)", () => {
    expect(erros(menu({ on_invalid: "fim", collect: { ...DTMF, max_invalid: undefined } })).join())
      .toMatch(/on_invalid exige max_invalid/)
    expect(erros(menu({ on_invalid: "fim" })).join()).toMatch(/on_invalid exige `collect`/)
  })

  it("NIV-08: menu mascarado com input voice é recusado — no step e no campo", () => {
    expect(erros(menu({ masked: "cpf", collect: DTMF })).join()).toMatch(/NIV-08/)
    const form = menu({ interaction: "form", options: undefined,
      fields: [{ id: "pin", label: "PIN", type: "text", masked: "pin" }], collect: DTMF })
    expect(erros(form).join()).toMatch(/NIV-08/)
  })

  it("CONTROLE NIV-08: mascarado só por teclado passa", () => {
    expect(erros(menu({ masked: "cpf", collect: { input: ["dtmf"], first_input_timeout_s: 8, echo: "masked" } })))
      .toEqual([])
  })

  it("a violação nomeia o step", () => {
    expect(erros(menu({ collect: { input: ["dtmf"] } }))[0]).toMatch(/^m1: /)
  })

  it("menuCollectViolations com timeout_s ref não afirma nada sobre a guarda (só em runtime)", () => {
    expect(menuCollectViolations({ timeout_s: "$.pipeline_state.t", collect: { ...DTMF, barge_in: true } as never }))
      .toEqual([])
  })
})
