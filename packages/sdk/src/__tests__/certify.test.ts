/**
 * certify.test.ts
 * Testes da certificação de agente — spec 4.6e
 */

import { describe, it, expect } from "vitest"
import { certifyAgent }          from "../certify"
import { PlugHubAdapter }        from "../adapter"

const validAdapter = new PlugHubAdapter({
  context_map: { "customer_data.tier": "cliente.tier" },
  result_map:  { "outcome": "status", "issue_status": "issues" },
  outcome_map: { "ok": "resolved" },
})

const validHandler = async () => ({
  result: { status: "ok" },
  issues: [{ issue_id: "i1", description: "Atendimento concluído", status: "resolved" as const }],
})

describe("certifyAgent", () => {
  it("certifica agente válido com status passed", async () => {
    const report = await certifyAgent({
      agent_type_id: "agente_retencao_v1",
      adapter:       validAdapter,
      handler:       validHandler,
      pools:         ["retencao_humano"],
    })
    expect(report.status).toBe("passed")
    expect(report.agent_type_id).toBe("agente_retencao_v1")
    expect(report.checks.every(c => c.status !== "failed")).toBe(true)
  })

  it("falha certificação quando pools está vazio", async () => {
    const report = await certifyAgent({
      agent_type_id: "agente_retencao_v1",
      adapter:       validAdapter,
      handler:       validHandler,
      pools:         [],
    })
    expect(report.status).toBe("failed")
    const poolCheck = report.checks.find(c => c.name === "registration.pools_declared")
    expect(poolCheck?.status).toBe("failed")
  })

  it("falha certificação quando handler lança exceção", async () => {
    const report = await certifyAgent({
      agent_type_id: "agente_test_v1",
      adapter:       validAdapter,
      handler:       async () => { throw new Error("handler quebrado") },
      pools:         ["pool_test"],
    })
    expect(report.status).toBe("failed")
    const handlerCheck = report.checks.find(c => c.name === "handler.executes_without_error")
    expect(handlerCheck?.status).toBe("failed")
  })

  it("relatório tem certified_at no formato ISO", async () => {
    const report = await certifyAgent({
      agent_type_id: "agente_retencao_v1",
      adapter:       validAdapter,
      handler:       validHandler,
      pools:         ["pool_test"],
    })
    expect(() => new Date(report.certified_at)).not.toThrow()
  })
})
