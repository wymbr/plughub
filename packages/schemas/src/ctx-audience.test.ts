/**
 * ctx-audience.test.ts — a derivação de plateia e a política por plateia.
 *
 * É a lógica que o censo estático (CTX-01) e o runtime (CTX-02) compartilham.
 * Se ela estiver errada, os dois erram JUNTOS e concordando — que é o pior
 * desfecho possível, porque a concordância parece confirmação.
 */
import { describe, expect, it } from "vitest"
import {
  deriveAudience, resolveEchoPolicy, flattenContextMap,
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

describe("resolveEchoPolicy — o TIPO decide, a plateia escolhe a coluna", () => {
  it("credit_card não ecoa ao cliente, mas sai mascarado ao operador", () => {
    expect(resolveEchoPolicy("credit_card", "customer")).toBe("none")
    expect(resolveEchoPolicy("credit_card", "operator")).toBe("masked")
  })

  it("para o SISTEMA o valor sai inteiro — o CRM precisa dele", () => {
    // Controle positivo do arco inteiro: sem este caso, um filtro que bloqueia
    // tudo passaria em qualquer teste que só verifique bloqueio.
    expect(resolveEchoPolicy("credit_card", "system")).toBe("plain")
  })

  it("`model` é `undecided`, e isso NÃO é sinônimo de `plain`", () => {
    // §D5. Chamar de `plain` transformaria uma decisão pendente numa permissão.
    expect(resolveEchoPolicy("cpf", "model")).toBe("undecided")
    expect(resolveEchoPolicy("cpf", "model")).not.toBe("plain")
  })

  it("sítio que não renderiza não tem eco a filtrar", () => {
    expect(resolveEchoPolicy("credit_card", "none")).toBe("plain")
  })

  it("tipo ausente ou fora do catálogo é `unknown`, nunca `plain`", () => {
    // §D4 — este ADR não decide o default do desconhecido; ele o CONTA. Devolver
    // `plain` seria decidir, e afirmar uma permissão que ninguém escreveu.
    expect(resolveEchoPolicy(undefined, "customer")).toBe("unknown")
    expect(resolveEchoPolicy("tipo_que_nao_existe", "customer")).toBe("unknown")
  })

  it("tipo declarado SEM política de eco também é `unknown`", () => {
    const cat = { types: [{ id: "mudo", formato: {}, mascara: {}, lgpd: "none" }] }
    expect(resolveEchoPolicy("mudo", "customer", cat as never)).toBe("unknown")
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
    expect(resolveEchoPolicy(real.get("session.numero_cartao"), "customer",
      DEFAULT_DATA_TYPE_CATALOG)).toBe("none")
  })
})
