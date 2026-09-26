/**
 * agent-text-masking.test.ts — MSK-07: a casa ÚNICA do mascaramento de texto de agente.
 *
 * O texto do atendente humano (WS do Console, `server.ts`) ia CRU ao stream, ao ClickHouse e
 * ao cliente, porque a máscara da MSK-04 morava dentro da tool `message_send`. Ela saiu de lá
 * para `maskMessageContent`, e os dois caminhos a chamam. Estes testes cobrem a função e a
 * forma que o CLIENTE recebe (`tokensToDisplay`); o handler do WS é medido ao vivo.
 *
 * Pares: mascara ↔ texto limpo intacto; token no destino de agente ↔ exibição sem envelope no
 * destino do cliente; catálogo do tenant ↔ catálogo indisponível.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest"
import RedisMock from "ioredis-mock"
import { DEFAULT_DATA_TYPE_CATALOG } from "@plughub/schemas"
import { MaskingService, maskMessageContent, tokensToDisplay } from "../lib/masking"

const FONE = "(11) 98765-4321"
const texto = (t: string) => ({ type: "text" as const, text: t, metadata: {} })

describe("maskMessageContent — texto de agente", () => {
  let redis: InstanceType<typeof RedisMock>
  beforeEach(async () => { redis = new RedisMock(); await redis.flushall() })
  afterEach(() => vi.restoreAllMocks())

  it("dado detectado vira TOKEN com a exibição do catálogo, e o original fica à parte", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("sem config-api"))
    vi.spyOn(console, "warn").mockImplementation(() => {})
    const r = await maskMessageContent(redis as never, "tenant_msk07_a", texto(`fone ${FONE}`), "t")
    expect(r.masked).toBe(true)
    expect(r.maskedCategories).toEqual(["phone"])
    expect(r.finalContent.text).toMatch(/^fone \[phone:tk_[a-f0-9]+:\*\*\*4321\]$/)
    expect(r.originalContent?.text).toBe(`fone ${FONE}`)
  })

  it("CONTROLE: texto limpo sai intacto, sem original e sem marca", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("sem config-api"))
    vi.spyOn(console, "warn").mockImplementation(() => {})
    const r = await maskMessageContent(redis as never, "tenant_msk07_b", texto("vou verificar"), "t")
    expect(r).toEqual({ finalContent: texto("vou verificar"), masked: false, maskedCategories: [] })
  })

  it("o display segue o by_role do catálogo DO TENANT", async () => {
    const catalogo = {
      ...DEFAULT_DATA_TYPE_CATALOG,
      types: DEFAULT_DATA_TYPE_CATALOG.types.map(t =>
        t.id === "phone" ? { ...t, mascara: { ...t.mascara, by_role: { operator: "full" } } } : t),
    }
    vi.spyOn(globalThis, "fetch").mockImplementation((async () =>
      new Response(JSON.stringify({ value: catalogo }), { status: 200 })) as never)
    const r = await maskMessageContent(redis as never, "tenant_msk07_c", texto(FONE), "t")
    expect(r.finalContent.text).toMatch(/^\[phone:tk_[a-f0-9]+:\*\*\*\]$/)
  })

  it("falha inesperada degrada para a rede pura — nunca para o original — e LOGA", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("sem config-api"))
    vi.spyOn(console, "warn").mockImplementation(() => {})
    vi.spyOn(MaskingService, "applyMasking").mockRejectedValue(new Error("boom"))
    const err = vi.spyOn(console, "error").mockImplementation(() => {})
    const r = await maskMessageContent(redis as never, "tenant_msk07_d", texto(FONE), "ctx-x", "[agent-ws]")
    expect(r.finalContent.text).toBe("***4321")
    expect(r.originalContent).toBeUndefined()
    const log = err.mock.calls.map(c => String(c[0])).join("\n")
    expect(log).toContain("[agent-ws] mascaramento FALHOU ctx-x")
  })
})

describe("tokensToDisplay — o que o CLIENTE recebe", () => {
  it("tira o envelope e deixa a exibição", () => {
    expect(tokensToDisplay("fone [phone:tk_ab12:***4321] e [email_addr:tk_9f:j***@x.com]"))
      .toBe("fone ***4321 e j***@x.com")
  })

  it("CONTROLE: texto sem token sai igual (colchete comum não é token)", () => {
    expect(tokensToDisplay("[Seleção: 2] ok")).toBe("[Seleção: 2] ok")
  })
})
