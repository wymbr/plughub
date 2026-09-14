/**
 * arrival-evidence.test.ts — PID-09 (2026-09-14)
 *
 * O juiz do pedido do channel-gateway: o que uma CHEGADA pode afirmar, e o que o servidor
 * carimba sozinho. Cada recusa com o controle que passa.
 */
import { describe, it, expect } from "vitest"
import { judgeArrivalEvidence } from "../lib/arrival-evidence"

const AGORA = "2026-09-14T12:00:00.000Z"
const VERIFICADA = {
  tenant_id: "t", session_id: "S1", mechanism: "whatsapp", status: "verified",
  customer_id: "cus_a", source: "authoritative", anchor_kind: "phone",
}

describe("PID-09 — judgeArrivalEvidence", () => {
  it("controle: chegada verificada do telefone autoritativo — relógio e sessão da prova são do servidor", () => {
    const j = judgeArrivalEvidence({ ...VERIFICADA, verified_at: "1999-01-01T00:00:00Z", proven_in_session: "OUTRA" }, AGORA)
    expect(j).toEqual({
      kind: "ok", tenantId: "t", sessionId: "S1", mechanism: "whatsapp",
      record: { status: "verified", anchor_kind: "phone", customer_id: "cus_a", source: "authoritative", verified_at: AGORA, proven_in_session: "S1" },
    })
  })

  it("`otp` não é registrável por chegada — só a tool que confere o código o prova", () => {
    expect(judgeArrivalEvidence({ ...VERIFICADA, mechanism: "otp" }, AGORA)).toMatchObject({ kind: "refuse", status: 422, error: "mechanism_not_arrival" })
  })

  it("verified sem cliente, ou com fonte que não é o cadastro autoritativo, é recusado", () => {
    const { customer_id: _, ...semCliente } = VERIFICADA
    expect(judgeArrivalEvidence(semCliente, AGORA)).toMatchObject({ kind: "refuse", error: "verified_sem_cliente" })
    for (const source of ["declared", "channel_origin", "", undefined]) {
      expect(judgeArrivalEvidence({ ...VERIFICADA, source }, AGORA)).toMatchObject({ kind: "refuse", error: "verified_sem_fonte_autoritativa" })
    }
  })

  it("failed (número sem dono autoritativo) grava só o status — sem cliente, sem prova", () => {
    const j = judgeArrivalEvidence({ tenant_id: "t", session_id: "S1", mechanism: "whatsapp", status: "failed", customer_id: "cus_a", source: "authoritative" }, AGORA)
    expect(j).toEqual({ kind: "ok", tenantId: "t", sessionId: "S1", mechanism: "whatsapp", record: { status: "failed" } })
  })

  it("forma: sem tenant/sessão 400; status desconhecido 422", () => {
    expect(judgeArrivalEvidence({ ...VERIFICADA, session_id: "" }, AGORA)).toMatchObject({ kind: "refuse", status: 400 })
    expect(judgeArrivalEvidence(undefined, AGORA)).toMatchObject({ kind: "refuse", status: 400 })
    expect(judgeArrivalEvidence({ ...VERIFICADA, status: "ok" }, AGORA)).toMatchObject({ kind: "refuse", status: 422, error: "status_invalido" })
  })
})
