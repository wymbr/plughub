/**
 * masking-policy.test.ts
 * Testes unitários para a política de mascaramento.
 * Garante que a regra de precedência (field-level > step-level) está correta
 * e que computeMaskedFieldIds gera o array esperado em todos os casos.
 */

import { describe, test, expect } from "vitest"
import { isFieldMasked, computeMaskedFieldIds } from "../masking-policy"
import type { MaskedFieldDef } from "../masking-policy"

// ── isFieldMasked ─────────────────────────────────────────────────────────────

describe("isFieldMasked", () => {
  // field.masked === true → sempre mascarado
  test("field.masked=true com step.masked=false → mascarado", () => {
    expect(isFieldMasked({ id: "f1", masked: true }, false)).toBe(true)
  })

  test("field.masked=true com step.masked=undefined → mascarado", () => {
    expect(isFieldMasked({ id: "f1", masked: true }, undefined)).toBe(true)
  })

  // field.masked === false → nunca mascarado, mesmo com step.masked=true
  test("field.masked=false com step.masked=true → NÃO mascarado (override)", () => {
    expect(isFieldMasked({ id: "f1", masked: false }, true)).toBe(false)
  })

  test("field.masked=false com step.masked=undefined → NÃO mascarado", () => {
    expect(isFieldMasked({ id: "f1", masked: false }, undefined)).toBe(false)
  })

  // field.masked === undefined → herda step.masked
  test("field.masked=undefined com step.masked=true → mascarado (herda)", () => {
    expect(isFieldMasked({ id: "f1" }, true)).toBe(true)
  })

  test("field.masked=undefined com step.masked=false → NÃO mascarado (herda)", () => {
    expect(isFieldMasked({ id: "f1" }, false)).toBe(false)
  })

  test("field.masked=undefined com step.masked=undefined → NÃO mascarado", () => {
    expect(isFieldMasked({ id: "f1" }, undefined)).toBe(false)
  })
})

// ── computeMaskedFieldIds ─────────────────────────────────────────────────────

describe("computeMaskedFieldIds", () => {

  // ── Sem fields declarados (interação text/button/list) ──────────────────────

  test("step.masked=true sem fields com implicitFieldId → retorna [implicitId]", () => {
    expect(computeMaskedFieldIds(true, undefined, "pin_input")).toEqual(["pin_input"])
  })

  test("step.masked=true sem fields sem implicitFieldId → retorna []", () => {
    expect(computeMaskedFieldIds(true, undefined)).toEqual([])
  })

  test("step.masked=false sem fields → retorna []", () => {
    expect(computeMaskedFieldIds(false, undefined, "pin_input")).toEqual([])
  })

  test("step.masked=undefined sem fields → retorna []", () => {
    expect(computeMaskedFieldIds(undefined, undefined, "output")).toEqual([])
  })

  test("fields=[] (array vazio) com step.masked=true → usa implicitFieldId ([] ≡ undefined)", () => {
    // [] tem length=0 (falsy) — tratado igual a undefined; usa o campo implícito se fornecido.
    expect(computeMaskedFieldIds(true, [], "output")).toEqual(["output"])
  })

  // ── Com fields declarados ───────────────────────────────────────────────────

  test("step.masked=true, todos os campos sem override → todos mascarados", () => {
    const fields: MaskedFieldDef[] = [
      { id: "cpf" },
      { id: "nome" },
    ]
    expect(computeMaskedFieldIds(true, fields)).toEqual(["cpf", "nome"])
  })

  test("step.masked=true, campo com masked=false → exclui do resultado", () => {
    const fields: MaskedFieldDef[] = [
      { id: "cpf" },
      { id: "nome", masked: false },  // override explícito
    ]
    expect(computeMaskedFieldIds(true, fields)).toEqual(["cpf"])
  })

  test("step.masked=false, campo com masked=true → inclui só esse campo", () => {
    const fields: MaskedFieldDef[] = [
      { id: "pin",  masked: true },   // override explícito
      { id: "nome" },
    ]
    expect(computeMaskedFieldIds(false, fields)).toEqual(["pin"])
  })

  test("step.masked=false, nenhum campo com masked=true → retorna []", () => {
    const fields: MaskedFieldDef[] = [
      { id: "nome" },
      { id: "email" },
    ]
    expect(computeMaskedFieldIds(false, fields)).toEqual([])
  })

  test("step.masked=undefined, nenhum campo com masked=true → retorna []", () => {
    const fields: MaskedFieldDef[] = [
      { id: "nome" },
    ]
    expect(computeMaskedFieldIds(undefined, fields)).toEqual([])
  })

  test("mix: step.masked=true + campo=false + campo=true + campo=undefined", () => {
    // Cenário form com campos heterogêneos:
    //   senha_atual: step.masked herda → mascarado
    //   nome:        masked=false → NÃO mascarado
    //   pin:         masked=true → mascarado
    //   email:       step.masked herda → mascarado
    const fields: MaskedFieldDef[] = [
      { id: "senha_atual" },
      { id: "nome",       masked: false },
      { id: "pin",        masked: true },
      { id: "email" },
    ]
    const result = computeMaskedFieldIds(true, fields)
    expect(result).toEqual(["senha_atual", "pin", "email"])
    expect(result).not.toContain("nome")
  })

  test("implicitFieldId é ignorado quando fields[] não está vazio", () => {
    const fields: MaskedFieldDef[] = [{ id: "cpf", masked: true }]
    const result = computeMaskedFieldIds(true, fields, "implicit_should_be_ignored")
    expect(result).toEqual(["cpf"])
    expect(result).not.toContain("implicit_should_be_ignored")
  })

  // ── Ordem de retorno preservada ─────────────────────────────────────────────

  test("preserva a ordem dos fields originais", () => {
    const fields: MaskedFieldDef[] = [
      { id: "z_campo" },
      { id: "a_campo" },
    ]
    expect(computeMaskedFieldIds(true, fields)).toEqual(["z_campo", "a_campo"])
  })
})
