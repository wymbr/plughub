/**
 * masking-policy.test.ts
 * Testes unitários para a política de mascaramento.
 * Garante que a regra de precedência (field-level > step-level) está correta
 * e que computeMaskedFieldIds gera o array esperado em todos os casos.
 */

import { describe, test, it, expect } from "vitest"
import { isFieldMasked, computeMaskedFieldIds, resolveMaskedFields, maskedFieldType, isStepMasked } from "../masking-policy"
import { OPAQUE_DATA_TYPE_ID, MaskedDeclarationSchema } from "@plughub/schemas"
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

// ─────────────────────────────────────────────────────────────────────────────
// T2 — a declaração passa a ser `boolean | string` (id de tipo do catálogo).
//
// O caso que mais importa aqui é o FAIL-OPEN: antes da T2, todo call site testava
// `masked === true`, e um `masked: "cpf"` daria `false` — o campo sairia DESmascarado.
// Cada teste abaixo que usa string reprova essa regressão.
// ─────────────────────────────────────────────────────────────────────────────

describe("maskedFieldType — o tipo efetivo", () => {
  it("string no campo é o tipo, e vence o step", () => {
    expect(maskedFieldType({ id: "cpf", masked: "cpf" }, undefined)).toBe("cpf")
    expect(maskedFieldType({ id: "cpf", masked: "cpf" }, false)).toBe("cpf")
    expect(maskedFieldType({ id: "cpf", masked: "cpf" }, "credit_card")).toBe("cpf")
  })

  it("`true` NÃO é 'sem tipo' — resolve para o mais restritivo", () => {
    expect(maskedFieldType({ id: "senha", masked: true }, undefined)).toBe(OPAQUE_DATA_TYPE_ID)
    expect(maskedFieldType({ id: "x" }, true)).toBe(OPAQUE_DATA_TYPE_ID)
  })

  it("`false` no campo continua vencendo o step, inclusive step tipado", () => {
    expect(maskedFieldType({ id: "email", masked: false }, "cpf")).toBeNull()
    expect(maskedFieldType({ id: "email", masked: false }, true)).toBeNull()
  })

  it("campo ausente herda o TIPO do step, não só o booleano", () => {
    expect(maskedFieldType({ id: "x" }, "credit_card")).toBe("credit_card")
    expect(maskedFieldType({ id: "x" }, undefined)).toBeNull()
    expect(maskedFieldType({ id: "x" }, false)).toBeNull()
  })

  it("string vazia falha FECHADO (opaque), nunca aberto", () => {
    // Declaração malformada: o deploy recusa (D3); em runtime o lado seguro é o
    // restritivo. Devolver null aqui seria fail-open no único ponto onde degradar vaza.
    expect(maskedFieldType({ id: "x", masked: "" }, undefined)).toBe(OPAQUE_DATA_TYPE_ID)
    expect(maskedFieldType({ id: "x", masked: "   " }, undefined)).toBe(OPAQUE_DATA_TYPE_ID)
  })
})

describe("isStepMasked — o booleano derivado (contrato do bridge)", () => {
  it("é verdadeiro para step tipado — a regressão que o `=== true` causaria", () => {
    expect(isStepMasked("cpf")).toBe(true)
    expect(isStepMasked(true)).toBe(true)
  })
  it("é falso só para ausente e para `false`", () => {
    expect(isStepMasked(undefined)).toBe(false)
    expect(isStepMasked(false)).toBe(false)
  })
})

describe("resolveMaskedFields — ids e tipos numa passada", () => {
  it("devolve os dois, e os ids continuam iguais aos de computeMaskedFieldIds", () => {
    const fields = [
      { id: "email", masked: false },
      { id: "cpf",   masked: "cpf" },
      { id: "senha", masked: true },
      { id: "livre" },
    ]
    const r = resolveMaskedFields("credit_card", fields)
    expect(r.ids).toEqual(["cpf", "senha", "livre"])
    expect(r.types).toEqual({
      cpf:   "cpf",
      senha: OPAQUE_DATA_TYPE_ID,
      livre: "credit_card",          // herdou o TIPO do step
    })
    // a derivação não pode divergir da fonte
    expect(computeMaskedFieldIds("credit_card", fields)).toEqual(r.ids)
  })

  it("campo implícito herda o tipo do step (interação sem fields[])", () => {
    expect(resolveMaskedFields("cpf", undefined, "output")).toEqual({
      ids: ["output"], types: { output: "cpf" },
    })
    expect(resolveMaskedFields(true, undefined, "pin")).toEqual({
      ids: ["pin"], types: { pin: OPAQUE_DATA_TYPE_ID },
    })
    expect(resolveMaskedFields(false, undefined, "pin")).toEqual({ ids: [], types: {} })
  })
})

// ─────────────────────────────────────────────────────────────────────────────
// T7-A — a ESCRITA fecha para `true`; o RUNTIME segue tolerando.
//
// A assimetria é o ponto, e estes testes existem para que ela não seja "limpa"
// por engano: quem vir `if (d === true)` na masking-policy depois de o schema já
// recusar `true` vai achar que é resíduo. Não é — é o que mantém executável um
// snapshot de slot `previous` anterior à T6, que não passa por Zod na execução.
// ─────────────────────────────────────────────────────────────────────────────

describe("T7-A — assimetria escrita × runtime", () => {
  it("a ESCRITA recusa `true` (a declaração anônima morreu na porta)", () => {
    expect(MaskedDeclarationSchema.safeParse(true).success).toBe(false)
  })

  it("a escrita ACEITA `false` e id de tipo — `false` é capacidade, não legado", () => {
    // `false` tem zero usos no parque e mesmo assim fica: é a única forma de dizer
    // "este campo NÃO é mascarado, mesmo que o step mascare".
    expect(MaskedDeclarationSchema.safeParse(false).success).toBe(true)
    expect(MaskedDeclarationSchema.safeParse("cpf").success).toBe(true)
  })

  it("o RUNTIME ainda resolve `true` → opaque (snapshot antigo segue executável)", () => {
    // Se este teste ficar vermelho, um rollback para deploy pré-T6 passa a estourar
    // com TypeError no meio de um atendimento — o `d.trim()` sobre um booleano.
    expect(maskedFieldType({ id: "x", masked: true }, undefined)).toBe(OPAQUE_DATA_TYPE_ID)
    expect(maskedFieldType({ id: "x" }, true)).toBe(OPAQUE_DATA_TYPE_ID)
    expect(isStepMasked(true)).toBe(true)
  })
})
