/**
 * output-masking.test.ts — R7a
 *
 * Garante que o output_snapshot é mascarado simetricamente ao input: PII detectada
 * por padrão (DEFAULT_MASKING_RULES) nunca persiste crua; os paths e categorias são
 * registrados; conteúdo não-PII é preservado (faithfulness-vs-ferramenta não-PII).
 */
import { describe, it, expect } from "vitest"
import { maskOutputForAudit } from "../mcp-interceptor"

describe("maskOutputForAudit (R7a)", () => {
  it("masks CPF in a string field and records path + category", () => {
    const r = maskOutputForAudit({ doc: "CPF 123.456.789-09 confirmado" })
    expect(String((r.value as any).doc)).not.toContain("123.456.789-09")
    expect(r.fields).toContain("doc")
    expect(r.categories).toContain("cpf")
  })

  it("masks email and credit card", () => {
    const r = maskOutputForAudit({
      contato: "fale com joao@exemplo.com",
      cartao:  "4111 1111 1111 1111",
    })
    expect(String((r.value as any).contato)).not.toContain("joao@exemplo.com")
    expect(String((r.value as any).cartao)).not.toContain("4111 1111 1111 1111")
    expect(r.categories).toEqual(expect.arrayContaining(["email_addr", "credit_card"]))
    expect(r.fields).toEqual(expect.arrayContaining(["contato", "cartao"]))
  })

  it("preserves non-PII content (only PII is masked)", () => {
    const r = maskOutputForAudit({ saldo: "Seu saldo é R$ 500,00", status: "ativo" })
    expect((r.value as any).saldo).toBe("Seu saldo é R$ 500,00")
    expect((r.value as any).status).toBe("ativo")
    expect(r.fields).toEqual([])
    expect(r.categories).toEqual([])
  })

  it("walks nested objects and arrays, recording dot/bracket paths", () => {
    const r = maskOutputForAudit({
      cliente: { emails: ["a@b.com", "sem-pii"] },
    })
    const emails = (r.value as any).cliente.emails
    expect(emails[0]).not.toContain("a@b.com")
    expect(emails[1]).toBe("sem-pii")
    expect(r.fields).toContain("cliente.emails[0]")
    expect(r.fields).not.toContain("cliente.emails[1]")
  })

  it("masks a bare PII string (path '$')", () => {
    const r = maskOutputForAudit("meu telefone é (11) 98765-4321")
    expect(r.value).not.toContain("98765-4321")
    expect(r.fields).toEqual(["$"])
    expect(r.categories).toContain("phone")
  })

  it("leaves primitives and null untouched", () => {
    expect(maskOutputForAudit(42).value).toBe(42)
    expect(maskOutputForAudit(null).value).toBe(null)
    expect(maskOutputForAudit(true).fields).toEqual([])
  })
})
