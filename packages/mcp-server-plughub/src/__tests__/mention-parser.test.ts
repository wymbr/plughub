/**
 * mention-parser.test.ts
 * Unit tests for the @mention parser.
 * Spec: docs/guias/mention-protocol.md
 */

import { describe, it, expect } from "vitest"
import { parseMentions }        from "../lib/mention-parser"

describe("parseMentions", () => {

  // ── No mentions ──────────────────────────────────────────────────────────

  it("returns no mentions for plain text", () => {
    const r = parseMentions("olá, como posso ajudar?")
    expect(r.has_mentions).toBe(false)
    expect(r.mentions).toHaveLength(0)
    expect(r.stripped_text).toBe("olá, como posso ajudar?")
  })

  it("returns no mentions for empty string", () => {
    const r = parseMentions("")
    expect(r.has_mentions).toBe(false)
  })

  // ── @ctx.* references are NOT mentions ───────────────────────────────────

  it("does not treat @ctx.field as a mention", () => {
    const r = parseMentions("cliente @ctx.caller.nome quer portabilidade")
    expect(r.has_mentions).toBe(false)
  })

  it("does not treat @ctx.namespace.field as a mention", () => {
    const r = parseMentions("@ctx.session.pergunta tem valor")
    expect(r.has_mentions).toBe(false)
  })

  // ── Single mention ────────────────────────────────────────────────────────

  it("parses a bare mention", () => {
    const r = parseMentions("@billing preciso de ajuda")
    expect(r.has_mentions).toBe(true)
    expect(r.mentions).toHaveLength(1)
    expect(r.mentions[0]!.alias).toBe("billing")
  })

  it("parses mention at start of string", () => {
    const r = parseMentions("@copilot")
    expect(r.has_mentions).toBe(true)
    expect(r.mentions[0]!.alias).toBe("copilot")
    expect(r.mentions[0]!.args_raw).toBe("")
  })

  // ── Args extraction ───────────────────────────────────────────────────────

  it("captures key=value args", () => {
    const r = parseMentions("@billing conta=12345 motivo=portabilidade")
    expect(r.mentions[0]!.alias).toBe("billing")
    expect(r.mentions[0]!.args_raw).toBe("conta=12345 motivo=portabilidade")
  })

  it("captures @ctx.* inline references in args", () => {
    const r = parseMentions("@billing conta=@ctx.caller.account_id")
    expect(r.mentions[0]!.ctx_refs).toHaveLength(1)
    expect(r.mentions[0]!.ctx_refs[0]!.field).toBe("caller.account_id")
    expect(r.mentions[0]!.ctx_refs[0]!.fallback).toBe("")
  })

  it("captures @ctx.* with inline fallback", () => {
    const r = parseMentions('@copilot plano=@ctx.caller.plano_atual|"não identificado"')
    expect(r.mentions[0]!.ctx_refs[0]!.field).toBe("caller.plano_atual")
    expect(r.mentions[0]!.ctx_refs[0]!.fallback).toBe("não identificado")
  })

  it("captures multiple @ctx.* refs in same mention", () => {
    const r = parseMentions("@billing conta=@ctx.caller.account_id motivo=@ctx.caller.motivo_contato")
    expect(r.mentions[0]!.ctx_refs).toHaveLength(2)
    expect(r.mentions[0]!.ctx_refs[0]!.field).toBe("caller.account_id")
    expect(r.mentions[0]!.ctx_refs[1]!.field).toBe("caller.motivo_contato")
  })

  // ── Multi-mention ─────────────────────────────────────────────────────────

  it("parses two bare mentions", () => {
    const r = parseMentions("@billing @suporte analise o contexto")
    expect(r.has_mentions).toBe(true)
    expect(r.mentions).toHaveLength(2)
    expect(r.mentions[0]!.alias).toBe("billing")
    expect(r.mentions[1]!.alias).toBe("suporte")
  })

  it("assigns args to the correct mention in multi-mention", () => {
    const r = parseMentions("@billing conta=123 @suporte urgente=true")
    expect(r.mentions[0]!.alias).toBe("billing")
    expect(r.mentions[0]!.args_raw).toBe("conta=123")
    expect(r.mentions[1]!.alias).toBe("suporte")
    expect(r.mentions[1]!.args_raw).toBe("urgente=true")
  })

  it("parses three mentions in sequence", () => {
    const r = parseMentions("@billing @suporte @copilot analise")
    expect(r.mentions).toHaveLength(3)
    expect(r.mentions.map(m => m.alias)).toEqual(["billing", "suporte", "copilot"])
  })

  // ── stripped_text ─────────────────────────────────────────────────────────

  it("preserves text before first mention in stripped_text", () => {
    const r = parseMentions("urgente @billing analise a conta")
    expect(r.stripped_text).toContain("urgente")
  })

  it("preserves free prose from mention args in stripped_text", () => {
    const r = parseMentions("@billing analise a conta deste cliente")
    // "analise a conta deste cliente" is free text in args (no @ctx, no key=val)
    expect(r.stripped_text).toContain("analise a conta deste cliente")
  })

  it("strips @ctx.* from stripped_text but keeps free prose", () => {
    const r = parseMentions("@billing conta=@ctx.caller.account_id analise agora")
    expect(r.stripped_text).not.toContain("@ctx")
    expect(r.stripped_text).toContain("analise agora")
  })

  // ── Edge cases ────────────────────────────────────────────────────────────

  it("handles underscore in alias", () => {
    const r = parseMentions("@billing_ops verifica")
    expect(r.mentions[0]!.alias).toBe("billing_ops")
  })

  it("does not parse mid-word @ as mention", () => {
    // "user@example.com" — @ is preceded by non-whitespace
    const r = parseMentions("contato user@example.com para mais info")
    expect(r.has_mentions).toBe(false)
  })

  it("handles mention with no trailing text", () => {
    const r = parseMentions("por favor @billing")
    expect(r.has_mentions).toBe(true)
    expect(r.mentions[0]!.alias).toBe("billing")
    expect(r.mentions[0]!.args_raw).toBe("")
  })
})
