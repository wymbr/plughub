/**
 * session-scope.test.ts — AUT-55: a sessão não esconde o pool do item que alguém
 * DETÉM.
 *
 * O caso medido (2026-09-12): tarefa delegada cuja sessão é do pool do WORKFLOW
 * (`formfill_demo_ia`) e cujo item está parqueado no pool HUMANO
 * (`formfill_demo`). O operador alcança o humano, o portão olhava só os dois
 * primeiros, e o formulário não abria para quem detinha a tarefa — enquanto o
 * submit do mesmo operador era autorizado pela posse (A5).
 *
 * ⚠️ O caso que decide NÃO é "soma o pool do item". É o de baixo: **isto não
 * alarga o alcance de ninguém** — quem não alcança o pool do item continua
 * recusado. A fonte nova diz de quem a SESSÃO é, nunca o que o chamador pode.
 */

import { describe, it, expect } from "vitest"
import { poolsDaSessao, type ScopeRedis } from "../lib/session-scope"

const T = "tenant_demo"
const S = "sess-1"

function fakeRedis(over: { ctxPool?: string; ledgerPool?: string; quebrado?: boolean } = {}): ScopeRedis {
  return {
    async hget(chave: string, campo: string) {
      if (over.quebrado) throw new Error("redis fora")
      if (chave === `${T}:ctx:${S}` && campo === "core.pool.id" && over.ctxPool) {
        return JSON.stringify({ value: over.ctxPool })
      }
      return null
    },
    async get(chave: string) {
      if (over.quebrado) throw new Error("redis fora")
      if (chave === `${T}:work_task:${S}` && over.ledgerPool) {
        return JSON.stringify({ pool_id: over.ledgerPool, step_id: "coletar" })
      }
      return null
    },
  }
}

describe("poolsDaSessao — as três fontes", () => {
  it("⚠️ o caso AUT-55: o pool do ITEM parqueado entra, e é o que o operador alcança", async () => {
    const pools = await poolsDaSessao(
      fakeRedis({ ctxPool: "formfill_demo_ia", ledgerPool: "formfill_demo" }), T, S, "",
    )
    expect(pools).toContain("formfill_demo")     // onde o item foi reivindicado
    expect(pools).toContain("formfill_demo_ia")  // o pool do workflow
  })

  it("soma o pool que ATENDE, quando o meta já foi escrito", async () => {
    const pools = await poolsDaSessao(fakeRedis({ ctxPool: "sac_ia" }), T, S, "fila_humano")
    expect(pools).toEqual(expect.arrayContaining(["fila_humano", "sac_ia"]))
  })

  it("contato SEM item parqueado não muda — o ledger ausente é o caminho normal", async () => {
    const pools = await poolsDaSessao(fakeRedis({ ctxPool: "sac_ia" }), T, S, "")
    expect(pools).toEqual(["sac_ia"])
  })

  it("não repete pool quando as fontes concordam", async () => {
    const pools = await poolsDaSessao(
      fakeRedis({ ctxPool: "formfill_demo", ledgerPool: "formfill_demo" }), T, S, "formfill_demo",
    )
    expect(pools).toEqual(["formfill_demo"])
  })

  it("sessão sem fonte nenhuma devolve VAZIO — indeterminável, e quem chama decide", async () => {
    expect(await poolsDaSessao(fakeRedis(), T, S, "")).toEqual([])
  })

  it("sem tenant, só o pool que atende — nada a consultar", async () => {
    expect(await poolsDaSessao(fakeRedis({ ledgerPool: "x" }), "", S, "fila_humano")).toEqual(["fila_humano"])
  })

  it("Redis quebrado não estoura o portão: devolve o que sabe", async () => {
    // Degradar para VAZIO aqui vira 403 nomeado no chamador — recusa, nunca
    // liberação. O que não pode é a exceção subir e derrubar a rota.
    expect(await poolsDaSessao(fakeRedis({ quebrado: true }), T, S, "fila_humano")).toEqual(["fila_humano"])
  })

  it("ledger ilegível é ignorado, não inventa pool", async () => {
    const redis: ScopeRedis = {
      async hget() { return null },
      async get() { return "{nao e json" },
    }
    expect(await poolsDaSessao(redis, T, S, "")).toEqual([])
  })
})

describe("o que a fonte nova NÃO é", () => {
  it("⚠️ não alarga alcance: quem não tem o pool do item continua fora", async () => {
    // O portão faz `daSessao ∩ accessible_pools`. Simulamos o chamador de OUTRO
    // escopo: a fonte nova acrescenta pools À SESSÃO, nunca ao chamador.
    const daSessao = await poolsDaSessao(
      fakeRedis({ ctxPool: "formfill_demo_ia", ledgerPool: "formfill_demo" }), T, S, "",
    )
    const alcanceDeOutro = ["limite_ia", "limite_retorno"]
    expect(daSessao.some((p) => alcanceDeOutro.includes(p))).toBe(false)
  })
})
