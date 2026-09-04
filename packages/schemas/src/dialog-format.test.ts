/**
 * dialog-format.test.ts — catálogo de formatos: oráculo, interpretador, herança.
 *
 * O que este arquivo NÃO faz: reimplementar as regras que julga. Os vetores vêm
 * das próprias entradas (`vectors`) e o veredicto vem de `verifyDialogFormatCatalog`
 * — se o teste reconstruísse a regra, estaria testando a si mesmo.
 */
import { describe, expect, it } from "vitest"
import {
  DEFAULT_DIALOG_FORMAT_CATALOG,
  DialogFormatCatalogSchema,
  formatForMaskedType,
  resolveFormatMask,
  validateDialogFormat,
  verifyDialogFormatCatalog,
  type DialogFormatCatalog,
} from "./dialog-format"
import { DEFAULT_DATA_TYPE_CATALOG } from "./audit"

describe("catálogo semeado", () => {
  it("passa no próprio oráculo, com testemunha de presença", () => {
    const r = verifyDialogFormatCatalog()
    // A testemunha primeiro: listas vazias sobre catálogo vazio não é aprovação.
    expect(r.declared).toBeGreaterThan(10)
    expect(r.dangling_masked_ref).toEqual([])
    expect(r.ambiguous_masked_ref).toEqual([])
    expect(r.duplicated_mask).toEqual([])
    expect(r.unanchored_shape).toEqual([])
    expect(r.invalid_shape).toEqual([])
    expect(r.inert_entry).toEqual([])
    expect(r.vector_mismatch).toEqual([])
    expect(r.duplicate_id).toEqual([])
  })

  it("é um DialogFormatCatalog válido", () => {
    expect(DialogFormatCatalogSchema.safeParse(DEFAULT_DIALOG_FORMAT_CATALOG).success).toBe(true)
  })

  it("todo vetor declarado é exercido pelo interpretador", () => {
    let n = 0
    for (const f of DEFAULT_DIALOG_FORMAT_CATALOG.formats) {
      for (const v of f.vectors.valid) {
        expect(validateDialogFormat(v, f.id), `${f.id} ← ${JSON.stringify(v)}`).toMatchObject({ ok: true })
        n++
      }
      for (const v of f.vectors.invalid) {
        expect(validateDialogFormat(v, f.id).ok, `${f.id} ← ${JSON.stringify(v)}`).toBe(false)
        n++
      }
    }
    // Sem isto, um catálogo que perdesse todos os vetores passaria neste teste.
    expect(n).toBeGreaterThan(50)
  })
})

describe("os DOIS níveis de veredicto (D4)", () => {
  // O par que prova que forma e validade não são o mesmo fato: os dois casam a
  // regex e só o segundo é recusado — por semântica.
  it.each([
    ["date_br", "01/01/2026", "31/02/2026"],
    ["date_br", "29/02/2024", "29/02/2026"],
    ["cpf", "529.982.247-25", "000.000.000-00"],
    ["credit_card", "4539 1488 0343 6467", "4539 1488 0343 6468"],
    ["card_expiry", "12/30", "13/26"],
  ])("%s: %s passa, %s casa a forma e falha a semântica", (fmt, bom, mau) => {
    expect(validateDialogFormat(bom, fmt).ok).toBe(true)
    const r = validateDialogFormat(mau, fmt)
    expect(r.ok).toBe(false)
    expect(r.reason).toBe("semantic")
  })

  it("recusa por FORMA nomeia forma, não semântica", () => {
    const r = validateDialogFormat("1/1/2026", "date_br")
    expect(r).toMatchObject({ ok: false, reason: "shape" })
    expect(r.error).toBeDefined()   // a recusa carrega o que dizer ao cliente
  })
})

describe("formato desconhecido RECUSA — nunca libera", () => {
  it("nome fora do catálogo é unknown_format", () => {
    expect(validateDialogFormat("qualquer", "nao_existe")).toMatchObject({
      ok: false, reason: "unknown_format",
    })
  })
})

describe("herança de máscara (D3) e derivação por masked (D8)", () => {
  it("entrada com from_masked_type herda a máscara de masking.types", () => {
    const cpf = DEFAULT_DIALOG_FORMAT_CATALOG.formats.find(f => f.id === "cpf")!
    expect(cpf.affordance.mask).toBeUndefined()          // não declara a própria
    const herdada = resolveFormatMask(cpf)
    const doMasked = DEFAULT_DATA_TYPE_CATALOG.types.find(t => t.id === "cpf")!.formato.display
    expect(herdada).toBe(doMasked)
    expect(herdada).toBeTruthy()                          // testemunha: herdou ALGO
  })

  it("entrada autossuficiente usa a própria máscara", () => {
    const d = DEFAULT_DIALOG_FORMAT_CATALOG.formats.find(f => f.id === "date_br")!
    expect(resolveFormatMask(d)).toBe("##/##/####")
  })

  it("masked: cpf deriva o formato cpf", () => {
    expect(formatForMaskedType("cpf")?.id).toBe("cpf")
  })

  it("ids que NÃO coincidem também resolvem — é para isso que o vínculo existe", () => {
    // O tipo mascarado é `email_addr`; o formato é `email`. Igualdade de nome
    // não resolveria, e é por isso que o vínculo é campo declarado.
    expect(formatForMaskedType("email_addr")?.id).toBe("email")
    expect(formatForMaskedType("phone")?.id).toBe("phone_br")
  })

  it("tipo que mascara sem formatar devolve undefined — desfecho legítimo", () => {
    // Eixos ortogonais: `credential` e `opaque` mascaram e não formatam. Isto é
    // um ramo VERDE; tratá-lo como falha empurraria alguém a inventar formato.
    expect(formatForMaskedType("credential")).toBeUndefined()
    expect(formatForMaskedType("opaque")).toBeUndefined()
  })
})

// ─────────────────────────────────────────────
// Falseabilidade do oráculo — cada ramo tem de saber REPROVAR
// ─────────────────────────────────────────────

const clone = (): DialogFormatCatalog =>
  JSON.parse(JSON.stringify(DEFAULT_DIALOG_FORMAT_CATALOG)) as DialogFormatCatalog

describe("o oráculo reprova (bateria de mutação)", () => {
  it("from_masked_type inexistente", () => {
    const c = clone()
    c.formats.find(f => f.id === "cpf")!.from_masked_type = "nao_existe"
    expect(verifyDialogFormatCatalog(c).dangling_masked_ref).toContain("cpf")
  })

  it("dois formatos para o mesmo tipo mascarado (D8 ficaria ambígua)", () => {
    const c = clone()
    c.formats.find(f => f.id === "digits")!.from_masked_type = "cpf"
    expect(verifyDialogFormatCatalog(c).ambiguous_masked_ref).toContain("cpf")
  })

  it("máscara própria junto de from_masked_type (a duplicação que a D3 proíbe)", () => {
    const c = clone()
    c.formats.find(f => f.id === "cpf")!.affordance.mask = "###.###.###-##"
    expect(verifyDialogFormatCatalog(c).duplicated_mask).toContain("cpf")
  })

  it("shape sem âncora — finder disfarçado de validador", () => {
    const c = clone()
    c.formats.find(f => f.id === "digits")!.verdict.shape = "[0-9]+"
    expect(verifyDialogFormatCatalog(c).unanchored_shape).toContain("digits")
  })

  it("shape que não compila", () => {
    const c = clone()
    c.formats.find(f => f.id === "digits")!.verdict.shape = "^[0-9$"
    expect(verifyDialogFormatCatalog(c).invalid_shape).toContain("digits")
  })

  it("entrada inerte — nem guia nem julga", () => {
    const c = clone()
    c.formats.push({
      id: "fantasma", affordance: {}, verdict: { semantic: "none" },
      vectors: { valid: [], invalid: [] },
    })
    expect(verifyDialogFormatCatalog(c).inert_entry).toContain("fantasma")
  })

  it("vetor que discorda do próprio veredicto", () => {
    const c = clone()
    c.formats.find(f => f.id === "date_br")!.vectors.valid.push("31/02/2026")
    expect(verifyDialogFormatCatalog(c).vector_mismatch).toContain("date_br")
  })

  it("id repetido", () => {
    const c = clone()
    c.formats.push({ ...c.formats[0] })
    expect(verifyDialogFormatCatalog(c).duplicate_id).toContain(c.formats[0].id)
  })

  it("catálogo VAZIO não passa por 'nenhum problema encontrado'", () => {
    // Todas as listas ficam vazias — e é exatamente por isso que `declared`
    // viaja junto. Sem ele, apagar o catálogo seria indistinguível de consertá-lo.
    const r = verifyDialogFormatCatalog({ formats: [] })
    expect(r.dangling_masked_ref).toEqual([])
    expect(r.declared).toBe(0)
  })
})
