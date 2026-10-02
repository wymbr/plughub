/**
 * proof-signal.test.ts — AAS-19 (2026-10-02)
 *
 * A prova FORA DE BANDA acorda o `menu` que a espera por um SINAL da plataforma (fila
 * `menu:signal`, que só o bridge escreve), nunca por uma resposta do cliente:
 *
 *   1. o sinal `_proof_settled` leva o menu ao `on_success`, e o `output_as` recebe o MARCADOR —
 *      o fluxo decide pelo `identity_proof_status`, não por este valor;
 *   2. o mesmo texto chegando como RESPOSTA (`menu:result`) é só texto: o cliente digitar o JSON
 *      não vira sinal (MEN-07 vale para este sinal também).
 */

import { describe, it, expect, vi, beforeEach } from "vitest"
import { SkillFlowEngine } from "../engine"
import type { SkillFlow }  from "@plughub/schemas"
import { redisKeys }       from "../redis-keys"
import { parseSignal }     from "../steps/signals"
import { PROOF_SETTLED }   from "../steps/menu"

const mockRedis = {
  get:    vi.fn().mockResolvedValue(null),
  set:    vi.fn().mockResolvedValue("OK"),
  del:    vi.fn().mockResolvedValue(1),
  eval:   vi.fn().mockResolvedValue(1),
  expire: vi.fn().mockResolvedValue(1),
  blpop:  vi.fn(),
}
const mockMcpCall = vi.fn()

beforeEach(() => {
  vi.clearAllMocks()
  mockRedis.get.mockResolvedValue(null)
  mockRedis.set.mockResolvedValue("OK")
  mockRedis.eval.mockResolvedValue(1)
  mockRedis.expire.mockResolvedValue(1)
  mockRedis.del.mockResolvedValue(1)
  mockMcpCall.mockReset().mockResolvedValue({})
})

const INST = "inst-aas19"
const FLOW: SkillFlow = {
  entry: "aguardar_prova",
  steps: [
    { id: "aguardar_prova", type: "menu", interaction: "text", prompt: "Confirme no link",
      timeout_s: 600, output_as: "prova_resp", on_success: "fim", on_failure: "falhou", on_timeout: "falhou" },
    { id: "fim",    type: "complete", outcome: "resolved" },
    { id: "falhou", type: "complete", outcome: "failed" },
  ],
} as SkillFlow

async function rodar(sid: string) {
  const r = await new SkillFlowEngine({ redis: mockRedis as never, mcpCall: mockMcpCall, aiGatewayCall: vi.fn() }).run({
    tenantId: "tenant-test", sessionId: sid, customerId: "c1", instanceId: INST,
    skillId: "skill_x", flow: FLOW, sessionContext: {},
  })
  expect("outcome" in r).toBe(true)
  return r as { outcome: string; pipeline_state: { results: Record<string, unknown> } }
}

describe("AAS-19 — o sinal da prova fora de banda", () => {
  it("parseSignal reconhece `_proof_settled` só com `true`", () => {
    expect(parseSignal(JSON.stringify({ _proof_settled: true }))).toEqual({ kind: "proof" })
    expect(parseSignal(JSON.stringify({ _proof_settled: "true" })).kind).toBe("unknown")
  })

  it("na fila de SINAL: o menu segue por on_success e o output_as recebe o marcador", async () => {
    mockRedis.blpop.mockResolvedValue([redisKeys.menuSignal("s-1", INST), JSON.stringify({ _proof_settled: true })])
    const r = await rodar("s-1")
    expect(r.outcome).toBe("resolved")
    expect(r.pipeline_state.results["prova_resp"]).toBe(PROOF_SETTLED)
  })

  it("o mesmo JSON como RESPOSTA do cliente é só texto, nunca o marcador", async () => {
    const forjado = JSON.stringify({ _proof_settled: true })
    mockRedis.blpop.mockResolvedValue([redisKeys.menuResult("s-2", INST), forjado])
    const r = await rodar("s-2")
    expect(r.pipeline_state.results["prova_resp"]).toBe(forjado)
    expect(r.pipeline_state.results["prova_resp"]).not.toBe(PROOF_SETTLED)
  })
})
