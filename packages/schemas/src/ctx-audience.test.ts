/**
 * ctx-audience.test.ts — a derivação de plateia e a política por plateia.
 *
 * É a lógica que o censo estático (CTX-01) e o runtime (CTX-02) compartilham.
 * Se ela estiver errada, os dois erram JUNTOS e concordando — que é o pior
 * desfecho possível, porque a concordância parece confirmação.
 */
import { describe, expect, it } from "vitest"
import {
  deriveAudience, resolveMaskForAudience, maskForSite, maskChangesValue,
  flattenContextMap,
} from "./ctx-audience"
import { DEFAULT_DATA_TYPE_CATALOG } from "./audit"

describe("deriveAudience — a plateia vem do SÍTIO", () => {
  it.each([
    ["notify", "all",          "customer"],
    ["notify", undefined,      "customer"],
    ["menu",   "all",          "customer"],
    ["menu",   "agents_only",  "operator"],
    ["invoke", undefined,      "system"],
    ["reason", undefined,      "model"],
    ["choice", undefined,      "none"],
    ["complete", undefined,    "none"],
  ])("%s + visibility=%s → %s", (tipo, vis, esperado) => {
    expect(deriveAudience(tipo, vis)).toBe(esperado)
  })

  it("visibility AUSENTE é cliente, não 'não sei'", () => {
    // O default do produto é `all`. Ler `undefined` como indeterminado faria a
    // MAIORIA dos sítios escapar do filtro justamente por não terem declarado
    // nada — o permissivo vencendo por omissão, que é o padrão que este arco
    // existe para quebrar.
    expect(deriveAudience("notify")).toBe("customer")
  })

  it("array de participantes RECUSA ALTO — trata como cliente", () => {
    // Sem resolver os ids não dá para saber se o cliente está dentro, e ele
    // frequentemente está (o NPS dirige a pergunta ao participante-cliente).
    // Assumir "operador" faria esse caso exato escapar.
    expect(deriveAudience("notify", ["part_abc"])).toBe("customer")
  })

  it("ref não resolvida também recusa alto", () => {
    expect(deriveAudience("menu", "$.pipeline_state.quem")).toBe("customer")
  })
})

describe("resolveMaskForAudience — gêmeo fiel do resolvedor canônico", () => {
  const tipo = (id: string) => DEFAULT_DATA_TYPE_CATALOG.types.find(x => x.id === id)

  it("audiência DECLARADA ganha a máscara dela", () => {
    expect(resolveMaskForAudience(tipo("credit_card"), "operator")).toBe("last_4")
    expect(resolveMaskForAudience(tipo("email_addr"), "operator")).toBe("email_domain")
  })

  it("audiência NÃO declarada cai na do `operator` — e é assim que `customer` funciona hoje", () => {
    // Medido: `customer` não é declarado em NENHUM dos 14 tipos. Este ramo é o
    // que faz o `_build_pending_preview` produzir `***4444` desde 2026-09-02,
    // e é a resposta para a tela 3 do dono — política nova, nenhuma.
    expect(resolveMaskForAudience(tipo("credit_card"), "customer")).toBe("last_4")
  })

  it("`by_role` VAZIO é ABERTO declarado, não omissão", () => {
    // Tipo de FINALIDADE. Cair no `operator` aqui esconderia do cliente o que
    // ele mesmo declarou.
    const finalidade = DEFAULT_DATA_TYPE_CATALOG.types.filter(
      x => Object.keys(x.mascara?.by_role ?? {}).length === 0)
    expect(finalidade.length).toBeGreaterThan(0)   // testemunha de presença
    for (const f of finalidade) {
      expect(resolveMaskForAudience(f, "customer")).toBe("plain")
    }
  })

  it("tipo fora do catálogo RECUSA ALTO — `full`, nunca `plain`", () => {
    expect(resolveMaskForAudience(undefined, "customer")).toBe("full")
  })
})

describe("maskForSite — o sítio já virou plateia; aqui o tipo decide quanto ela vê", () => {
  it("para o SISTEMA o valor sai inteiro — o CRM precisa dele", () => {
    // Controle positivo do arco inteiro: sem este caso, um filtro que bloqueia
    // tudo passaria em qualquer teste que só verifique bloqueio.
    expect(maskForSite("credit_card", "system")).toBe("plain")
    expect(maskChangesValue(maskForSite("credit_card", "system"))).toBe(false)
  })

  it("ao CLIENTE o cartão sai `last_4` — o mesmo que a tela 2 já mostrava", () => {
    expect(maskForSite("credit_card", "customer")).toBe("last_4")
    expect(maskChangesValue(maskForSite("credit_card", "customer"))).toBe(true)
  })

  it("`model` é `undecided`, e isso NÃO é sinônimo de `plain`", () => {
    // §D5. Chamar de `plain` transformaria uma decisão pendente numa permissão.
    expect(maskForSite("cpf", "model")).toBe("undecided")
    expect(maskForSite("cpf", "model")).not.toBe("plain")
    // E `undecided` não autoriza aplicação nenhuma na F3.
    expect(maskChangesValue(maskForSite("cpf", "model"))).toBe(false)
  })

  it("sítio que não renderiza não tem o que filtrar", () => {
    expect(maskForSite("credit_card", "none")).toBe("plain")
  })

  it("tag fora do mapa é `unknown` — CONTADA, não decidida (§D4)", () => {
    // Este ADR não decide o default do desconhecido; isso é a V4 da allowlist.
    // Devolver `plain` afirmaria uma permissão que ninguém escreveu; devolver
    // `full` decidiria a V4 de esguelha.
    expect(maskForSite(undefined, "customer")).toBe("unknown")
    expect(maskChangesValue(maskForSite(undefined, "customer"))).toBe(false)
  })

  it("tipo no mapa mas fora do catálogo é outro fato, e recusa alto", () => {
    // Não é tag indeclarada (§D4) — é config inconsistente, e aí o gêmeo manda.
    expect(maskForSite("tipo_que_nao_existe", "customer")).toBe("full")
  })
})

describe("flattenContextMap — os ALIASES são o que importa", () => {
  const mapa = {
    contexto: {
      session: {
        cartao: { numero: { tipo: "credit_card", legado: ["session.numero_cartao"] } },
      },
    },
  }

  it("indexa a canônica sem a raiz do documento", () => {
    expect(flattenContextMap(mapa).get("session.cartao.numero")).toBe("credit_card")
  })

  it("indexa o alias legado — e é ELE que os flows interpolam", () => {
    // Um índice só de canônicas resolveria ZERO das interpolações vivas e
    // concluiria que não há nada a filtrar. Foi medido: a tag que o flow lê é
    // `session.numero_cartao`, e a que carrega o tipo é `session.cartao.numero`.
    expect(flattenContextMap(mapa).get("session.numero_cartao")).toBe("credit_card")
  })

  it("o mapa REAL resolve a tag que o defeito usa", () => {
    // Testemunha de presença sobre o catálogo embutido, não sobre um fixture.
    const real = flattenContextMap({
      contexto: { session: { cartao: { numero: { tipo: "credit_card", legado: ["session.numero_cartao"] } } } },
    })
    expect(maskForSite(real.get("session.numero_cartao"), "customer",
      DEFAULT_DATA_TYPE_CATALOG)).toBe("last_4")
  })
})
