/**
 * transport-credential.test.ts — CAP-10 (2026-09-29)
 *
 * O transporte MCP (`/sse`, `/messages`) exige credencial de serviço e falha FECHADO.
 * Cada recusa tem a sua contraparte: um portão que recusasse tudo passaria em todo
 * "recusou", então a credencial certa tem de passar.
 */
import { describe, it, expect } from "vitest"
import { judgeTransportCredential } from "../lib/transport-credential"

describe("judgeTransportCredential", () => {
  it("controle positivo: a credencial certa passa", () => {
    expect(judgeTransportCredential("segredo", "segredo")).toBe("ok")
  })

  it.each([
    ["ausente",            undefined],
    ["vazia",              ""],
    ["errada, mesmo tamanho", "segredx"],
    ["prefixo da certa",   "segred"],
    ["a certa com sobra",  "segredo!"],
  ])("credencial %s → refused", (_n, got) => {
    expect(judgeTransportCredential("segredo", got)).toBe("refused")
  })

  it.each([undefined, ""])("env %s no servidor → not_configured, nunca ok — nem para quem manda vazio", (esperado) => {
    expect(judgeTransportCredential(esperado, "")).toBe("not_configured")
    expect(judgeTransportCredential(esperado, undefined)).toBe("not_configured")
    expect(judgeTransportCredential(esperado, "qualquer")).toBe("not_configured")
  })
})
