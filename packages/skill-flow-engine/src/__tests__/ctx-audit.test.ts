/**
 * ctx-audit.test.ts — CTX-02: a auditoria de plateia dispara, e cala quando deve.
 *
 * ⚠️ **Por que este teste existe, e não uma prova ao vivo.** A tentativa de
 * provar rodando `smoke_limite_tres_acessos` saiu MUDA — e corretamente: aquele
 * smoke só exercita `skill_limite_processo_v1`, que tem zero steps
 * `notify`/`menu`. Um log vazio ali é consistente com o censo, não evidência de
 * que a auditoria funciona. *"Não achou" e "não rodou" produzem o mesmo
 * silêncio*, e é justamente essa confusão que este arco combate.
 *
 * Os dois defeitos vivos estão em `skill_limite_retorno_v1`, cujo `notify` só
 * dispara depois de uma aprovação chegar ao cliente. A confirmação ao vivo fica
 * dependendo de um contato real; a garantia re-executável é esta.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"
import type { StepContext } from "../executor"

const MAPA = {
  value: { contexto: { session: { cartao: {
    numero: { tipo: "credit_card", legado: ["session.numero_cartao"] },
  } } } },
}
const TIPOS = {
  value: { types: [{
    id: "credit_card", formato: {}, lgpd: "financeiro",
    mascara: { display: { echo_to_customer: "none", echo_to_operator: "masked" } },
  }] },
}

function stubFetch(): void {
  vi.stubGlobal("fetch", vi.fn(async (u: string) => ({
    ok: true,
    json: async () => (String(u).includes("context_map") ? MAPA : TIPOS),
  })))
}

const ctx = { tenantId: "t1", sessionId: "s1" } as unknown as StepContext

async function carrega() {
  vi.resetModules()
  return import("../ctx-audit")
}

describe("auditarLeituraCtx", () => {
  let warn: ReturnType<typeof vi.spyOn>

  beforeEach(() => { warn = vi.spyOn(console, "warn").mockImplementation(() => {}) })
  afterEach(() => { warn.mockRestore(); vi.unstubAllGlobals(); vi.unstubAllEnvs() })

  it("AVISA quando o valor não poderia ecoar ao cliente", async () => {
    vi.stubEnv("CONFIG_API_URL", "http://config")
    stubFetch()
    const { auditarLeituraCtx } = await carrega()
    await auditarLeituraCtx("session.numero_cartao",
      { stepType: "notify", visibility: "all", stepId: "notificar_aprovado" }, "t1")
    const txt = warn.mock.calls.flat().join(" ")
    expect(txt).toContain("AUDITORIA (não aplicado)")
    expect(txt).toContain("credit_card")
    expect(txt).toContain("plateia=customer")
    expect(txt).toContain("política=none")
  })

  it("CALA quando a mesma tag vai ao SISTEMA — controle positivo", async () => {
    // Sem este caso o teste acima passaria por uma auditoria que avisa sempre,
    // e o arco inteiro seria um filtro que bloqueia tudo.
    vi.stubEnv("CONFIG_API_URL", "http://config")
    stubFetch()
    const { auditarLeituraCtx } = await carrega()
    await auditarLeituraCtx("session.numero_cartao",
      { stepType: "invoke", stepId: "chamar_crm" }, "t1")
    expect(warn.mock.calls.flat().join(" ")).not.toContain("AUDITORIA")
  })

  it("avisa MASKED quando a plateia é operador", async () => {
    vi.stubEnv("CONFIG_API_URL", "http://config")
    stubFetch()
    const { auditarLeituraCtx } = await carrega()
    await auditarLeituraCtx("session.numero_cartao",
      { stepType: "notify", visibility: "agents_only", stepId: "nota_interna" }, "t1")
    expect(warn.mock.calls.flat().join(" ")).toContain("política=masked")
  })

  it("não repete a mesma combinação — o volume esconderia o achado", async () => {
    vi.stubEnv("CONFIG_API_URL", "http://config")
    stubFetch()
    const { auditarLeituraCtx, estadoAuditoriaCtx } = await carrega()
    const sitio = { stepType: "notify", visibility: "all", stepId: "n1" }
    await auditarLeituraCtx("session.numero_cartao", sitio, "t1")
    await auditarLeituraCtx("session.numero_cartao", sitio, "t1")
    await auditarLeituraCtx("session.numero_cartao", sitio, "t1")
    expect(warn.mock.calls.filter(c => String(c[0]).includes("AUDITORIA")).length).toBe(1)
    expect(estadoAuditoriaCtx().achados).toBe(1)
  })

  it("sem CONFIG_API_URL, a degradação NOMEIA o que deixa de valer", async () => {
    // A frase genérica "using default values" é a que ninguém leu por meses no
    // bridge, segundo o próprio CLAUDE.md. Aqui o aviso precisa dizer QUAL
    // capacidade caiu, e que vazio ≠ zero achados.
    vi.stubEnv("CONFIG_API_URL", "")
    const { auditarLeituraCtx, estadoAuditoriaCtx } = await carrega()
    await auditarLeituraCtx("session.numero_cartao",
      { stepType: "notify", visibility: "all" }, "t1")
    const txt = warn.mock.calls.flat().join(" ")
    expect(txt).toContain("AUDITORIA DE PLATEIA não roda")
    expect(txt).toContain("VAZIO")
    expect(estadoAuditoriaCtx().configurado).toBe(false)
  })

  it("nunca lança — auditar é observação, não pode derrubar um turno", async () => {
    vi.stubEnv("CONFIG_API_URL", "http://config")
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("rede caiu") }))
    const { auditarLeituraCtx } = await carrega()
    await expect(auditarLeituraCtx("session.numero_cartao",
      { stepType: "notify", visibility: "all" }, "t1")).resolves.toBeUndefined()
  })
})

describe("interpolate passa o SÍTIO adiante", () => {
  it("um `notify` audita; um template sem @ctx. não", async () => {
    vi.stubEnv("CONFIG_API_URL", "http://config")
    stubFetch()
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {})
    vi.resetModules()
    const { interpolate } = await import("../interpolate")
    const store = {
      getValue: async () => "1111222233334444",
    } as unknown as Parameters<typeof interpolate>[2]

    await interpolate("Cartão: {{@ctx.session.numero_cartao}}", ctx, store,
      { stepType: "notify", visibility: "all", stepId: "notificar_aprovado" })
    // A auditoria é disparada sem `await` (não entra no caminho crítico do
    // turno), então o teste espera pelas TASKS em vez de contar yields.
    await new Promise(r => setTimeout(r, 20))
    expect(warn.mock.calls.flat().join(" ")).toContain("AUDITORIA (não aplicado)")
    warn.mockRestore()
    vi.unstubAllGlobals(); vi.unstubAllEnvs()
  })
})
