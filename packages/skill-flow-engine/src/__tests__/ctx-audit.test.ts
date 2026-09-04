/**
 * ctx-audit.test.ts — CTX-04 (F3): o filtro por plateia APLICA, e cala quando deve.
 *
 * ⚠️ **Por que este teste existe, e não uma prova ao vivo.** A tentativa de provar
 * rodando `smoke_limite_tres_acessos` saiu MUDA — e corretamente: aquele smoke só
 * exercita `skill_limite_processo_v1`, que tem zero steps `notify`/`menu`. Um log
 * vazio ali é consistente com o censo, não evidência de que o filtro funciona.
 * *"Não achou" e "não rodou" produzem o mesmo silêncio*, e é essa confusão que o
 * arco inteiro combate.
 *
 * ── O caso que carrega o peso é o CONTROLE POSITIVO ──────────────────────────
 * Um filtro que mascara TUDO passa em qualquer teste que só verifique mascaramento.
 * Por isso a mesma tag, indo ao SISTEMA, tem de sair INTEIRA — e é o `invoke` que
 * manda o número ao CRM.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest"

const MAPA = {
  value: { contexto: { session: {
    cartao: { numero: { tipo: "credit_card", legado: ["session.numero_cartao"] } },
    limite: { aprovado: { tipo: "valor_informado_ao_cliente", legado: ["session.limite_aprovado"] } },
    segredo: { pin: { tipo: "credential", legado: ["session.pin"] } },
  } } },
}
const TIPOS = {
  value: { types: [
    // `by_role` só com `operator` é o REGIME VIVO: `customer` não é declarado em
    // nenhum dos 15 tipos, e é o teto do agente que responde por ele (§D9.1).
    { id: "credit_card", formato: {}, lgpd: "financeiro",
      mascara: { by_role: { operator: "last_4" } } },
    // Tipo de FINALIDADE — `by_role` vazio é ABERTO declarado, não omissão.
    { id: "valor_informado_ao_cliente", formato: {}, lgpd: "financeiro",
      mascara: { by_role: {} } },
    { id: "credential", formato: {}, lgpd: "credencial",
      mascara: { by_role: { operator: "hidden" } } },
  ] },
}

function stubFetch(): void {
  vi.stubGlobal("fetch", vi.fn(async (u: string) => ({
    ok: true,
    json: async () => (String(u).includes("context_map") ? MAPA : TIPOS),
  })))
}

const espiaWarn = () => vi.spyOn(console, "warn").mockImplementation(() => {})
const espiaInfo = () => vi.spyOn(console, "info").mockImplementation(() => {})

async function carrega() {
  vi.resetModules()
  return import("../ctx-audit")
}

const CLIENTE = { stepType: "notify", visibility: "all",         stepId: "notificar_aprovado" }
const OPERADOR = { stepType: "notify", visibility: "agents_only", stepId: "nota_interna" }
const SISTEMA  = { stepType: "invoke",                            stepId: "chamar_crm" }
const MODELO   = { stepType: "reason",                            stepId: "avaliar" }

describe("filtrarLeituraCtx — o leitor SUBSTITUI o valor", () => {
  let warn: ReturnType<typeof espiaWarn>
  let info: ReturnType<typeof espiaInfo>

  beforeEach(() => { warn = espiaWarn(); info = espiaInfo(); stubFetch(); vi.stubEnv("CONFIG_API_URL", "http://config") })
  afterEach(() => { warn.mockRestore(); info.mockRestore(); vi.unstubAllGlobals(); vi.unstubAllEnvs() })

  it("ao CLIENTE o cartão sai mascarado — e é o mesmo `***4444` da outra tela", async () => {
    const { filtrarLeituraCtx } = await carrega()
    const fora = await filtrarLeituraCtx("1111222233334444", "session.numero_cartao", CLIENTE, "t1")
    expect(fora).toBe("***4444")
    expect(info.mock.calls.flat().join(" ")).toContain("APLICADO")
  })

  it("ao SISTEMA o MESMO valor sai INTEIRO — controle positivo do arco", async () => {
    // Sem este caso, um filtro que bloqueia tudo passaria em todos os outros.
    const { filtrarLeituraCtx } = await carrega()
    expect(await filtrarLeituraCtx("1111222233334444", "session.numero_cartao", SISTEMA, "t1"))
      .toBe("1111222233334444")
  })

  it("ao OPERADOR também mascara — e hoje com a MESMA máscara do cliente", async () => {
    // Não é redundância: as duas plateias recebem `last_4` porque `customer` não é
    // declarado e cai no teto do operador. Registrar isso é o que fará alguém notar
    // quando o eixo `customer` for preenchido e as duas passarem a divergir.
    const { filtrarLeituraCtx } = await carrega()
    expect(await filtrarLeituraCtx("1111222233334444", "session.numero_cartao", OPERADOR, "t1"))
      .toBe("***4444")
  })

  it("tipo de FINALIDADE sai INTEIRO ao cliente — `by_role: {}` é aberto declarado", async () => {
    // O limite aprovado é o que a mensagem existe para anunciar (§D9.2). Se este
    // caso virar `R$ ****,**`, a F3 quebrou o produto que ela deveria proteger.
    const { filtrarLeituraCtx } = await carrega()
    expect(await filtrarLeituraCtx("5000", "session.limite_aprovado", CLIENTE, "t1")).toBe("5000")
  })

  it("`hidden` devolve VAZIO — o sinal de omitir o campo", async () => {
    const { filtrarLeituraCtx } = await carrega()
    expect(await filtrarLeituraCtx("1234", "session.pin", CLIENTE, "t1")).toBe("")
  })

  it("plateia `model` NÃO é aplicada, e o motivo é logado (§D5)", async () => {
    const { filtrarLeituraCtx } = await carrega()
    expect(await filtrarLeituraCtx("1111222233334444", "session.numero_cartao", MODELO, "t1"))
      .toBe("1111222233334444")
    const txt = warn.mock.calls.flat().join(" ")
    expect(txt).toContain("NÃO aplicado")
    expect(txt).toContain("F4")
  })

  it("tag FORA do mapa não é aplicada, e o motivo é a V4 da allowlist (§D4)", async () => {
    const { filtrarLeituraCtx } = await carrega()
    expect(await filtrarLeituraCtx("valor", "session.nao_declarada", CLIENTE, "t1")).toBe("valor")
    expect(warn.mock.calls.flat().join(" ")).toContain("V4")
  })

  it("não repete a mesma combinação — o volume esconderia o achado", async () => {
    const { filtrarLeituraCtx, estadoAuditoriaCtx } = await carrega()
    for (let i = 0; i < 3; i++) {
      await filtrarLeituraCtx("1111222233334444", "session.numero_cartao", CLIENTE, "t1")
    }
    expect(info.mock.calls.filter(c => String(c[0]).includes("APLICADO")).length).toBe(1)
    expect(estadoAuditoriaCtx().achados).toBe(1)
  })

  it("valor ausente atravessa sem tocar no catálogo", async () => {
    const { filtrarLeituraCtx } = await carrega()
    expect(await filtrarLeituraCtx(undefined, "session.numero_cartao", CLIENTE, "t1")).toBeUndefined()
    expect(await filtrarLeituraCtx("", "session.numero_cartao", CLIENTE, "t1")).toBe("")
  })
})

describe("degradação — o valor passa CRU, e isso nunca é silencioso", () => {
  let warn: ReturnType<typeof espiaWarn>
  beforeEach(() => { warn = espiaWarn() })
  afterEach(() => { warn.mockRestore(); vi.unstubAllGlobals(); vi.unstubAllEnvs() })

  it("sem CONFIG_API_URL o valor sai INTEIRO, e o aviso NOMEIA o que caiu", async () => {
    // Mascarar tudo faria toda mensagem do parque virar `***` por uma queda de
    // config. Sem catálogo toda tag é `unknown`, e `unknown` já está decidido como
    // contar-e-não-aplicar (§D4) — o que não pode é o silêncio.
    vi.stubEnv("CONFIG_API_URL", "")
    const { filtrarLeituraCtx } = await carrega()
    expect(await filtrarLeituraCtx("1111222233334444", "session.numero_cartao", CLIENTE, "t1"))
      .toBe("1111222233334444")
    const txt = warn.mock.calls.flat().join(" ")
    expect(txt).toContain("CONFIG_API_URL")
    expect(txt.toLowerCase()).toContain("plateia")
  })

  it("catálogo que responde erro também deixa passar, avisando", async () => {
    vi.stubEnv("CONFIG_API_URL", "http://config")
    vi.stubGlobal("fetch", vi.fn(async () => ({ ok: false, status: 503, json: async () => ({}) })))
    const { filtrarLeituraCtx } = await carrega()
    expect(await filtrarLeituraCtx("1111222233334444", "session.numero_cartao", CLIENTE, "t1"))
      .toBe("1111222233334444")
    expect(warn.mock.calls.flat().join(" ")).toContain("indisponíveis")
  })
})
