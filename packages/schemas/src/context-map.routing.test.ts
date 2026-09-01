/**
 * context-map.routing.test.ts — CNS-04: uma casa só para "onde esta tag mora".
 *
 * ── O defeito que esta fase fechou ───────────────────────────────────────────
 *
 * Havia DUAS listas respondendo à mesma pergunta e elas divergiam:
 *
 *   · `ContextScopeSchema` = ["session","journey","customer"] — o oráculo do mapa
 *     aprovava um root contra isto, e o comentário prometia que `customer` valia
 *     90 dias;
 *   · os prefixos do `sdk/context-store.ts` — que decidem de verdade o TTL e a
 *     chave, e nos quais **`customer.` não existe** (as rotas de 90 d são
 *     `insight.historico`, `pricing` e `core.customer.`).
 *
 * Consequência: um `contexto.customer.x` declarado no mapa passava no oráculo e a
 * tag `customer.x` caía no default — **4 horas**, onde a declaração prometia 90
 * dias. Armadilha ARMADA, dano zero só porque nenhum mapa vigente usava o root.
 *
 * O gate agora é a própria tabela: `resolveContextStore` é a única resposta, e o
 * oráculo confere COERÊNCIA (o root anuncia um store? então as tags dele têm de ir
 * para lá) em vez de conferir PERTENCIMENTO A UM ENUM — que era o teste da
 * proposição vizinha.
 */
import { describe, it, expect } from "vitest"
import {
  resolveContextStore,
  CONTEXT_ROUTE_PREFIXES,
  verifyContextMap,
  DEFAULT_CONTEXT_MAP,
  type ContextMap,
} from "./context-map"

describe("CNS-04 — resolveContextStore é a única casa do roteamento", () => {
  it("R-1: as rotas de 90 dias são as três declaradas", () => {
    expect(resolveContextStore("insight.historico.resumo")).toBe("customer")
    expect(resolveContextStore("pricing.plano")).toBe("customer")
    expect(resolveContextStore("core.customer.plano")).toBe("customer")
  })

  it("R-2: as rotas de 30 dias são as duas declaradas", () => {
    expect(resolveContextStore("journey.resultado")).toBe("journey")
    expect(resolveContextStore("core.journey.parecer")).toBe("journey")
  })

  it("R-3: o default é sessão, e vale para root de tenant — é o que a CNS-02 libera", () => {
    expect(resolveContextStore("session.cliente.nome")).toBe("session")
    expect(resolveContextStore("card.number")).toBe("session")
    expect(resolveContextStore("core.contact.close_origin")).toBe("session")
  })

  it("R-4: `customer.` NÃO roteia para o cliente — era a armadilha, e ela fica DECLARADA", () => {
    // Este teste documenta um fato contra-intuitivo de propósito: o nome do store e
    // o prefixo que roteia para ele NÃO são a mesma string. Se um dia alguém
    // acrescentar a rota `customer.`, este teste fica vermelho e obriga a decidir —
    // que é melhor que a rota nascer e o `mismatched_retention` deixar de acusar.
    expect(resolveContextStore("customer.qualquer_coisa")).toBe("session")
  })

  it("R-5: a ORDEM da tabela protege a rota da journey", () => {
    // `core.customer.` vem antes de `core.journey.` na tabela, mas as duas são
    // prefixos DISJUNTOS — a ordem só machuca se alguém encurtar uma delas para
    // `core.`. A bateria de mutação da CNS-03 mediu exatamente esse caso.
    const idxCustomer = CONTEXT_ROUTE_PREFIXES.findIndex(r => r.prefix === "core.customer.")
    const idxJourney = CONTEXT_ROUTE_PREFIXES.findIndex(r => r.prefix === "core.journey.")
    expect(idxCustomer).toBeGreaterThanOrEqual(0)
    expect(idxJourney).toBeGreaterThanOrEqual(0)
    expect(resolveContextStore("core.journey.x")).toBe("journey")
  })
})

describe("CNS-04 — o oráculo confere COERÊNCIA de retenção, não pertencimento a enum", () => {
  const leaf = { tipo: "texto" }

  it("O-1: root `core` é ACEITO — era o que o enum barrava", () => {
    const map: ContextMap = {
      ...DEFAULT_CONTEXT_MAP,
      contexto: { core: { contact: { close_origin: leaf } } },
    }
    expect(verifyContextMap(map).mismatched_retention).toEqual([])
  })

  it("O-2: root de TENANT é aceito — não anuncia store nenhum", () => {
    const map: ContextMap = {
      ...DEFAULT_CONTEXT_MAP,
      contexto: { card: { credito: { number: leaf } } },
    }
    expect(verifyContextMap(map).mismatched_retention).toEqual([])
  })

  it("O-3: root `customer` é ACUSADO — anuncia 90 d e roteia para sessão", () => {
    const map: ContextMap = {
      ...DEFAULT_CONTEXT_MAP,
      contexto: { customer: { perfil: { plano: leaf } } },
    }
    const r = verifyContextMap(map).mismatched_retention
    expect(r).toHaveLength(1)
    expect(r[0]).toMatchObject({ root: "customer", anuncia: "customer", roteia_para: "session" })
  })

  it("O-4 (testemunha): root `journey` é aceito — anuncia 30 d e roteia para lá", () => {
    const map: ContextMap = {
      ...DEFAULT_CONTEXT_MAP,
      contexto: { journey: { processo: { parecer: leaf } } },
    }
    expect(verifyContextMap(map).mismatched_retention).toEqual([])
  })

  it("O-5: o mapa VIGENTE não tem incoerência de retenção", () => {
    const v = verifyContextMap()
    expect(v.mismatched_retention).toEqual([])
    // Testemunha de PRESENÇA ao lado: zero sobre mapa vazio não é aprovação.
    expect(v.declared).toBeGreaterThan(50)
  })
})
